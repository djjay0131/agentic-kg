#!/usr/bin/env python3
"""Render a ``gcloud run jobs describe --format=json`` job as a safe summary.

``deploy-master`` and ``gcp-diagnose`` printed the experiment Job definition
from ``--format='value(template.template...)'`` and every field came out
blank (#171). ``gcloud run jobs`` uses the Cloud Run **v1** representation
(``spec.template.spec``); the v2 API / Terraform shape is
``template.template``. This formatter accepts either, so a gcloud version
change cannot silently print an empty definition again.

Environment **values** are never printed, only names. The service-account
email is printed because it is not a secret and is the key fact when the
runner cannot read the uploaded script.

Usage:
    gcloud run jobs describe <job> --format=json | python3 scripts/format_run_job.py
    gcloud run jobs describe <job> --format=json | python3 scripts/format_run_job.py --field image
"""

from __future__ import annotations

import argparse
import json
import sys

# gcloud's v1 **Job** has two template levels: the ExecutionTemplate under
# ``spec.template.spec`` (parallelism/taskCount) wraps the RevisionTemplate at
# ``spec.template.spec.template.spec`` (containers/serviceAccount/timeout).
# An Execution or a Service has only ``spec.template.spec``. The v2 API/Terraform
# shape is ``template.template``. #172 tried only the Execution/Service paths,
# so every field of a real Job rendered blank.
_TASK_PATHS = (
    ("spec", "template", "spec", "template", "spec"),  # gcloud v1 Job
    ("template", "template"),                          # v2 Job
    ("spec", "template", "spec"),                      # v1 Service/Execution
    ("template", "spec"),                              # defensive
    ("template",),                                     # defensive
)

_FIELDS = (
    "image",
    "command",
    "args",
    "timeout",
    "maxRetries",
    "serviceAccount",
    "vpcAccess",
    "resources",
    "envNames",
)


def _dig(job: dict, path: tuple[str, ...]) -> dict | None:
    node: object = job
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, dict) else None


def _has_containers(node: dict) -> bool:
    containers = node.get("containers")
    return (
        isinstance(containers, list)
        and bool(containers)
        and isinstance(containers[0], dict)
    )


def _find_containers_node(job: dict) -> dict:
    """Find the task template wherever a gcloud/API version nests it."""
    stack: list[object] = [job]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if _has_containers(node):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return {}


def _task_template(job: dict) -> tuple[dict, str]:
    found: dict | None = None
    found_shape = "unknown"
    for path in _TASK_PATHS:
        node = _dig(job, path)
        if node is None:
            continue
        if _has_containers(node):
            return node, ".".join(path)
        if found is None:
            found, found_shape = node, ".".join(path)
    if found is not None:
        return found, found_shape
    return _find_containers_node(job), "recursive"


def _first_container(task: dict) -> dict:
    containers = task.get("containers")
    if isinstance(containers, list) and containers and isinstance(containers[0], dict):
        return containers[0]
    return {}


def _find_field(job: dict, *names: str):
    """Last-resort search for a uniquely named field (never print a blank)."""
    stack: list[object] = [job]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for name in names:
                if name in node and node[name] is not None:
                    return node[name]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _pick(job: dict, task: dict, container: dict, *names: str):
    for name in names:
        if name in task:
            return task[name]
        if name in container:
            return container[name]
    return _find_field(job, *names)


def summarize(job: dict) -> dict:
    """Extract the fields the deploy diagnosis prints, from either shape."""
    task, _shape = _task_template(job)
    container = _first_container(task)
    resources = container.get("resources") or {}
    limits = resources.get("limits") if isinstance(resources, dict) else {}
    env = []
    for item in container.get("env") or []:
        if isinstance(item, dict) and item.get("name"):
            env.append(str(item["name"]))
    return {
        # gcloud's task template uses ``timeoutSeconds`` and
        # ``serviceAccountName``; the v2 API uses ``timeout``/``serviceAccount``.
        "image": container.get("image") or _find_field(job, "image"),
        "command": container.get("command") or _find_field(job, "command"),
        "args": container.get("args") or _find_field(job, "args"),
        "timeout": _pick(job, task, container, "timeoutSeconds", "timeout"),
        "maxRetries": _pick(job, task, container, "maxRetries"),
        "serviceAccount": _pick(
            job, task, container, "serviceAccountName", "serviceAccount"
        ),
        "vpcAccess": _pick(job, task, container, "vpcAccess"),
        "resources": limits,
        "envNames": env,
    }


def _render_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def render(job: dict) -> str:
    summary = summarize(job)
    lines = [
        f"image: {_render_value(summary['image'])}",
        f"command: {_render_value(summary['command'])}",
        f"args: {_render_value(summary['args'])}",
        f"timeout: {_render_value(summary['timeout'])}",
        f"maxRetries: {_render_value(summary['maxRetries'])}",
        f"serviceAccount: {_render_value(summary['serviceAccount'])}",
        f"vpcAccess: {_render_value(summary['vpcAccess'])}",
        f"resources: {_render_value(summary['resources'])}",
        f"envNames: {', '.join(summary['envNames'])}",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        help="JSON file from `gcloud run jobs describe --format=json` (default: stdin)",
    )
    parser.add_argument(
        "--field",
        choices=_FIELDS,
        help="Print one field instead of the full summary.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.path:
            with open(args.path, encoding="utf-8") as handle:
                job = json.load(handle)
        else:
            job = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"format_run_job: could not read job JSON: {exc}\n")
        return 2
    if not isinstance(job, dict):
        sys.stderr.write("format_run_job: expected a JSON object\n")
        return 2
    summary = summarize(job)
    if args.field:
        print(_render_value(summary[args.field]))
    else:
        print(render(job))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
