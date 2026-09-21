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

That derivation has two honest limits, both stated rather than glossed.

**Deleting a whole module removes it from both sides at once.**
:data:`REQUIRED_MODULES` is therefore the transcribed part, and it is
deliberately the smallest thing that can be transcribed — a list of filenames,
checked to exist.

**Shrinking the required set is itself an attack.** An independent review
demonstrated it: the first version walked ``tree.body`` only, so indenting a
required test under ``if False:`` removed it from the expected set *and* from
execution, and the gate printed ``OK: 93 required tests ran and passed`` and
exited 0. Two fixes, and both are needed. Discovery now uses :func:`ast.walk`,
so a nested or conditionally-defined test is still required. And
:data:`MINIMUM_REQUIRED_TESTS` pins a floor on the count, because ``ast.walk``
alone only moves the attack rather than closing it — deleting the function
outright still shrinks both sides silently. The floor is what makes a shrinking
suite loud.

**Tests are keyed ``module::name``, not by bare name**, and that was the second
round's finding. Keying by name collapses two different tests that share one:
``test_the_package_has_modules_to_check`` is defined in both
``test_config_injection.py`` and ``test_isolation.py``, so the *union* held 104
entries while the *multiset* held 105. The gate required and printed the union
and compared the floor against the multiset -- two numbers that were never the
same quantity. A reviewer measured the consequences: deleting five tests still
printed ``OK: 99 required tests ran and passed`` with rc=0 against a floor of
100, and deleting the duplicated test outright printed ``OK: 104 required...``,
rc=0, with **no change in output at all** -- a required test removed,
invisibly. One qualified set now flows from :func:`expected_tests` through
:func:`qualified_tests` to the junit ``classname`` and the printed count, and
the floor compares that same set. A count computed one way and compared another
is the same defect shape as a check that cannot fail: the two sides simply
stopped referring to the same thing.

**Known residual divergence, recorded rather than closed.** The qualifier is
the junit ``classname``'s *last segment* -- a basename -- while
:func:`expected_tests` reads one specific directory. A hypothetical
``extra/test_isolation.py`` collected in the same run would therefore produce
keys identical to this directory's. It is not exploitable today: on a collision
the worse outcome wins, so a failure cannot be masked by a passing namesake,
and a module that stops collecting still drops ~20 keys and trips the "never
ran" check. But it is the same family as the multiset/union bug above -- two
sides naming the same thing slightly differently -- and closing it means keying
on a path relative to :data:`HERE`, which junit does not hand us directly. Left
open deliberately, and written down so it is a known gap rather than a
surprise.
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

#: Floor on the number of required tests.
#:
#: Pinned, not derived, and that is the point: every other number in this gate
#: is read from the suite, so a suite that shrinks shrinks its own expectations
#: with it and reports OK. This is the one value that does not move on its own.
#: Raise it when the suite grows; a *drop* has to be an explicit edit with a
#: reason, which is exactly the conversation that was missing.
MINIMUM_REQUIRED_TESTS = 105

#: Tests allowed to skip, with the reason each is allowed to. **Empty.**
#:
#: It held exactly one entry --
#: ``test_the_runner_can_construct_its_own_types_from_this_payload``, which
#: could not run until PR #73 put the evaluation runner on this branch's base.
#: #73 merged (``03abb9d``), the test now exercises the real
#: ``ArmPaper``/``ArmEntity``, and the entry was removed *after* the rebase --
#: in that order, because dropping it first turns the gate red.
#:
#: Empty means **no skip passes this gate**, which is the strongest form and
#: matches the canonical-adapter gate next door. The mechanism is kept rather
#: than deleted so a future legitimate skip has somewhere to be declared with a
#: stated reason; what is not kept is an open-ended allowlist, which is how a
#: suite erodes into a green check over nothing.
PERMITTED_SKIPS: dict[str, str] = {}


class SuiteGateError(AssertionError):
    """The suite did not run the way the gate requires."""


