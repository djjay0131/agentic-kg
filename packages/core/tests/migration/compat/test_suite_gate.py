"""The compat gate is falsifiable: every way the suite can vanish is rejected.

A gate is a check like any other, and §9.0 obligation 5 applies to it hardest —
a gate that cannot fail is worse than no gate, because it is *reported* as
assurance. So each rejection path gets a doctored report and an assertion that
it goes red.

Two motivating cases, both found by review rather than by writing:

* ``neo4j/suite_gate.py`` accepted a junit report with this entire harness
  absent, because it identifies seven shared contract tests by name and treats
  everything else as uncounted background. That gate is not wrong — it is
  scoped. This one covers the scope it leaves open.
* **V-5**: the first version of *this* gate matched junit's ``name`` and
  ignored ``classname``, so any passing testcase anywhere satisfied a compat
  requirement. Live, not hypothetical: ``test_an_empty_report_is_rejected``
  exists in this directory *and* in ``neo4j/``, so one of the 80 was un-gated.
  ``test_a_namesake_in_another_package_does_not_satisfy_a_requirement``
  reproduces exactly that, and
  ``test_the_real_cross_package_collision_is_covered`` asserts the collision
  still exists so the regression test cannot quietly become vacuous.

Needs no database.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .suite_gate import COMPAT_PACKAGE, CompatGateError, check, expected_tests

NEIGHBOUR_DIR = Path(__file__).resolve().parents[1] / "neo4j"


def _report(cases: list[tuple[str, str, str]]) -> str:
    """Build a junit XML body from (classname, name, outcome) triples."""
    body = []
    for classname, name, outcome in cases:
        if outcome == "passed":
            body.append(f'<testcase classname="{classname}" name="{name}"/>')
        elif outcome == "skipped":
            body.append(
                f'<testcase classname="{classname}" name="{name}"><skipped/></testcase>'
            )
        else:
            body.append(
                f'<testcase classname="{classname}" name="{name}">'
                f"<failure>boom</failure></testcase>"
            )
    return "<testsuites><testsuite>" + "".join(body) + "</testsuite></testsuites>"


def _write(tmp_path: Path, cases: list[tuple[str, str, str]]) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(_report(cases), encoding="utf-8")
    return path


def _classname(module: str) -> str:
    return f"{COMPAT_PACKAGE}.{module}"


def _all_passing() -> list[tuple[str, str, str]]:
    return [
        (_classname(module), name, "passed")
        for module, name in sorted(expected_tests())
    ]


# ---------------------------------------------------------------------------
# The expectation is real
# ---------------------------------------------------------------------------


def test_expected_tests_are_derived_from_source_and_are_plentiful() -> None:
    """Obligation 1: the gate must know about a real, non-trivial suite."""
    pairs = expected_tests()
    assert len(pairs) >= 50, f"gate expects only {len(pairs)} tests"
    modules = {module for module, _ in pairs}
    assert len(modules) >= 6, f"gate only sees modules {sorted(modules)}"
    names = {name for _, name in pairs}
    assert any(n.startswith("test_the_guard") for n in names)
    assert any("inventory" in n or "snippet" in n for n in names)
    assert any("ledger" in n or "canonical" in n for n in names)


def test_the_gate_is_not_a_transcribed_list() -> None:
    """The names come from the files, so a new test appears without editing this.

    Asserted by checking the gate's own source contains no literal test name —
    a transcribed list is the drift failure this gate is supposed to prevent
    one level down.
    """
    source = (Path(__file__).resolve().parent / "suite_gate.py").read_text(encoding="utf-8")
    hardcoded = [
        line
        for line in source.splitlines()
        if '"test_' in line and "startswith" not in line and "test_*" not in line
    ]
    assert hardcoded == [], f"gate hardcodes test names: {hardcoded}"


def test_tests_nested_in_a_class_are_collected() -> None:
    """V-6: walking only ``tree.body`` let a class wrapper shrink the expectation."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        module = Path(tmp) / "test_nested.py"
        module.write_text(
            "class TestGroup:\n"
            "    def test_inside_a_class(self): ...\n\n"
            "def test_at_top_level(): ...\n",
            encoding="utf-8",
        )
        pairs = expected_tests(Path(tmp))
    assert pairs == {
        ("test_nested", "test_inside_a_class"),
        ("test_nested", "test_at_top_level"),
    }


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------


def test_a_complete_passing_report_is_accepted(tmp_path: Path) -> None:
    """Without this, every rejection below could be a gate that refuses all input."""
    summary = check(_write(tmp_path, _all_passing()))
    assert "compat harness verified" in summary


def test_parametrised_names_are_matched_on_their_stem(tmp_path: Path) -> None:
    """pytest reports ``test_x[param]``; the gate must not demand a bare name."""
    cases = [(cls, f"{name}[case-1]", "passed") for cls, name, _ in _all_passing()]
    assert "compat harness verified" in check(_write(tmp_path, cases))


def test_a_class_qualified_classname_is_accepted(tmp_path: Path) -> None:
    """pytest reports ``<module>.<Class>`` for a nested test; still ours."""
    cases = [
        (f"{cls}.TestGroup", name, "passed") for cls, name, _ in _all_passing()
    ]
    assert "compat harness verified" in check(_write(tmp_path, cases))


