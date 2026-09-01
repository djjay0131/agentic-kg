#!/usr/bin/env python3
"""Validate ground-truth gold files against SCHEMA.md's checklist.

Checks, per gold file:
  * every ``expected_topics[].name`` exists in seed_taxonomy.yml at the
    declared level (the closed-set constraint the extractor enforces via a
    pydantic Literal)
  * every concept/model/method has a ``quoted_text`` of >= 10 characters
  * every quote appears verbatim in the paper's ``.txt``, comparing with
    internal whitespace normalised (the PDF extractor hard-wraps mid-sentence)
  * ``problems[].statement`` >= 20 chars, ``problems[].quoted_text`` >= 10
  * ``involves_concepts`` reference concepts declared in the same file
  * ``reviewer`` matches the containing directory
  * ``cites_within_set`` uses known slugs

Usage:
    .venv-gt/Scripts/python.exe scripts/validate_ground_truth.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GT_DIR = (
    REPO / "packages" / "core" / "tests" / "extraction"
    / "fixtures" / "ground_truth_chain"
)
TAXONOMY = (
    REPO / "packages" / "core" / "src" / "agentic_kg" / "knowledge_graph"
    / "data" / "seed_taxonomy.yml"
)

SLUGS = {
    "cskg", "cskg2", "kg_construction_survey", "llm_ontology_gen",
    "fact_completion", "kg_validation_hitl", "hypothesis_generation", "empire",
}
ENTITY_KEYS = ("expected_concepts", "expected_models", "expected_methods")


def load_taxonomy() -> set[tuple[str, str]]:
    """Return {(name, level)} for every node in the seed taxonomy."""
    def walk(nodes):
        for node in nodes:
            yield (node["name"], node["level"])
            yield from walk(node.get("children", []))

    with TAXONOMY.open(encoding="utf-8") as handle:
        return set(walk(yaml.safe_load(handle)))


def norm(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def check(path: Path, taxonomy: set[tuple[str, str]]) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        gold = yaml.safe_load(handle)
    errors: list[str] = []

    slug = gold.get("slug")
    if slug not in SLUGS:
        errors.append(f"unknown slug {slug!r}")
        return errors

    expected_reviewer = path.parent.name
    if gold.get("reviewer") != expected_reviewer:
        errors.append(
            f"reviewer {gold.get('reviewer')!r} != directory {expected_reviewer!r}"
        )

    source = GT_DIR / f"paper_{slug}.txt"
    if not source.exists():
        errors.append(f"missing source text {source.name}")
        return errors
    haystack = norm(source.read_text(encoding="utf-8"))

    for name, level in (
        (t.get("name"), t.get("level")) for t in gold.get("expected_topics") or []
    ):
        if (name, level) not in taxonomy:
            near = [n for n, _ in taxonomy if n == name]
            hint = f" (name exists at level {near})" if near else ""
            errors.append(f"topic not in seed taxonomy: {name!r}/{level!r}{hint}")

    declared: set[str] = set()
    for key in ENTITY_KEYS:
        for entry in gold.get(key) or []:
            canonical = entry.get("canonical", "<missing canonical>")
            if key == "expected_concepts":
                declared.add(canonical)
            quote = entry.get("quoted_text") or ""
            if len(quote) < 10:
                errors.append(f"{key}:{canonical}: quoted_text < 10 chars")
            elif norm(quote) not in haystack:
                errors.append(f"{key}:{canonical}: quote not found verbatim")

    for index, problem in enumerate(gold.get("problems") or []):
        if len(problem.get("statement") or "") < 20:
            errors.append(f"problems[{index}]: statement < 20 chars")
        quote = problem.get("quoted_text") or ""
        if len(quote) < 10:
            errors.append(f"problems[{index}]: quoted_text < 10 chars")
        elif norm(quote) not in haystack:
            errors.append(f"problems[{index}]: quote not found verbatim")
        for concept in problem.get("involves_concepts") or []:
            if concept not in declared:
                errors.append(
                    f"problems[{index}]: involves_concepts {concept!r} "
                    "is not a declared concept"
                )

    for cited in gold.get("cites_within_set") or []:
        if cited not in SLUGS:
            errors.append(f"cites_within_set: unknown slug {cited!r}")
        if cited == slug:
            errors.append("cites_within_set: paper cites itself")

    return errors


def main() -> int:
    taxonomy = load_taxonomy()
    files = sorted(GT_DIR.glob("*/paper_*.gold.yml"))
    if not files:
        print(f"No gold files found under {GT_DIR}")
        return 0

    total = 0
    for path in files:
        errors = check(path, taxonomy)
        total += len(errors)
        label = path.relative_to(GT_DIR).as_posix()
        if errors:
            print(f"FAIL  {label}")
            for error in errors:
                print(f"        {error}")
        else:
            print(f"ok    {label}")

    print(f"\n{len(files)} file(s), {total} problem(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
