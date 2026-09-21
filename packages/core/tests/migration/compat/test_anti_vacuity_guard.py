"""The anti-vacuity guard, and the proof that it fires.

This programme has produced eleven vacuous checks. The dominant shape is
"compare two results that are both empty" — and it is the *natural* shape for a
compatibility suite, because eight of this application's read paths traverse
``(:Problem)-[:BELONGS_TO]->(:Topic)``, an edge no automated writer produces.
Run those against legacy and against a projection and you get ``[] == []``: a
green test that has exercised nothing.

The guard makes that unrepresentable rather than discouraged. It is proved here
three ways, in increasing strength:

1. **Pure** — :func:`require_non_vacuous` raises on an empty result, and
   :func:`assert_parity` raises on two empty results *instead of* reporting
   parity. A naive ``left.rows == right.rows`` is shown to pass the same input,
   so the guard is demonstrably stronger than the check it replaces rather than
   equivalent to it.
2. **Against a real empty graph** — the whole probe set, unmodified, is pointed
   at a genuinely empty Neo4j. Every probe comes back empty and every one is
   shown to raise. This is the leg that catches a guard which only works on
   hand-built ``ProbeResult`` objects.
3. **Against the populated graph** — the same probes, same guard, and the guard
   must *not* fire. Without this, a guard hard-wired to ``raise`` would pass
   legs 1 and 2 perfectly.

Legs 2 and 3 need Docker; they skip cleanly without it, and the
``migration-canonical-adapter`` CI job provides it.
"""

from __future__ import annotations

from typing import Any

import pytest
from agentic_kg.migration.compat import (
    CYPHER_PROBES,
    ProbeInputs,
    ProbeResult,
    VacuousProbe,
    assert_parity,
    require_non_vacuous,
    run_all_probes,
)

from .conftest import FixtureGraph

EMPTY = ProbeResult(probe_id="example", surface="left", rows=())
OTHER_EMPTY = ProbeResult(probe_id="example", surface="right", rows=())
POPULATED = ProbeResult(probe_id="example", surface="left", rows=({"id": "x"},))
POPULATED_COPY = ProbeResult(probe_id="example", surface="right", rows=({"id": "x"},))
DIFFERENT = ProbeResult(probe_id="example", surface="right", rows=({"id": "y"},))


# ---------------------------------------------------------------------------
# 1. Pure
# ---------------------------------------------------------------------------


def test_require_non_vacuous_raises_on_an_empty_result() -> None:
    with pytest.raises(VacuousProbe):
        require_non_vacuous(EMPTY)


def test_require_non_vacuous_passes_a_populated_result_through() -> None:
    """The guard must not be a blanket refusal -- it has to let real data by."""
    assert require_non_vacuous(POPULATED) is POPULATED


def test_assert_parity_refuses_two_empty_sides() -> None:
    """The headline case: `[] == []` must never be reported as parity."""
    with pytest.raises(VacuousProbe):
        assert_parity(EMPTY, OTHER_EMPTY)


def test_assert_parity_refuses_a_single_empty_side() -> None:
    """Both orders, because a one-sided guard is a coin flip."""
    with pytest.raises(VacuousProbe):
        assert_parity(EMPTY, POPULATED_COPY)
    with pytest.raises(VacuousProbe):
        assert_parity(POPULATED, OTHER_EMPTY)


def test_assert_parity_still_detects_a_real_divergence() -> None:
    """The guard must not swallow the failure it exists alongside."""
    with pytest.raises(AssertionError) as excinfo:
        assert_parity(POPULATED, DIFFERENT)
    assert not isinstance(excinfo.value, VacuousProbe)


def test_assert_parity_accepts_genuine_agreement() -> None:
    assert_parity(POPULATED, POPULATED_COPY)


def test_the_naive_check_the_guard_replaces_would_have_passed() -> None:
    """Obligation 5, stated as a comparison rather than a claim.

    The defect the guard exists to catch is a parity assertion written as a
    bare equality. Here is that assertion, on the exact input the guard
    rejects, passing. If `assert_parity` were ever weakened back into this, the
    test above turns red while this one stays green -- which is what makes the
    pair informative.
    """
    assert EMPTY.rows == OTHER_EMPTY.rows  # the vacuous pass, demonstrated


