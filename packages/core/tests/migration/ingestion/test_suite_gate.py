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
    qualified_tests,
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
    """A junit document with `(qualified_or_bare_name, outcome)` cases.

    A `module::test` key is split back into the `classname`/`name` pair pytest
    actually writes, so these fixtures exercise the same parsing path as a real
    report rather than a convenient shorthand.
    """
    body = []
    for key, outcome in cases:
        module, _, name = key.rpartition("::")
        classname = (
            f"packages.core.tests.migration.ingestion.{module}" if module else "c"
        )
        inner = "" if outcome == "passed" else f"<{_OUTCOME_TAG[outcome]} message='x'/>"
        body.append(
            f"<testcase classname='{classname}' name='{name}'>{inner}</testcase>"
        )
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
    """The qualified `module::test` keys — the set the gate actually counts."""
    return frozenset(qualified_tests(expected_tests()))


def test_the_expected_set_is_derived_and_non_empty(required: frozenset[str]) -> None:
    """A gate that expects nothing passes over nothing."""
    assert len(REQUIRED_MODULES) >= 9
    assert len(required) >= 60, len(required)
    assert "test_documents::test_chunk_offsets_resolve_to_the_chunk_text" in required
    assert (
        "test_shadow_is_falsifiable::"
        "test_naive_section_offsets_break_the_resolvability_check"
    ) in required


def test_a_full_passing_run_is_accepted(
    tmp_path: Path, required: frozenset[str]
) -> None:
    report = _write(tmp_path, [(name, "passed") for name in sorted(required)])
    assert "OK" in check(report)


def test_no_skip_is_permitted(tmp_path: Path, required: frozenset[str]) -> None:
    """The allowlist is empty, so any skip in a required test fails the gate.

    It held one entry while PR #73 was unmerged; #73 landed, the arm test now
    runs against the real `ArmPaper`/`ArmEntity`, and the entry was removed
    after the rebase. Empty is the strongest setting and matches the
    canonical-adapter gate next door.

    Asserted behaviourally as well as by inspecting the dict: an empty
    allowlist and a broken skip-check look identical from a passing run, so a
    skip is actually fed to `check` and required to be rejected.
    """
    assert PERMITTED_SKIPS == {}, (
        f"the allowlist is no longer empty ({sorted(PERMITTED_SKIPS)}); if that "
        f"is deliberate, this test should assert the new entry and its reason"
    )
    victim = "test_documents::test_chunk_offsets_resolve_to_the_chunk_text"
    assert victim in required
    cases = [
        (key, "skipped" if key == victim else "passed") for key in sorted(required)
    ]
    with pytest.raises(SuiteGateError, match="did not pass"):
        check(_write(tmp_path, cases))


def test_an_allowlisted_skip_would_be_accepted(
    tmp_path: Path, required: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """...and the allowlist still works, so emptying it is a choice not a break.

    Without this, `PERMITTED_SKIPS` could have stopped being consulted at all
    and every test here would still pass -- the mechanism would be dead code
    wearing a docstring.
    """
    from . import suite_gate

    victim = "test_documents::test_chunk_offsets_resolve_to_the_chunk_text"
    bare = victim.split("::", 1)[-1]
    monkeypatch.setattr(suite_gate, "PERMITTED_SKIPS", {bare: "a stated reason"})
    cases = [
        (key, "skipped" if key == victim else "passed") for key in sorted(required)
    ]
    assert "OK" in suite_gate.check(_write(tmp_path, cases))


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
        (key, "passed")
        for key in sorted(required)
        if key != "test_documents::test_chunk_offsets_resolve_to_the_chunk_text"
    ]
    with pytest.raises(SuiteGateError, match="never ran"):
        check(_write(tmp_path, cases))


def test_a_failing_test_is_rejected(tmp_path: Path, required: frozenset[str]) -> None:
    cases = [
        (
            key,
            "failed"
            if key == "test_shadow_run::test_every_evidence_ref_resolves"
            else "passed",
        )
        for key in sorted(required)
    ]
    with pytest.raises(SuiteGateError, match="did not pass"):
        check(_write(tmp_path, cases))


