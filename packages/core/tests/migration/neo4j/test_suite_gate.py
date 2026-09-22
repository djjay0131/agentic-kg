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


#: The three tests kg_contracts 2.0.0 (ADR-0025) added to the shared suite.
#: Named, not counted: this module's own thesis is that counting is not
#: identification, and a bare ``len(names) == 10`` would pass just as well if
#: upstream had added three unrelated tests and dropped these.
ADR_0025_TESTS = frozenset(
    {
        "test_revoked_assertions_hidden_by_default_visible_with_flag",
        "test_include_superseded_and_include_revoked_are_independent",
        "test_revoke_identity_hides_entity_and_preserves_creation_epoch",
    }
)


def test_expected_names_come_from_upstream_and_are_non_empty() -> None:
    """Obligation 1 and 3: derived from upstream, and the set is not empty."""
    names = expected_contract_tests()
    assert "test_snapshot_read_at_old_epoch_hides_later_records" in names
    assert len(names) == 10


def test_the_revocation_tests_the_new_pin_added_are_now_required() -> None:
    """The gate's stated payoff, collected on the 0.3.0 re-pin.

    ``suite_gate`` derives its expectations from upstream precisely so that "an
    added upstream test becomes a *requirement* here the moment the pin moves".
    kg_contracts 2.0.0 added three. This asserts the derivation actually picked
    them up — if a future pin drops ``include_revoked`` from the suite, this
    goes red and says which name vanished, rather than the adapter quietly
    ceasing to be tested for it.
    """
    missing = ADR_0025_TESTS - expected_contract_tests()
    assert not missing, f"upstream no longer publishes: {sorted(missing)}"


def test_positive_control_passes(tmp_path) -> None:
    expected = expected_contract_tests()
    summary = check(_write(tmp_path, _contract_cases() + _filler(53)))
    assert f"{len(expected)}/{len(expected)} shared contract tests passed" in summary
    assert f"{len(expected) + 53} tests" in summary


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
    """All-but-one is not the suite."""
    cases = _contract_cases()[:-1]
    with pytest.raises(SuiteGateError, match=f"closest class ran {len(cases)}"):
        check(_write(tmp_path, cases + _filler(53)))


def test_dropping_only_the_new_revocation_tests_is_rejected(tmp_path) -> None:
    """The specific regression the re-pin makes possible.

    An adapter that never learned ``include_revoked`` would run the seven tests
    it used to run and skip the three it does not implement. That is the shape
    the old gate passed; here it must be rejected, and the rejection must name
    the tests that are missing rather than only a count.
    """
    kept = [c for c in _contract_cases() if c[1] not in ADR_0025_TESTS]
    assert len(kept) == len(_contract_cases()) - len(ADR_0025_TESTS)
    with pytest.raises(SuiteGateError) as excinfo:
        check(_write(tmp_path, kept + _filler(53)))
    message = str(excinfo.value)
    assert f"closest class ran {len(kept)}" in message
    for name in ADR_0025_TESTS:
        assert name in message


def test_an_empty_report_is_rejected(tmp_path) -> None:
    with pytest.raises(SuiteGateError, match="no tests at all"):
        check(_write(tmp_path, []))


def test_the_contract_tests_split_across_two_classes_is_rejected(tmp_path) -> None:
    """Half here, half there is not one store passing the suite.

    Two adapters each passing part of it would otherwise look like one adapter
    passing all of it.
    """
    names = sorted(expected_contract_tests())
    # Split unevenly so the "closest class" the gate reports is unambiguous;
    # an even split would make the reported number a tie-break artefact.
    head, tail = names[:-2], names[-2:]
    split = [(CONTRACT_CLASS, n, "passed") for n in head]
    split += [("other.TestSomethingElse", n, "passed") for n in tail]
    with pytest.raises(SuiteGateError, match=f"closest class ran {len(head)}"):
        check(_write(tmp_path, split))
