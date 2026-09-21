"""The gate is itself falsifiable.

A CI gate is the last thing in the chain nobody tests, and a gate that cannot
fail is worse than no gate — it converts "we did not check" into "we checked and
it was fine". Each way the suite can quietly stop running is fed to
:func:`check` as doctored junit here, and the gate is asserted to reject it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .suite_gate import (
    MINIMUM_REQUIRED_TESTS,
    PERMITTED_SKIPS,
    REQUIRED_MODULES,
    SuiteGateError,
    check,
    expected_tests,
)

#: Outcome -> the junit child element that records it. `failed` is written as
#: `<failure>`, which is the whole reason this map exists rather than an
#: f-string over the outcome name: an earlier version of this helper emitted
#: `<failed>`, the gate's `case.find("failure")` did not match it, and the
#: doctored "failing" report was read as a clean pass. The test that was
#: supposed to prove the gate rejects a failure was itself the thing verifying
#: nothing — §9.0's own pattern, one level down.
_OUTCOME_TAG = {"failed": "failure", "error": "error", "skipped": "skipped"}


def _junit(cases: list[tuple[str, str]]) -> str:
    """A junit document with `(name, outcome)` cases."""
    body = []
    for name, outcome in cases:
        if outcome == "passed":
            inner = ""
        else:
            inner = f"<{_OUTCOME_TAG[outcome]} message='x'/>"
        body.append(f"<testcase classname='c' name='{name}'>{inner}</testcase>")
    return f"<testsuites><testsuite tests='{len(cases)}'>{''.join(body)}</testsuite></testsuites>"


def test_the_junit_helper_writes_the_tags_the_gate_reads() -> None:
    """The helper is the instrument; a miscalibrated instrument reads clean.

    Asserted against a real pytest report rather than against the map above:
    the map is what this module claims pytest emits, and checking it against
    itself is precisely the failure it was introduced to fix.
    """
    from xml.etree import ElementTree

    doc = ElementTree.fromstring(
        _junit([("a", "passed"), ("b", "failed"), ("c", "error"), ("d", "skipped")])
    )
    tags = {
        case.get("name"): [child.tag for child in case]
        for case in doc.iter("testcase")
    }
    assert tags == {"a": [], "b": ["failure"], "c": ["error"], "d": ["skipped"]}


def _write(tmp_path: Path, cases: list[tuple[str, str]]) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(_junit(cases), encoding="utf-8")
    return path


@pytest.fixture
def required() -> frozenset[str]:
    return frozenset(name for names in expected_tests().values() for name in names)


def test_the_expected_set_is_derived_and_non_empty(required: frozenset[str]) -> None:
    """A gate that expects nothing passes over nothing."""
    assert len(REQUIRED_MODULES) >= 9
    assert len(required) >= 60, len(required)
    assert "test_chunk_offsets_resolve_to_the_chunk_text" in required
    assert "test_naive_section_offsets_break_the_resolvability_check" in required


def test_a_full_passing_run_is_accepted(
    tmp_path: Path, required: frozenset[str]
) -> None:
    report = _write(tmp_path, [(name, "passed") for name in sorted(required)])
    assert "OK" in check(report)


def test_the_permitted_skip_is_accepted(
    tmp_path: Path, required: frozenset[str]
) -> None:
    """Exactly the one #73-dependent test, and only it."""
    assert len(PERMITTED_SKIPS) == 1
    permitted = next(iter(PERMITTED_SKIPS))
    assert permitted in required
    cases = [
        (name, "skipped" if name == permitted else "passed") for name in sorted(required)
    ]
    assert "OK" in check(_write(tmp_path, cases))


def test_a_whole_suite_skipped_is_rejected(
    tmp_path: Path, required: frozenset[str]
) -> None:
    """The KG-1 failure: the `migration` extra did not resolve.

    Every test is collected and skipped by `importorskip`. Total is non-zero,
    failures are zero, and the job is green over nothing.
    """
    report = _write(tmp_path, [(name, "skipped") for name in sorted(required)])
    with pytest.raises(SuiteGateError, match="did not pass"):
        check(report)


def test_a_deleted_test_is_rejected(tmp_path: Path, required: frozenset[str]) -> None:
    """One check quietly removed from collection while the rest pass."""
    cases = [
        (name, "passed")
        for name in sorted(required)
        if name != "test_chunk_offsets_resolve_to_the_chunk_text"
    ]
    with pytest.raises(SuiteGateError, match="never ran"):
        check(_write(tmp_path, cases))


