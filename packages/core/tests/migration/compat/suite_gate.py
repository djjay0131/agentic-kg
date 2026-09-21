"""Gate: the compatibility harness actually ran, identified by name.

Why a second gate exists
------------------------
``packages/core/tests/migration/neo4j/suite_gate.py`` guards the *shared KGCS
contract suite*. It was built to defeat exactly one failure — "53 other tests
passed, so the seven that matter must have run" — and it does that well. But it
identifies only those seven names. Feed it a junit report with this entire
compatibility harness deleted and it reports success: the compat tests are, to
that gate, indistinguishable from the "other tests" it already refuses to count.

That is the same "counting is not identification" failure one level up, and the
fix has to be the same shape: derive the expected names from the source at
runtime and require every one of them.

Three iterations of that failure have now been found in this area, each a
different shape. Recording them, because the pattern is the point:

1. **Count, not names** — the sibling gate's original `tests > 0 and
   skipped == 0`.
2. **Union, not multiset** (PR #83) — a set of names cannot notice that one of
   two identically-named tests vanished.
3. **Name, not identity** (V-5, fixed here) — matching junit's ``name`` while
   ignoring ``classname``, so *any* passing testcase in the report satisfied a
   compat requirement. This was live, not hypothetical:
   ``test_an_empty_report_is_rejected`` exists in both this directory and
   ``neo4j/``, so one of the 80 was genuinely un-gated.

Keying the expectation on ``(module, test_name)`` and filtering junit
testcases by ``classname`` closes 2 and 3 together: a foreign package can
satisfy nothing, and two compat modules sharing a name stay two expectations.

What this gate does
-------------------
Walks ``packages/core/tests/migration/compat/`` with ``ast`` and collects every
``test_*`` function it defines — including those nested in a class (V-6:
walking only ``tree.body`` meant wrapping a test in a class silently shrank the
expectation). That set of ``(module, name)`` pairs is the expectation. Then it
requires each pair to appear in the junit XML, under a ``classname`` belonging
to this package, as a **passed** testcase. A deleted module, a renamed test, a
skip, a failure, an ``-m`` filter that silently dropped half the suite, or a
same-named test passing elsewhere — all go red, and the message names what is
missing.

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

#: The dotted fragment every compat testcase's junit ``classname`` must contain.
#: Derived from this file's own location, so moving the package cannot silently
#: turn the filter into one that matches nothing.
COMPAT_PACKAGE = ".".join(COMPAT_DIR.parts[-4:])

#: Modules that define no tests and must not be demanded of the report.
NON_TEST_MODULES = frozenset({"__init__.py", "conftest.py", "suite_gate.py"})


class CompatGateError(RuntimeError):
    """The compatibility harness did not run in full."""


def expected_tests(compat_dir: Path = COMPAT_DIR) -> frozenset[tuple[str, str]]:
    """Every compat test as a ``(module_stem, test_name)`` pair, read from source.

    Pairs rather than bare names: see the V-5 / multiset notes in the module
    docstring. Derived, never transcribed — a list of names in this file would
    drift from the suite and then vouch for tests that no longer exist.
    """
    pairs: set[tuple[str, str]] = set()
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
        # ast.walk, not tree.body: a test nested in a class is still a test.
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ):
                pairs.add((path.stem, node.name))
    if not pairs:
        raise CompatGateError(f"{compat_dir} defines no test functions")
    return frozenset(pairs)


def _stem(name: str) -> str:
    """``test_x[param]`` -> ``test_x``."""
    return name.split("[", 1)[0]


def _module_of(classname: str) -> str | None:
    """The compat module a junit ``classname`` belongs to, or None if foreign.

    ``classname`` is the dotted module path, optionally followed by a class
    name for a test nested in one. A testcase from any other package returns
    None and can therefore satisfy nothing.
    """
    if COMPAT_PACKAGE not in classname:
        return None
    tail = classname.split(COMPAT_PACKAGE, 1)[1].lstrip(".")
    return tail.split(".", 1)[0] if tail else None


def check(junit_path: Path, expected: frozenset[tuple[str, str]] | None = None) -> str:
    """Raise :class:`CompatGateError` unless every compat test ran and passed.

    Returns a one-line summary on success.
    """
    expected = expected_tests() if expected is None else expected
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)

    outcomes: dict[tuple[str, str], str] = {}
    for suite in suites:
        for case in suite.iter("testcase"):
            module = _module_of(case.get("classname") or "")
            if module is None:
                continue  # not ours; cannot satisfy a compat requirement
            key = (module, _stem(case.get("name") or ""))
            outcome = "passed"
            for child in case:
                if child.tag == "skipped":
                    outcome = "skipped"
                elif child.tag in ("failure", "error"):
                    outcome = "failed"
            # A parametrised test is only "passed" if every case passed.
            if outcomes.get(key) in ("failed", "skipped"):
                continue
            outcomes[key] = outcome

    missing = sorted(key for key in expected if key not in outcomes)
    if missing:
        shown = [f"{module}::{name}" for module, name in missing[:8]]
        raise CompatGateError(
            f"{junit_path}: the compatibility harness did not run in full. "
            f"{len(missing)} of {len(expected)} tests are absent from the "
            f"report: {shown}{' ...' if len(missing) > 8 else ''}. "
            f"A report containing other passing tests is not a substitute, and "
            f"neither is a same-named test in another package - that is the "
            f"failure this gate exists to catch."
        )

    not_passed = sorted(
        f"{module}::{name}={outcomes[(module, name)]}"
        for module, name in expected
        if outcomes[(module, name)] != "passed"
    )
    if not_passed:
        raise CompatGateError(
            f"{junit_path}: compatibility tests present but not passing: {not_passed}"
        )

    modules = len({module for module, _ in expected})
    return (
        f"compat harness verified: {len(expected)}/{len(expected)} tests across "
        f"{modules} modules ran and passed, identified by (module, name) under "
        f"{COMPAT_PACKAGE}"
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