# ---------------------------------------------------------------------------
# V-5: identity, not name
# ---------------------------------------------------------------------------


def test_the_real_cross_package_collision_is_covered() -> None:
    """The regression below must keep describing a collision that exists.

    If ``neo4j/`` ever stops defining a name this package also defines, the
    next test still passes but stops demonstrating anything. This asserts the
    precondition rather than assuming it.
    """
    neighbour_names: set[str] = set()
    for path in sorted(NEIGHBOUR_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            ):
                neighbour_names.add(node.name)
    ours = {name for _, name in expected_tests()}
    assert neighbour_names, "the neighbouring suite defines no tests - scan broken"
    collisions = ours & neighbour_names
    assert collisions, (
        "no compat test name is shared with the neighbouring suite any more, so "
        "test_a_namesake_in_another_package_does_not_satisfy_a_requirement no "
        "longer reproduces V-5. Point it at a fresh collision or delete it."
    )


def test_a_namesake_in_another_package_does_not_satisfy_a_requirement(
    tmp_path: Path,
) -> None:
    """V-5, reproduced exactly: delete a compat test, keep a namesake elsewhere.

    The first version of this gate reported 80/80 green on this input. The
    fix — keying on ``(module, name)`` and filtering by ``classname`` — makes
    the foreign testcase satisfy nothing.
    """
    victim_module, victim_name = sorted(expected_tests())[0]
    cases = [
        (cls, name, "passed")
        for cls, name, _ in _all_passing()
        if not (cls == _classname(victim_module) and name == victim_name)
    ]
    # ...and the namesake, passing, in the neighbouring package.
    cases.append(
        ("packages.core.tests.migration.neo4j.test_suite_gate", victim_name, "passed")
    )
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, cases))
    assert f"{victim_module}::{victim_name}" in str(excinfo.value)


def test_a_compat_test_passing_under_a_foreign_classname_is_not_credited(
    tmp_path: Path,
) -> None:
    """The general form: every compat name present, none under our package."""
    cases = [("some.other.package.test_x", name, "passed") for _, name, _ in _all_passing()]
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, cases))
    assert "did not run in full" in str(excinfo.value)


def test_two_modules_sharing_a_name_stay_two_requirements(tmp_path: Path) -> None:
    """The multiset variant (PR #83's shape), closed structurally here.

    A bare-name expectation would collapse these into one, so deleting either
    module's copy would be invisible. Asserted on a synthetic expectation so
    the test does not depend on a collision existing inside this package today.
    """
    expected = frozenset({("test_a", "test_shared"), ("test_b", "test_shared")})
    complete = [
        (_classname("test_a"), "test_shared", "passed"),
        (_classname("test_b"), "test_shared", "passed"),
    ]
    assert "compat harness verified" in check(_write(tmp_path, complete), expected)

    with pytest.raises(CompatGateError):
        check(_write(tmp_path, complete[:1]), expected)


# ---------------------------------------------------------------------------
# Every other way the suite can vanish
# ---------------------------------------------------------------------------


def test_the_whole_harness_missing_is_rejected(tmp_path: Path) -> None:
    """The exact doctored report that the neighbouring gate accepted."""
    decoys = [
        ("packages.core.tests.migration.neo4j.test_read_surface", f"test_other_{i}", "passed")
        for i in range(200)
    ]
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, decoys))
    assert "did not run in full" in str(excinfo.value)


def test_one_deleted_module_is_rejected(tmp_path: Path) -> None:
    """Partial runs are the realistic failure -- an `-m` filter, a collection error."""
    cases = [c for c in _all_passing() if "test_anti_vacuity_guard" not in c[0]]
    assert len(cases) < len(_all_passing())
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, cases))


def test_a_skipped_compat_test_is_rejected(tmp_path: Path) -> None:
    """A skip is how a missing Docker or a missing extra turns green."""
    cases = _all_passing()
    cases[0] = (cases[0][0], cases[0][1], "skipped")
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, cases))
    assert "not passing" in str(excinfo.value)


def test_a_failing_compat_test_is_rejected(tmp_path: Path) -> None:
    cases = _all_passing()
    cases[-1] = (cases[-1][0], cases[-1][1], "failed")
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, cases))


def test_one_failing_parametrisation_is_rejected(tmp_path: Path) -> None:
    """A test that passes for one param and fails for another has not passed."""
    module, name = sorted(expected_tests())[0]
    cases = _all_passing()
    cases.append((_classname(module), f"{name}[bad]", "failed"))
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, cases))


def test_an_empty_report_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, []))


def test_a_gate_pointed_at_a_suite_that_does_not_exist_refuses(tmp_path: Path) -> None:
    """The vacuous case for the gate itself: no modules found.

    A path typo must not silently produce an empty expectation that every
    report satisfies.
    """
    with pytest.raises(CompatGateError) as excinfo:
        expected_tests(tmp_path / "nowhere")
    assert "no test modules found" in str(excinfo.value)
