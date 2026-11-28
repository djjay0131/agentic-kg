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

_V1_PATH = ("spec", "template", "spec")
_V2_PATH = ("template", "template")

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


def _task_template(job: dict) -> tuple[dict, str]:
    for path, shape in ((_V1_PATH, "v1"), (_V2_PATH, "v2")):
        node = _dig(job, path)
        if node is not None:
            return node, shape
    return {}, "unknown"


def _first_container(task: dict) -> dict:
    containers = task.get("containers")
    if isinstance(containers, list) and containers and isinstance(containers[0], dict):
        return containers[0]
    return {}


def _pick(task: dict, container: dict, *names: str):
    for name in names:
        if name in task:
            return task[name]
        if name in container:
            return container[name]
    return None


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
        "image": container.get("image"),
        "command": container.get("command"),
        "args": container.get("args"),
        "timeout": _pick(task, container, "timeoutSeconds", "timeout"),
        "maxRetries": _pick(task, container, "maxRetries"),
        "serviceAccount": _pick(task, container, "serviceAccountName", "serviceAccount"),
        "vpcAccess": _pick(task, container, "vpcAccess"),
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