def test_a_failing_test_is_rejected(tmp_path: Path, required: frozenset[str]) -> None:
    cases = [
        (name, "failed" if name == "test_every_evidence_ref_resolves" else "passed")
        for name in sorted(required)
    ]
    with pytest.raises(SuiteGateError, match="did not pass"):
        check(_write(tmp_path, cases))


def test_an_errored_test_is_rejected(tmp_path: Path, required: frozenset[str]) -> None:
    cases = [
        (name, "error" if name == "test_the_run_produced_candidates" else "passed")
        for name in sorted(required)
    ]
    with pytest.raises(SuiteGateError, match="did not pass"):
        check(_write(tmp_path, cases))


def test_an_empty_report_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SuiteGateError, match="zero test cases"):
        check(_write(tmp_path, []))


def test_a_missing_report_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SuiteGateError, match="no junit report"):
        check(tmp_path / "absent.xml")


def test_a_missing_required_module_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one transcribed thing, and the one thing derivation cannot catch.

    Deleting a module removes its tests from the discovered set *and* from the
    junit report, so both sides agree and nothing looks wrong. The filename
    list is what notices, and this is the proof that it does.
    """
    from . import suite_gate

    monkeypatch.setattr(
        suite_gate, "REQUIRED_MODULES", (*REQUIRED_MODULES, "test_deleted_module.py")
    )
    with pytest.raises(SuiteGateError, match="required test module is missing"):
        suite_gate.expected_tests()


def test_a_test_nested_under_a_false_condition_is_still_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's bypass: `if False:` removed a test from both sides.

    The first version of `expected_tests` walked `tree.body` only. Indenting a
    required test under `if False:` removed it from the required set *and* from
    execution, so the gate printed `OK: 93 required tests ran and passed` and
    exited 0 while the test it was guarding had silently stopped running.
    `ast.walk` is the fix: the test is still required, so its absence from the
    report is now a "never ran" failure.
    """
    from . import suite_gate

    module = tmp_path / "test_nested.py"
    module.write_text(
        "def test_visible() -> None:\n"
        "    pass\n"
        "\n"
        "if False:\n"
        "    def test_hidden_by_a_false_condition() -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(suite_gate, "HERE", tmp_path)
    monkeypatch.setattr(suite_gate, "REQUIRED_MODULES", ("test_nested.py",))

    required = suite_gate.expected_tests()
    assert required["test_nested"] == {
        "test_visible",
        "test_hidden_by_a_false_condition",
    }, "a test nested under `if False:` fell out of the required set"

    # ...and the gate now fails over a report that only ran the visible one.
    monkeypatch.setattr(suite_gate, "MINIMUM_REQUIRED_TESTS", 0)
    report = _write(tmp_path, [("test_visible", "passed")])
    with pytest.raises(SuiteGateError, match="never ran"):
        suite_gate.check(report)


def test_a_shrinking_required_set_trips_the_pinned_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ast.walk` moves the attack; the floor closes it.

    Deleting a required test outright still removes it from both the discovered
    set and the report, so both sides agree and nothing looks wrong. The floor
    is the only value in this gate that does not shrink with the suite.
    """
    from . import suite_gate

    module = tmp_path / "test_tiny.py"
    module.write_text("def test_one() -> None:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(suite_gate, "HERE", tmp_path)
    monkeypatch.setattr(suite_gate, "REQUIRED_MODULES", ("test_tiny.py",))

    report = _write(tmp_path, [("test_one", "passed")])
    with pytest.raises(SuiteGateError, match="below the pinned floor"):
        suite_gate.check(report)


def test_the_floor_is_below_the_suite_and_not_trivially_satisfied(
    required: frozenset[str],
) -> None:
    """The floor has to bind: at or just under today's count, never at zero.

    A floor of 0 would pass over any suite at all, which is the same defect one
    level up.
    """
    assert MINIMUM_REQUIRED_TESTS > 0
    assert MINIMUM_REQUIRED_TESTS <= len(required)
    assert len(required) - MINIMUM_REQUIRED_TESTS < 25, (
        f"the floor ({MINIMUM_REQUIRED_TESTS}) has drifted far below the suite "
        f"({len(required)}); raise it so it still binds"
    )


def test_every_required_module_exists_today() -> None:
    """...and the list is accurate right now, not only enforceable."""
    assert expected_tests().keys() == {name[: -len(".py")] for name in REQUIRED_MODULES}
