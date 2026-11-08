"""Render the nightly Cloud Workflows definition and prove it is valid YAML.

Terraform's ``google_workflows_workflow`` accepts ``source_contents`` without
validating it at plan time, so a malformed Workflows definition is only rejected
when the API is updated during ``apply`` (run 41870176355 failed exactly this
way: ``main.yaml: parse error: Unterminated expression: ${curate ? "true"``).

This test renders the Terraform heredoc in ``infra/main.tf`` the same way
Terraform would for the staging var-file, then:

* parses the rendered document with a YAML parser;
* checks every ``${...}`` Workflows expression is balanced;
* checks expressions containing a colon or quotes are wrapped in single quotes
  (Cloud Workflows' documented requirement);
* pins the rendered ``init`` assignments so the trigger/curate/research-cycle
  wiring cannot silently drift.

No network, no Docker, no Terraform binary. ``terraform`` is not installed on
the unit-test CI runner, so the small interpolation subset this heredoc uses
(``var.*`` and resource/local references) is reproduced here. The renderer
fails loudly on any construct it does not understand, so a future expression
cannot be silently mis-rendered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAIN_TF = _REPO_ROOT / "infra" / "main.tf"
_STAGING_TFVARS = _REPO_ROOT / "infra" / "envs" / "staging.tfvars"

# Resource ids render as plain strings; only YAML structure matters here.
_PLACEHOLDER = "projects/vt-gcp-00042/locations/us-central1/jobs/placeholder"


def _load_tfvars(path: Path) -> dict[str, Any]:
    """Parse the flat ``key = literal`` subset used by the env files."""
    values: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, literal = (part.strip() for part in line.split("=", 1))
        if literal == "true":
            values[name] = True
        elif literal == "false":
            values[name] = False
        elif literal.startswith('"') and literal.endswith('"'):
            values[name] = literal[1:-1]
        else:
            try:
                values[name] = int(literal)
            except ValueError:
                values[name] = literal
    return values


def _dedent(lines: list[str]) -> str:
    """Apply HCL's ``<<-`` rule: strip the least-indented non-empty line."""
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    trim = min(indents) if indents else 0
    return "\n".join(line[trim:] if line.strip() else "" for line in lines)


def _extract_source_contents(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*source_contents\s*=\s*<<-?([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if match is None:
            continue
        marker = match.group(1)
        end = index + 1
        while end < len(lines) and lines[end].strip() != marker:
            end += 1
        assert end < len(lines), f"unterminated heredoc marker {marker!r}"
        return _dedent(lines[index + 1 : end])
    raise AssertionError("infra/main.tf has no source_contents heredoc")


def _terraform_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _evaluate(expression: str, variables: dict[str, Any]) -> str:
    if expression.startswith("var."):
        name = expression[len("var.") :]
        assert name in variables, f"staging.tfvars does not set var.{name}"
        return _terraform_literal(variables[name])
    if expression.startswith(("local.", "google_", "data.")):
        # A resource/local reference renders to a plain string at apply time.
        return _PLACEHOLDER
    raise AssertionError(f"unsupported Terraform interpolation: ${{{expression}}}")


def _render(source: str, variables: dict[str, Any]) -> str:
    # ``$${`` is Terraform's escape for a literal ``${`` (a Workflows
    # expression). Protect it before rendering real Terraform interpolations.
    protected = source.replace("$${", "\x00{")

    def replace(match: re.Match[str]) -> str:
        return _evaluate(match.group(1).strip(), variables)

    rendered = re.sub(r"\$\{([^{}]*)\}", replace, protected)
    return rendered.replace("\x00{", "${")


def _expression_end(text: str, start: int) -> int | None:
    """Return the index of the ``}`` closing the expression at ``start``."""
    depth = 0
    in_single = in_double = escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_single:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "'":
                in_single = False
            continue
        if in_double:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
            continue
        if char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _expressions(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = line.find("${", cursor)
        if start == -1:
            return spans
        end = _expression_end(line, start)
        assert end is not None, f"unterminated Workflows expression in: {line}"
        spans.append((start, end))
        cursor = end + 1


def _rendered(overrides: dict[str, Any] | None = None) -> str:
    variables = _load_tfvars(_STAGING_TFVARS)
    variables.update(overrides or {})
    return _render(_extract_source_contents(_MAIN_TF.read_text(encoding="utf-8")), variables)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"nightly_curate_enabled": False},
        {"research_cycle_enabled": True},
        {"nightly_curate_enabled": False, "research_cycle_enabled": True},
    ],
    ids=["staging", "curate-off", "research-cycle-on", "both-on"],
)
def test_rendered_nightly_workflow_is_valid_yaml(overrides: dict[str, Any]) -> None:
    document = yaml.safe_load(_rendered(overrides))
    assert isinstance(document, dict)
    assert set(document) == {"main"}
    steps = document["main"]["steps"]
    assert isinstance(steps, list) and steps
    names = [next(iter(step)) for step in steps]
    assert names[0] == "init"
    assert {"runIngest", "runAdviser", "runResearchCycle"} <= set(names)


def test_every_workflow_expression_is_balanced() -> None:
    rendered = _rendered()
    for line in rendered.splitlines():
        for start, end in _expressions(line):
            assert line[start] == "$" and line[start + 1] == "{"
            assert line[end] == "}"
    # Catch a stray ``${`` that the line scanner might otherwise skip.
    assert rendered.count("${") == sum(len(_expressions(line)) for line in rendered.splitlines())


def test_expressions_with_colons_or_quotes_are_single_quoted() -> None:
    for line in _rendered().splitlines():
        for start, end in _expressions(line):
            expression = line[start : end + 1]
            if ":" not in expression and '"' not in expression:
                continue
            assert start > 0 and line[start - 1] == "'", line
            assert end + 1 < len(line) and line[end + 1] == "'", line


def test_init_assignments_are_staging_values() -> None:
    document = yaml.safe_load(_rendered())
    steps = document["main"]["steps"]
    init = next(step["init"] for step in steps if "init" in step)
    assignments = {key: value for item in init["assign"] for key, value in item.items()}

    # staging.tfvars: nightly_curate_enabled = true, research_cycle_enabled = false
    assert assignments["curate"] == "${default(args.curate, true)}"
    assert assignments["researchCycle"] == "${default(args.research_cycle, false)}"
    # Workflows has no ternary operator; the flag must go through if().
    assert assignments["curateFlag"] == '${if(curate, "true", "false")}'
    assert "?" not in assignments["curateFlag"]


def test_workflow_passes_the_curate_flag_to_the_ingest_job() -> None:
    rendered = _rendered()
    assert "- name: NIGHTLY_CURATE_ENABLED" in rendered
    assert "value: ${curateFlag}" in rendered
