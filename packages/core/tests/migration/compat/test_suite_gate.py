"""The compat gate is falsifiable: every way the suite can vanish is rejected.

A gate is a check like any other, and §9.0 obligation 5 applies to it hardest —
a gate that cannot fail is worse than no gate, because it is *reported* as
assurance. So each rejection path gets a doctored report and an assertion that
it goes red.

The case that motivated this file is the first one: the review fed
``neo4j/suite_gate.py`` a junit report with this entire harness absent and it
reported success, because it identifies seven shared contract tests by name and
treats everything else as uncounted background. That gate is not wrong — it is
scoped. This one covers the scope it leaves open.

Needs no database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .suite_gate import CompatGateError, check, expected_test_names


def _report(cases: list[tuple[str, str]]) -> str:
    """Build a junit XML body from (name, outcome) pairs."""
    body = []
    for name, outcome in cases:
        if outcome == "passed":
            body.append(f'<testcase classname="c" name="{name}"/>')
        elif outcome == "skipped":
            body.append(f'<testcase classname="c" name="{name}"><skipped/></testcase>')
        else:
            body.append(
                f'<testcase classname="c" name="{name}"><failure>boom</failure></testcase>'
            )
    return "<testsuites><testsuite>" + "".join(body) + "</testsuite></testsuites>"


def _write(tmp_path: Path, cases: list[tuple[str, str]]) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(_report(cases), encoding="utf-8")
    return path


def _all_passing() -> list[tuple[str, str]]:
    return [(name, "passed") for name in sorted(expected_test_names())]


# ---------------------------------------------------------------------------
# The expectation is real
# ---------------------------------------------------------------------------


def test_expected_names_are_derived_from_source_and_are_plentiful() -> None:
    """Obligation 1: the gate must know about a real, non-trivial suite."""
    names = expected_test_names()
    assert len(names) >= 50, f"gate expects only {len(names)} tests"
    # Spot-check that it found tests from more than one module by name shape.
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


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------


def test_a_complete_passing_report_is_accepted(tmp_path: Path) -> None:
    """Without this, every rejection below could be a gate that refuses all input."""
    summary = check(_write(tmp_path, _all_passing()))
    assert "compat harness verified" in summary


def test_parametrised_names_are_matched_on_their_stem(tmp_path: Path) -> None:
    """pytest reports ``test_x[param]``; the gate must not demand a bare name."""
    cases = [(f"{name}[case-1]", "passed") for name in sorted(expected_test_names())]
    assert "compat harness verified" in check(_write(tmp_path, cases))


# ---------------------------------------------------------------------------
# Every way the suite can vanish
# ---------------------------------------------------------------------------


def test_the_whole_harness_missing_is_rejected(tmp_path: Path) -> None:
    """The exact doctored report that the neighbouring gate accepted.

    A report full of other passing tests, with this harness entirely absent.
    """
    decoys = [(f"test_some_other_suite_case_{i}", "passed") for i in range(200)]
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, decoys))
    assert "did not run in full" in str(excinfo.value)


def test_one_deleted_module_is_rejected(tmp_path: Path) -> None:
    """Partial runs are the realistic failure -- an `-m` filter, a collection error."""
    cases = [c for c in _all_passing() if not c[0].startswith("test_the_guard")]
    assert len(cases) < len(_all_passing())
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, cases))


def test_a_skipped_compat_test_is_rejected(tmp_path: Path) -> None:
    """A skip is how a missing Docker or a missing extra turns green."""
    cases = _all_passing()
    cases[0] = (cases[0][0], "skipped")
    with pytest.raises(CompatGateError) as excinfo:
        check(_write(tmp_path, cases))
    assert "not passing" in str(excinfo.value)


def test_a_failing_compat_test_is_rejected(tmp_path: Path) -> None:
    cases = _all_passing()
    cases[-1] = (cases[-1][0], "failed")
    with pytest.raises(CompatGateError):
        check(_write(tmp_path, cases))


def test_one_failing_parametrisation_is_rejected(tmp_path: Path) -> None:
    """A test that passes for one param and fails for another has not passed."""
    name = sorted(expected_test_names())[0]
    cases = [(n, "passed") for n in sorted(expected_test_names())]
    cases.append((f"{name}[bad]", "failed"))
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
        expected_test_names(tmp_path / "nowhere")
    assert "no test modules found" in str(excinfo.value)
