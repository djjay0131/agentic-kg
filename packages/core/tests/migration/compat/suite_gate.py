"""Gate: the compatibility harness actually ran, identified by name.

Why a second gate exists
------------------------
``packages/core/tests/migration/neo4j/suite_gate.py`` guards the *shared KGCS
contract suite*. It was built to defeat exactly one failure — "53 other tests
passed, so the seven that matter must have run" — and it does that well. But it
identifies only those seven names. Feed it a junit report with this entire
compatibility harness deleted and it reports success: the compat tests are, to
that gate, indistinguishable from the "other tests" it already refuses to count.
The review that found this put a doctored report through it and watched it go
green.

That is the same "counting is not identification" failure one level up, and the
fix has to be the same shape: derive the expected names from the source at
runtime and require every one of them.

What this gate does
-------------------
Walks ``packages/core/tests/migration/compat/`` with ``ast`` and collects every
``test_*`` function it defines. That set is the expectation. Then it requires
each one to appear in the junit XML as a **passed** testcase. A deleted module,
a renamed test, a skip, a failure, an ``-m`` filter that silently dropped half
the suite — all go red, and the message names what is missing.

Deliberately *not* a count. ``len(tests) > 0``, or ``>= 80``, is the check this
file exists to replace.

Parametrised tests appear in junit as ``name[param]``, so comparison is on the
bracket-stripped stem.

Usage::

    python packages/core/tests/migration/compat/suite_gate.py test-results/x.xml

Exits non-zero with a specific message on failure.
"""

from __future__ import annotations

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

COMPAT_DIR = Path(__file__).resolve().parent

#: Modules that define no tests and must not be demanded of the report.
NON_TEST_MODULES = frozenset({"__init__.py", "conftest.py", "suite_gate.py"})


class CompatGateError(RuntimeError):
    """The compatibility harness did not run in full."""


def expected_test_names(compat_dir: Path = COMPAT_DIR) -> frozenset[str]:
    """Every ``test_*`` function defined under ``compat_dir``, read from source.

    Derived, never transcribed: a list of names in this file would drift from
    the suite and then vouch for tests that no longer exist.
    """
    names: set[str] = set()
    modules = [
        path
        for path in sorted(compat_dir.rglob("test_*.py"))
        if path.name not in NON_TEST_MODULES
    ]
    if not modules:
        raise CompatGateError(
            f"no test modules found under {compat_dir} - the gate cannot verify "
            f"a suite it cannot find, and would otherwise pass vacuously"
        )
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ):
                names.add(node.name)
    if not names:
        raise CompatGateError(f"{compat_dir} defines no test functions")
    return frozenset(names)


def _stem(name: str) -> str:
    """``test_x[param]`` -> ``test_x``."""
    return name.split("[", 1)[0]


def check(junit_path: Path, expected: frozenset[str] | None = None) -> str:
    """Raise :class:`CompatGateError` unless every compat test ran and passed.

    Returns a one-line summary on success.
    """
    expected = expected_test_names() if expected is None else expected
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)

    outcomes: dict[str, str] = {}
    for suite in suites:
        for case in suite.iter("testcase"):
            name = _stem(case.get("name") or "")
            outcome = "passed"
            for child in case:
                if child.tag == "skipped":
                    outcome = "skipped"
                elif child.tag in ("failure", "error"):
                    outcome = "failed"
            # A parametrised test is only "passed" if every case passed.
            if outcomes.get(name) in ("failed", "skipped"):
                continue
            outcomes[name] = outcome

    missing = sorted(name for name in expected if name not in outcomes)
    if missing:
        raise CompatGateError(
            f"{junit_path}: the compatibility harness did not run in full. "
            f"{len(missing)} of {len(expected)} tests are absent from the "
            f"report: {missing[:8]}{' ...' if len(missing) > 8 else ''}. "
            f"A report containing other passing tests is not a substitute - "
            f"that is the failure this gate exists to catch."
        )

    not_passed = sorted(
        f"{name}={outcomes[name]}" for name in expected if outcomes[name] != "passed"
    )
    if not_passed:
        raise CompatGateError(
            f"{junit_path}: compatibility tests present but not passing: {not_passed}"
        )

    return (
        f"compat harness verified: {len(expected)}/{len(expected)} tests ran and "
        f"passed, identified by name from {COMPAT_DIR.name}/"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <junit.xml>", file=sys.stderr)
        return 2
    try:
        print(check(Path(argv[1])))
    except CompatGateError as exc:
        print(f"COMPAT SUITE GATE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