def test_the_guard_checks_emptiness_before_equality() -> None:
    """Order matters, and it is asserted rather than assumed.

    A guard written as "compare, then complain if empty" reports parity first
    and the complaint never reaches the caller. Two *different* empty-ish
    results -- same emptiness, different surfaces -- must raise VacuousProbe,
    not an equality error, which is only true if the emptiness check runs first.
    """
    with pytest.raises(VacuousProbe) as excinfo:
        assert_parity(EMPTY, OTHER_EMPTY)
    assert "returned no rows" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. Against a real empty graph
# ---------------------------------------------------------------------------

EMPTY_GRAPH_INPUTS = ProbeInputs(
    topic_id="no-such-topic",
    root_topic_id="no-such-topic",
    problem_id="no-such-problem",
    paper_doi="10.0/no-such-paper",
    concept_id="no-such-concept",
    trace_id="no-such-trace",
)


@pytest.mark.integration
def test_every_probe_returns_nothing_on_an_empty_graph(empty_surface: Any) -> None:
    """The precondition for the next test, asserted rather than assumed."""
    assert CYPHER_PROBES, "the probe set is empty - nothing would be proved below"
    results = run_all_probes(empty_surface, EMPTY_GRAPH_INPUTS, token="unused")
    assert len(results) == len(CYPHER_PROBES)
    non_empty = {pid: r.rows for pid, r in results.items() if r.rows}
    assert non_empty == {}, (
        "a probe found rows in a graph that has none; the empty-graph fixture "
        f"is not empty: {non_empty}"
    )


@pytest.mark.integration
def test_the_guard_fires_for_every_probe_on_an_empty_graph(empty_surface: Any) -> None:
    """The guard, unmodified, pointed at an empty graph. Every probe must raise.

    This is the proof the task asks for. A guard that only worked on
    hand-constructed ProbeResults, or that was applied to some probes and not
    others, fails here.
    """
    results = run_all_probes(empty_surface, EMPTY_GRAPH_INPUTS, token="unused")
    assert results, "no probes ran"
    survivors = []
    for probe_id, result in results.items():
        try:
            require_non_vacuous(result)
        except VacuousProbe:
            continue
        survivors.append(probe_id)
    assert survivors == [], (
        f"these probes passed the anti-vacuity guard against an EMPTY graph, "
        f"so any parity assertion built on them would be vacuous: {survivors}"
    )


@pytest.mark.integration
def test_parity_between_two_empty_graph_runs_is_refused(empty_surface: Any) -> None:
    """The end-to-end vacuous scenario, refused end to end.

    Two runs of the same probe set against the same empty graph is exactly what
    a naive "run the old code, run the new code, diff" harness does when both
    sides are empty. Every probe must refuse.
    """
    left = run_all_probes(empty_surface, EMPTY_GRAPH_INPUTS, token="unused")
    right = run_all_probes(empty_surface, EMPTY_GRAPH_INPUTS, token="unused")
    assert left.keys() == right.keys() and left
    for probe_id in left:
        with pytest.raises(VacuousProbe):
            assert_parity(left[probe_id], right[probe_id])


# ---------------------------------------------------------------------------
# 3. Against the populated graph — the guard must not fire
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_guard_does_not_fire_on_the_populated_fixture(
    legacy_surface: Any, compat_graph: FixtureGraph
) -> None:
    """A guard that always raises would have passed every test above.

    Every probe in the set must come back non-empty against the fixture graph.
    That is also the standing coverage assertion for the baseline: if a probe
    goes empty because the fixture stopped populating its read path, the
    baseline silently stops proving anything about it, and this is where that
    shows up.
    """
    results = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )
    assert results
    empty = sorted(pid for pid, r in results.items() if not r.rows)
    assert empty == [], (
        "these probes found nothing on the populated fixture, so the baseline "
        f"they contribute is vacuous: {empty}"
    )
    for result in results.values():
        require_non_vacuous(result)
