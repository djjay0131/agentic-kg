"""The CI gate is itself falsifiable (§9.0 obligation 5).

A gate that cannot go red is not a gate. Each attack that defeated the previous
``tests > 0 and skipped == 0`` version is replayed here against doctored junit
XML, and asserted to be rejected:

* the conformance module deleted — the exact attack a reviewer used, which the
  old gate passed while reporting "53 tests, 0 skipped";
* the subclass renamed out of pytest's collection pattern;
* one contract test overridden with a stub that skips;
* one contract test failing;
* only *some* of the contract tests present.

Plus the positive control, so the gate is not simply rejecting everything.

No Neo4j and no container: this exercises the gate, not the adapter.
"""

from __future__ import annotations

import pytest

from .suite_gate import SuiteGateError, check, expected_contract_tests

CONTRACT_CLASS = (
    "packages.core.tests.migration.neo4j.test_contract_conformance."
    "TestNeo4jGraphMutationStoreContract"
)
OTHER_CLASS = "packages.core.tests.migration.neo4j.test_read_surface"


def _xml(cases: list[tuple[str, str, str]]) -> str:
    """Build a junit document from ``(classname, name, outcome)`` triples."""
    body = []
    for classname, name, outcome in cases:
        inner = {
            "passed": "",
            "skipped": '<skipped message="s"/>',
            "failed": '<failure message="f"/>',
        }[outcome]
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="pytest" tests="{len(cases)}">{"".join(body)}</testsuite>'
        "</testsuites>"
    )


def _write(tmp_path, cases):
    path = tmp_path / "junit.xml"
    path.write_text(_xml(cases), encoding="utf-8")
    return path


def _contract_cases(outcome: str = "passed"):
    return [(CONTRACT_CLASS, name, outcome) for name in sorted(expected_contract_tests())]


def _filler(count: int, outcome: str = "passed"):
    return [(OTHER_CLASS, f"test_other_{i}", outcome) for i in range(count)]


def test_expected_names_come_from_upstream_and_are_non_empty() -> None:
    """Obligation 1 and 3: derived from upstream, and the set is not empty."""
    names = expected_contract_tests()
    assert len(names) == 7
    assert "test_snapshot_read_at_old_epoch_hides_later_records" in names


def test_positive_control_passes(tmp_path) -> None:
    summary = check(_write(tmp_path, _contract_cases() + _filler(53)))
    assert "7/7 shared contract tests passed" in summary
    assert "60 tests" in summary


def test_deleting_the_conformance_module_is_rejected(tmp_path) -> None:
    """The reviewer's attack: 53 tests, 0 skipped, suite gone. Must go red.

    This is the case the previous gate passed.
    """
    path = _write(tmp_path, _filler(53))
    with pytest.raises(SuiteGateError, match="no class ran the shared"):
        check(path)


def test_renaming_the_subclass_out_of_collection_is_rejected(tmp_path) -> None:
    """A class pytest no longer collects contributes no testcases at all."""
    path = _write(tmp_path, _filler(53))
    with pytest.raises(SuiteGateError, match="deleted, renamed out of pytest"):
        check(path)


def test_renaming_the_class_but_still_running_the_suite_is_accepted(tmp_path) -> None:
    """A *legitimate* rename must not be a false positive.

    The gate identifies the suite by the seven upstream method names under one
    class, not by the class's own name, so renaming the subclass while it still
    runs is fine.
    """
    renamed = [
        ("some.other.module.TestRenamed", name, "passed") for name in expected_contract_tests()
    ]
    summary = check(_write(tmp_path, renamed + _filler(10)))
    assert "TestRenamed" in summary


def test_a_skipped_contract_test_is_rejected(tmp_path) -> None:
    cases = _contract_cases()
    cases[0] = (cases[0][0], cases[0][1], "skipped")
    with pytest.raises(SuiteGateError, match="skipped"):
        check(_write(tmp_path, cases + _filler(53)))


def test_a_failing_contract_test_is_rejected(tmp_path) -> None:
    cases = _contract_cases()
    cases[2] = (cases[2][0], cases[2][1], "failed")
    with pytest.raises(SuiteGateError, match="failed"):
        check(_write(tmp_path, cases + _filler(53)))


def test_a_partial_suite_is_rejected(tmp_path) -> None:
    """Six of seven is not the suite."""
    cases = _contract_cases()[:-1]
    with pytest.raises(SuiteGateError, match="closest class ran 6"):
        check(_write(tmp_path, cases + _filler(53)))


def test_an_empty_report_is_rejected(tmp_path) -> None:
    with pytest.raises(SuiteGateError, match="no tests at all"):
        check(_write(tmp_path, []))


def test_the_contract_tests_split_across_two_classes_is_rejected(tmp_path) -> None:
    """Half here, half there is not one store passing the suite.

    Two adapters each passing part of it would otherwise look like one adapter
    passing all of it.
    """
    names = sorted(expected_contract_tests())
    split = [(CONTRACT_CLASS, n, "passed") for n in names[:4]]
    split += [("other.TestSomethingElse", n, "passed") for n in names[4:]]
    with pytest.raises(SuiteGateError, match="closest class ran 4"):
        check(_write(tmp_path, split))