def test_an_errored_test_is_rejected(tmp_path: Path, required: frozenset[str]) -> None:
    cases = [
        (
            key,
            "error"
            if key == "test_shadow_run::test_the_run_produced_candidates"
            else "passed",
        )
        for key in sorted(required)
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


def test_a_name_shared_by_two_modules_counts_twice() -> None:
    """The second round's finding, and the reason keys are qualified.

    `test_the_package_has_modules_to_check` is defined in both
    `test_config_injection.py` and `test_isolation.py`. Under bare-name keying
    the union held 104 entries while the multiset held 105, the gate required
    the union and compared the floor against the multiset, and the two numbers
    were never the same quantity. This asserts the collision is real (so the
    test is not hypothetical) and that both copies survive qualification.
    """
    required = expected_tests()
    owners = [
        module
        for module, names in required.items()
        if "test_the_package_has_modules_to_check" in names
    ]
    assert len(owners) == 2, (
        f"the duplicated name this test is about is no longer duplicated "
        f"({owners}); pick another or delete this test rather than letting it "
        f"quietly stop checking anything"
    )
    keys = qualified_tests(required)
    for module in owners:
        assert f"{module}::test_the_package_has_modules_to_check" in keys
    # The flattened form loses one of them. That loss was the bug.
    flattened = {name for names in required.values() for name in names}
    assert len(keys) == len(flattened) + 1


def test_deleting_a_duplicated_test_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer deleted one and the gate's output did not change at all.

    Two modules define `test_shared`. Under bare-name keying, removing it from
    one module left the other's copy in the union, the required count was
    unchanged, and `check` passed a report that never ran it. Qualified keys
    make the deletion a `never ran` failure.
    """
    from . import suite_gate

    (tmp_path / "test_a.py").write_text(
        "def test_shared() -> None:\n    pass\n"
        "def test_only_a() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_b.py").write_text(
        "def test_shared() -> None:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(suite_gate, "HERE", tmp_path)
    monkeypatch.setattr(suite_gate, "REQUIRED_MODULES", ("test_a.py", "test_b.py"))
    monkeypatch.setattr(suite_gate, "MINIMUM_REQUIRED_TESTS", 0)

    full = [
        ("test_a::test_shared", "passed"),
        ("test_a::test_only_a", "passed"),
        ("test_b::test_shared", "passed"),
    ]
    assert "3 required tests" in suite_gate.check(_write(tmp_path, full))

    # test_b's copy silently stops running; the other module still has one.
    without_b = [c for c in full if c[0] != "test_b::test_shared"]
    with pytest.raises(SuiteGateError, match="never ran"):
        suite_gate.check(_write(tmp_path, without_b))


def test_the_floor_compares_the_number_the_gate_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One quantity, used for the floor, the requirement and the report.

    The defect: counting the multiset for the floor and requiring the union.
    With a duplicated name the two differ, so a suite could sit below the floor
    while the floor believed it was above it — and the printed number matched
    neither comparison.
    """
    from . import suite_gate

    (tmp_path / "test_a.py").write_text(
        "def test_shared() -> None:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "test_b.py").write_text(
        "def test_shared() -> None:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(suite_gate, "HERE", tmp_path)
    monkeypatch.setattr(suite_gate, "REQUIRED_MODULES", ("test_a.py", "test_b.py"))
    monkeypatch.setattr(suite_gate, "MINIMUM_REQUIRED_TESTS", 2)

    cases = [("test_a::test_shared", "passed"), ("test_b::test_shared", "passed")]
    message = suite_gate.check(_write(tmp_path, cases))
    assert "2 required tests" in message
    assert "floor 2" in message

    monkeypatch.setattr(suite_gate, "MINIMUM_REQUIRED_TESTS", 3)
    with pytest.raises(SuiteGateError, match="the suite requires 2 tests"):
        suite_gate.check(_write(tmp_path, cases))


def test_every_required_module_exists_today() -> None:
    """...and the list is accurate right now, not only enforceable."""
    assert expected_tests().keys() == {name[: -len(".py")] for name in REQUIRED_MODULES}