def expected_tests() -> dict[str, frozenset[str]]:
    """`{module_stem: {test names}}`, read from the test sources.

    Derived rather than transcribed, so the gate tracks the suite. An empty
    result is itself an error: a gate that expects nothing passes over nothing.

    Note the value is a per-module set. Flattening it to one set of bare names
    loses any test whose name is shared with another module, which is how a
    required test was deleted with no change in this gate's output; use
    :func:`qualified_tests` for anything that counts.
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
        # `ast.walk`, not `tree.body`: a top-level-only scan misses a test
        # indented under `if False:` -- which removes it from the required set
        # *and* from the run, so the gate reported OK over a suite it had just
        # stopped requiring.
        names = frozenset(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        if not names:
            raise SuiteGateError(f"{filename} defines no test functions")
        found[path.stem] = names
    return found


def qualified_tests(required: dict[str, frozenset[str]]) -> set[str]:
    """`{"module::test"}` — the one set the gate counts, requires and prints.

    Qualified, because two modules may legitimately define the same test name
    and a bare-name set silently merges them. Everything downstream uses this,
    so the number checked against the floor is the number reported.
    """
    return {f"{module}::{name}" for module, names in required.items() for name in names}


def _ran(junit_path: Path) -> tuple[dict[str, str], int]:
    """`({"module::test": outcome}, total)` from a junit XML report.

    Outcomes are `"passed"`, `"skipped"`, `"failed"` or `"error"`. Parametrized
    cases arrive as `name[param]`; the bracket is stripped so a parametrized
    test counts once under its function name. The module comes from the last
    segment of the junit `classname` (`...ingestion.test_isolation`), which is
    what keeps two same-named tests in different modules distinct.
    """
    root = ElementTree.parse(junit_path).getroot()
    outcomes: dict[str, str] = {}
    total = 0
    for case in root.iter("testcase"):
        total += 1
        name = (case.get("name") or "").split("[", 1)[0]
        module = (case.get("classname") or "").rsplit(".", 1)[-1]
        key = f"{module}::{name}"
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
        if rank[outcome] >= rank.get(outcomes.get(key, "passed"), 0):
            outcomes[key] = outcome
    return outcomes, total


def check(junit_path: Path, expected: dict[str, frozenset[str]] | None = None) -> str:
    """Raise `SuiteGateError` unless every required test really ran and passed."""
    required = expected_tests() if expected is None else expected
    if not required:
        raise SuiteGateError("the expected-test set is empty; the gate checks nothing")

    # ONE set, qualified by module, used for the floor, the requirement and the
    # printed count alike. Earlier versions counted the multiset here and
    # required the union below; the two differed by every duplicated test name,
    # so a required test could be deleted with no change in this gate's output.
    wanted = qualified_tests(required)
    if len(wanted) < MINIMUM_REQUIRED_TESTS:
        raise SuiteGateError(
            f"the suite requires {len(wanted)} tests, below the pinned floor "
            f"of {MINIMUM_REQUIRED_TESTS}. Tests were removed from the required "
            f"set -- deleted, renamed off the `test_` prefix, or nested under a "
            f"false condition. If the shrink is intended, lower "
            f"MINIMUM_REQUIRED_TESTS deliberately and say why."
        )

    if not junit_path.is_file():
        raise SuiteGateError(f"no junit report at {junit_path}")

    outcomes, total = _ran(junit_path)
    if total == 0:
        raise SuiteGateError(f"{junit_path} records zero test cases")

    missing = sorted(wanted - set(outcomes))
    if missing:
        raise SuiteGateError(
            f"{len(missing)} required test(s) never ran: {missing}. Causes, "
            f"commonest first: the 'migration' extra did not resolve (conftest's "
            f"importorskip turns the whole suite into a no-op); the pytest "
            f"invocation did not reach this directory; a module was renamed out "
            f"of collection; or a test is still *defined* but no longer "
            f"*collected* -- indenting one under `if False:`, or nesting it in a "
            f"plain (non-Test-prefixed) class, keeps it in the required set, "
            f"which is intended, and stops pytest running it."
        )

    bad: list[str] = []
    for key in sorted(wanted):
        outcome = outcomes[key]
        if outcome == "passed":
            continue
        if outcome == "skipped" and key.split("::", 1)[-1] in PERMITTED_SKIPS:
            continue
        bad.append(f"{key}: {outcome}")
    if bad:
        raise SuiteGateError(
            "required tests did not pass: " + "; ".join(bad) + ". A skip is a "
            "failure here unless it is in PERMITTED_SKIPS with a stated reason."
        )

    skipped = sorted(k for k in wanted if outcomes[k] == "skipped")
    return (
        f"shadow-ingestion suite gate OK: {len(wanted)} required tests ran and "
        f"passed across {len(required)} modules "
        f"(floor {MINIMUM_REQUIRED_TESTS}; {total} cases total; "
        f"permitted skips: {skipped or 'none'})"
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
