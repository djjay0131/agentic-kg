"""The anti-vacuity guard, and the proof that it fires.

This programme has produced eleven vacuous checks. The dominant shape is
"compare two results that are both empty" — and it is the *natural* shape for a
compatibility suite, because eight of this application's read paths traverse
``(:Problem)-[:BELONGS_TO]->(:Topic)``, an edge no automated writer produces.
Run those against legacy and against a projection and you get ``[] == []``: a
green test that has exercised nothing.

The guard makes that unrepresentable rather than discouraged. It is proved here
four ways, in increasing strength:

1. **Pure** — :func:`require_non_vacuous` raises on an empty result, and
   :func:`assert_parity` raises on two empty results *instead of* reporting
   parity. A naive ``left.rows == right.rows`` is shown to pass the same input,
   so the guard is demonstrably stronger than the check it replaces rather than
   equivalent to it.
1b. **Against a row of NULLs** — the hole review found, and the more
   interesting one. ``OPTIONAL MATCH (p)-[r]-(neighbor)`` fabricates exactly
   one all-NULL row whenever the anchor exists, so emptiness measured by *row
   count* passes it and parity is then reported over a row carrying no
   information at all. Emptiness is now measured by informative rows, and
   ``CypherProbe.informative`` is a required field so the next ``OPTIONAL
   MATCH`` cannot reintroduce the hole by omission.
2. **Against a real empty graph** — the whole probe set, unmodified, is pointed
   at a genuinely empty Neo4j. Every probe comes back empty and every one is
   shown to raise. This is the leg that catches a guard which only works on
   hand-built ``ProbeResult`` objects. A second variant puts a single edge-less
   ``:Problem`` in that graph, which is what makes the fabricated row appear.
3. **Against the populated graph** — the same probes, same guard, and the guard
   must *not* fire. Without this, a guard hard-wired to ``raise`` would pass
   every leg above perfectly.

Note what legs 2 and 3 cannot do: both are whole-suite **row-count** checks, so
neither noticed H-1. The all-NULL case is asserted against the single probe
that exhibits it, deliberately.

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

EMPTY = ProbeResult(probe_id="example", surface="left", rows=(), informative=("id",))
OTHER_EMPTY = ProbeResult(probe_id="example", surface="right", rows=(), informative=("id",))
POPULATED = ProbeResult(
    probe_id="example", surface="left", rows=({"id": "x"},), informative=("id",)
)
POPULATED_COPY = ProbeResult(
    probe_id="example", surface="right", rows=({"id": "x"},), informative=("id",)
)
DIFFERENT = ProbeResult(
    probe_id="example", surface="right", rows=({"id": "y"},), informative=("id",)
)


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


def _compare_first(baseline: ProbeResult, observed: ProbeResult) -> None:
    """The anti-pattern, written out, so it can be run rather than described.

    This is ``assert_parity`` with the two steps transposed: compare, then
    complain about emptiness. On two empty sides it reports parity; on one
    empty side it reports a *divergence*, which is the wrong diagnosis and the
    wrong exception type.
    """
    if baseline.rows != observed.rows:
        raise AssertionError("diverged")
    require_non_vacuous(baseline)
    require_non_vacuous(observed)


def test_the_guard_checks_emptiness_before_equality() -> None:
    """Order matters, and the assertion actually discriminates on it.

    An earlier version of this test used two empty sides and asserted
    VacuousProbe. That passes against ``_compare_first`` too: ``() == ()`` so
    the comparison falls through and the emptiness check raises anyway. It
    described the right property and tested nothing.

    A *single* empty side separates them. Guard-first raises VacuousProbe;
    compare-first raises a plain AssertionError reporting a divergence that is
    not the real problem. Both halves are asserted here, so the test fails if
    the ordering is ever transposed.
    """
    with pytest.raises(VacuousProbe) as excinfo:
        assert_parity(POPULATED, OTHER_EMPTY)
    assert type(excinfo.value) is VacuousProbe
    assert "returned no rows" in str(excinfo.value)

    with pytest.raises(AssertionError) as wrong:
        _compare_first(POPULATED, OTHER_EMPTY)
    assert not isinstance(wrong.value, VacuousProbe), (
        "the compare-first anti-pattern no longer behaves differently from the "
        "guard, so this test has stopped discriminating between them"
    )
    assert "diverged" in str(wrong.value)


# ---------------------------------------------------------------------------
# 1b. H-1: a row of NULLs is non-empty and information-free
# ---------------------------------------------------------------------------
#
# `OPTIONAL MATCH (p)-[r]-(neighbor)` manufactures exactly one all-NULL row
# whenever the anchor node exists and the optional pattern matches nothing.
# A guard that measures emptiness by row count passes it, and `assert_parity`
# then reports parity over a row that says nothing about the read path -- on
# `api.graph.node_neighbourhood`, the widest leak surface in the application.
#
# The fix is structural, not per-probe: `CypherProbe.informative` is a required
# field, so a future OPTIONAL MATCH probe cannot reintroduce the hole by
# omitting it. There is nothing to omit.

ALL_NULL = ProbeResult(
    probe_id="api.graph.node_neighbourhood",
    surface="legacy",
    rows=({"rel_type": None, "neighbour_labels": None},),
    informative=("rel_type", "neighbour_labels"),
)
ALL_NULL_OTHER = ProbeResult(
    probe_id="api.graph.node_neighbourhood",
    surface="projection",
    rows=({"rel_type": None, "neighbour_labels": None},),
    informative=("rel_type", "neighbour_labels"),
)


def test_a_row_of_nulls_is_not_empty_by_row_count() -> None:
    """The precondition: this really is the shape a row-count guard misses."""
    assert len(ALL_NULL.rows) == 1
    assert ALL_NULL.rows != ()


def test_the_guard_rejects_an_all_null_row() -> None:
    with pytest.raises(VacuousProbe) as excinfo:
        require_non_vacuous(ALL_NULL)
    assert "none of them informative" in str(excinfo.value)


def test_parity_over_two_all_null_rows_is_refused() -> None:
    """The end-to-end H-1 scenario: identical all-NULL rows on both surfaces."""
    with pytest.raises(VacuousProbe):
        assert_parity(ALL_NULL, ALL_NULL_OTHER)


def test_a_partially_null_row_does_not_rescue_the_result() -> None:
    """Every declared column must be non-NULL, not merely one of them.

    `OPTIONAL MATCH` nulls the whole optional half at once, but a future probe
    could null one column and not another, and a row missing half its declared
    evidence is still not evidence.
    """
    half = ProbeResult(
        probe_id="x",
        surface="legacy",
        rows=({"rel_type": "EXTENDS", "neighbour_labels": None},),
        informative=("rel_type", "neighbour_labels"),
    )
    with pytest.raises(VacuousProbe):
        require_non_vacuous(half)


def test_a_genuinely_populated_row_still_passes() -> None:
    """The guard must not have become a blanket refusal of this probe."""
    real = ProbeResult(
        probe_id="api.graph.node_neighbourhood",
        surface="legacy",
        rows=({"rel_type": "EXTENDS", "neighbour_labels": ["Problem"]},),
        informative=("rel_type", "neighbour_labels"),
    )
    assert require_non_vacuous(real) is real


def test_a_probe_result_cannot_be_built_without_informative_columns() -> None:
    """V-1: the structural half has to hold for ProbeResult too, not just CypherProbe.

    ``informative`` defaulted to ``()`` on this type, which silently degraded
    the guard to a row count for any hand-built result -- and the baseline side
    of the central parity assertion *is* hand-built, from JSON. The guard was
    therefore armed on the observed side and disarmed on the baseline side of
    the one comparison this harness exists to make.
    """
    with pytest.raises(ValueError, match="no informative columns"):
        ProbeResult(probe_id="x", surface="baseline", rows=({"id": "a"},), informative=())


def test_the_baseline_side_of_a_parity_check_is_armed() -> None:
    """The V-1 scenario end to end: two all-NULL sides must not report parity.

    Before the fix this returned normally -- the observed side raised nothing
    because the baseline side had no columns to check and the rows compared
    equal.
    """
    observed = ProbeResult(
        probe_id="api.graph.node_neighbourhood",
        surface="legacy",
        rows=({"rel_type": None, "neighbour_labels": None},),
        informative=("rel_type", "neighbour_labels"),
    )
    baseline = ProbeResult(
        probe_id="api.graph.node_neighbourhood",
        surface="baseline",
        rows=({"rel_type": None, "neighbour_labels": None},),
        informative=("rel_type", "neighbour_labels"),
    )
    with pytest.raises(VacuousProbe):
        assert_parity(baseline, observed)


def test_zero_and_empty_string_count_as_informative() -> None:
    """V-2, decided deliberately: NULL is the failure mode, not falsiness.

    ``0`` is a real count, ``False`` is a real ``is_canonical``, ``""`` is a
    real (if odd) title, and ``[]`` is a real empty list. ``OPTIONAL MATCH``
    produces NULL, and so does a dropped column; truthiness would reject
    correct results instead. Pinned so it is not "tidied" into ``if not
    row.get(c)`` later.
    """
    falsy = ProbeResult(
        probe_id="x",
        surface="legacy",
        rows=({"count": 0, "is_canonical": False, "title": "", "labels": []},),
        informative=("count", "is_canonical", "title", "labels"),
    )
    assert require_non_vacuous(falsy) is falsy


def test_every_probe_declares_what_makes_its_rows_informative() -> None:
    """The structural half of the H-1 fix, asserted.

    `informative` has no default, so this cannot currently fail -- which is the
    point. The test pins the property so that adding a default (the obvious
    future convenience) is caught here rather than by the next reviewer.
    """
    assert CYPHER_PROBES
    undeclared = sorted(p.id for p in CYPHER_PROBES if not p.informative)
    assert undeclared == [], f"probes with no informative columns: {undeclared}"


def test_declared_informative_columns_are_actually_returned() -> None:
    """A typo'd column name would be NULL on every row and fail everything.

    The opposite failure to H-1 and just as damaging: `informative=("rel_typo",)`
    makes the guard reject perfectly good results forever.
    """
    offenders = []
    for probe in CYPHER_PROBES:
        for column in probe.informative:
            if f" AS {column}" not in probe.cypher:
                offenders.append(f"{probe.id}: {column}")
    assert offenders == [], f"informative columns not returned by the query: {offenders}"


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
    cited_doi="10.0/no-such-paper",
    model_id="no-such-model",
    method_id="no-such-method",
    level="no-such-level",
    token="no-such-token",
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
def test_the_guard_fires_on_a_graph_holding_only_a_bare_anchor_node(
    empty_surface: Any, empty_graph_driver: Any
) -> None:
    """H-1's live reproduction, on unmodified probes.

    A graph containing one edge-less ``:Problem`` and nothing else. Six probes
    return rows — the label counts, and ``api.graph.node_neighbourhood``'s
    fabricated all-NULL row — so a row-count guard passes them and a two-run
    diff reports parity over all six. Every one must now be refused except the
    ones that legitimately carry information (the bare label counts do: a count
    of 1 is a real fact about the graph).

    Cleans up after itself so the empty-graph fixture stays empty for the other
    tests in this module, which assert exactly that.
    """
    anchor = "GUARD_PROBE_bare_anchor"
    with empty_graph_driver.session() as session:
        session.run(
            "CREATE (p:Problem {id: $id, statement: $s, status: 'open'})",
            id=anchor,
            s="a bare anchor with no edges at all",
        )
    try:
        inputs = ProbeInputs(
            topic_id="no-such-topic",
            root_topic_id="no-such-topic",
            problem_id=anchor,
            paper_doi="10.0/no-such-paper",
            concept_id="no-such-concept",
            trace_id="no-such-trace",
            cited_doi="10.0/no-such-paper",
            model_id="no-such-model",
            method_id="no-such-method",
            level="no-such-level",
            token="no-such-token",
        )
        results = run_all_probes(empty_surface, inputs, token="unused")
        neighbourhood = results["api.graph.node_neighbourhood"]

        # The precondition: Neo4j really did fabricate a row.
        assert len(neighbourhood.rows) == 1, neighbourhood.rows
        assert all(value is None for value in neighbourhood.rows[0].values()), (
            f"expected the OPTIONAL MATCH all-NULL row, got {neighbourhood.rows!r}"
        )

        # ...and the guard refuses it, on both the single-sided and the
        # two-run-diff forms.
        with pytest.raises(VacuousProbe):
            require_non_vacuous(neighbourhood)
        again = run_all_probes(empty_surface, inputs, token="unused")
        with pytest.raises(VacuousProbe):
            assert_parity(neighbourhood, again["api.graph.node_neighbourhood"])
    finally:
        with empty_graph_driver.session() as session:
            session.run("MATCH (p:Problem {id: $id}) DETACH DELETE p", id=anchor)


@pytest.mark.integration
def test_the_guard_fires_when_the_fixture_anchor_loses_its_edges(
    legacy_surface: Any, neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """The second live reproduction: H-1 inside a fully populated graph.

    Strip the anchor Problem's edges and the neighbourhood probe degrades to
    the all-NULL row while every other probe stays healthy — so the failure is
    invisible to any whole-suite row-count check. Both standing coverage
    assertions in this module are row-count checks, which is why this one is
    written against the single probe.
    """
    anchor = compat_graph.problem_ids[0]
    with neo4j_repository.session() as session:
        session.run("MATCH (p:Problem {id: $id})-[r]-() DELETE r", id=anchor)

    results = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )
    neighbourhood = results["api.graph.node_neighbourhood"]
    assert neighbourhood.rows == ({"rel_type": None, "neighbour_labels": None},)
    with pytest.raises(VacuousProbe):
        require_non_vacuous(neighbourhood)


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
