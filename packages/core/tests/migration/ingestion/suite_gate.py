"""CI gate: did the shadow-ingestion suite actually run?

    python packages/core/tests/migration/ingestion/suite_gate.py <junit.xml>

A skipped suite and a passing suite are indistinguishable in a green check.
`conftest.py` calls `pytest.importorskip("kgis")` at module scope, so on a
runner without the opt-in `migration` extra every test here vanishes silently
and the job goes green having executed nothing — which is BACKLOG KG-1, and is
the failure mode the canonical-adapter gate next door was written after
experiencing.

**It identifies, it does not count.** The neighbouring gate records what
happened when it counted instead: an earlier version asserted only
`tests > 0 and skipped == 0`, and deleting a test module left 53 other tests
passing while the seven it was guarding were gone. So this gate names the
checks it requires and looks for each of them by name.

**The required set is derived, not transcribed.** It is read from the test
modules themselves — every `test_*` function defined in the modules listed in
:data:`REQUIRED_MODULES`. A test added upstream of this file is covered without
an edit, and a test *deleted* is caught, because the gate re-reads the source
at gate time while the junit report reflects what ran. If the two disagree,
something was collected and not run.

That derivation has one honest limit, stated rather than glossed: deleting a
whole module removes it from both sides at once. :data:`REQUIRED_MODULES` is
therefore the transcribed part, and it is deliberately the *smallest* thing
that can be transcribed — a list of filenames, checked to exist.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent

#: The modules this suite is not allowed to lose. Filenames only — the tests
#: inside them are discovered. `test_shadow_is_falsifiable` is on the list
#: because a suite that loses its own falsifiability proof is exactly the state
#: this gate exists to make visible.
REQUIRED_MODULES: tuple[str, ...] = (
    "test_arm_export.py",
    "test_config_injection.py",
    "test_corpus.py",
    "test_documents.py",
    "test_identity.py",
    "test_isolation.py",
    "test_ontology.py",
    "test_shadow_is_falsifiable.py",
    "test_shadow_run.py",
)

#: Tests allowed to skip, with the reason each is allowed to.
#:
#: Exactly one entry, and it disappears when PR #73 merges. An open-ended
#: allowlist would let any future skip be waved through, which is how a suite
#: erodes into a green check over nothing.
PERMITTED_SKIPS: dict[str, str] = {
    "test_the_runner_can_construct_its_own_types_from_this_payload": (
        "PR #73 (the evaluation runner) is not on this branch's base yet"
    ),
}


class SuiteGateError(AssertionError):
    """The suite did not run the way the gate requires."""


def expected_tests() -> dict[str, frozenset[str]]:
    """`{module_stem: {test names}}`, read from the test sources.

    Derived rather than transcribed, so the gate tracks the suite. An empty
    result is itself an error: a gate that expects nothing passes over nothing.
    """
    found: dict[str, frozenset[str]] = {}
    for filename in REQUIRED_MODULES:
        path = HERE / filename
        if not path.is_file():
            raise SuiteGateError(
                f"required test module is missing: {path}. Deleting a module "
                f"removes it from the discovered set too, so this list is the "
                f"one place that notices."
            )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = frozenset(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        if not names:
            raise SuiteGateError(f"{filename} defines no test functions")
        found[path.stem] = names
    return found


def _ran(junit_path: Path) -> tuple[dict[str, str], int]:
    """`({test name: outcome}, total)` from a junit XML report.

    Outcomes are `"passed"`, `"skipped"`, `"failed"` or `"error"`. Parametrized
    cases arrive as `name[param]`; the bracket is stripped so a parametrized
    test counts once under its function name.
    """
    root = ElementTree.parse(junit_path).getroot()
    outcomes: dict[str, str] = {}
    total = 0
    for case in root.iter("testcase"):
        total += 1
        name = (case.get("name") or "").split("[", 1)[0]
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        # A parametrized test whose cases disagree keeps the worse outcome.
        rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
        if rank[outcome] >= rank.get(outcomes.get(name, "passed"), 0):
            outcomes[name] = outcome
    return outcomes, total


def check(junit_path: Path, expected: dict[str, frozenset[str]] | None = None) -> str:
    """Raise `SuiteGateError` unless every required test really ran and passed."""
    required = expected_tests() if expected is None else expected
    if not required:
        raise SuiteGateError("the expected-test set is empty; the gate checks nothing")

    if not junit_path.is_file():
        raise SuiteGateError(f"no junit report at {junit_path}")

    outcomes, total = _ran(junit_path)
    if total == 0:
        raise SuiteGateError(f"{junit_path} records zero test cases")

    wanted = {name for names in required.values() for name in names}
    missing = sorted(wanted - set(outcomes))
    if missing:
        raise SuiteGateError(
            f"{len(missing)} required test(s) never ran: {missing}. Either the "
            f"'migration' extra did not resolve (conftest's importorskip turned "
            f"the suite into a no-op), a module was renamed out of collection, "
            f"or the pytest invocation did not reach this directory."
        )

    bad: list[str] = []
    for name in sorted(wanted):
        outcome = outcomes[name]
        if outcome == "passed":
            continue
        if outcome == "skipped" and name in PERMITTED_SKIPS:
            continue
        bad.append(f"{name}: {outcome}")
    if bad:
        raise SuiteGateError(
            "required tests did not pass: " + "; ".join(bad) + ". A skip is a "
            "failure here unless it is in PERMITTED_SKIPS with a stated reason."
        )

    skipped = sorted(n for n in wanted if outcomes[n] == "skipped")
    return (
        f"shadow-ingestion suite gate OK: {len(wanted)} required tests ran and "
        f"passed across {len(required)} modules "
        f"({total} cases total; permitted skips: {skipped or 'none'})"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <junit.xml>", file=sys.stderr)
        return 2
    try:
        print(check(Path(argv[1])))
    except SuiteGateError as exc:
        print(f"SUITE GATE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
