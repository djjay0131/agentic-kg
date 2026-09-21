"""Candidate-ledger and canonical state must never reach an application read.

Mapping spec §4.2 wants two Neo4j databases so a projection rebuild physically
cannot touch canonical data, and records U-1: Neo4j Community supplies exactly
one. The adapter resolved that with a ``Canon__`` label prefix plus namespace
scoping — separation by convention. Convention is only as good as the assertion
that backs it, and the assertion that matters to *this* phase is the one from
the application's side: **can an API router or an agent see canonical or
provisional rows that happen to share the database?**

Two legs, and the second is the one that would catch a real regression.

**Structural.** The labels the canonical adapter writes are disjoint from every
label the application matches, checked against
``agentic_kg.migration.neo4j.schema.CANONICAL_LABELS`` — the constants the store
actually interpolates into its Cypher, not a copy of them here. If someone ever
named a canonical label ``Problem``, ``GET /api/stats``'s bare
``MATCH (p:Problem) RETURN count(p)`` would over-count on the day of the change.

**Behavioural.** Real canonical data is written into the *same database* as the
legacy fixture, through the real ``Neo4jCanonicalGraphStore``, and the entire
probe set is re-run. Every row must be byte-identical to the pre-write capture.
Then the same probes are pointed at a deliberately leaky node — a provisional
``:Problem`` wired to a real one — and must change, which is what makes the
"unchanged" result above evidence rather than an artefact of an insensitive
probe.

``kg_contracts`` is required (the opt-in ``migration`` extra). The
``migration-canonical-adapter`` CI job installs it and asserts it resolved, so
the skip below cannot go silently unnoticed there.
"""

from __future__ import annotations

from typing import Any

import pytest
from agentic_kg.migration.compat import labels_read, run_all_probes

from .conftest import FixtureGraph

pytest.importorskip(
    "kg_contracts",
    reason=(
        "the opt-in 'migration' extra is not installed; "
        "pip install './packages/core[migration]'"
    ),
)

# No module-level `integration` marker: the three structural tests below need no
# database and should run in the plain unit job too, where they are the cheapest
# possible guard on the label prefix. Only the two behavioural tests are marked.


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_no_canonical_label_collides_with_an_application_label() -> None:
    """Needs no database. Red the moment a canonical label loses its prefix."""
    from agentic_kg.migration.neo4j.schema import CANONICAL_LABELS

    application = labels_read()
    assert application, "the read-path inventory records no labels"
    assert CANONICAL_LABELS, "the canonical adapter declares no labels"
    collisions = sorted(set(CANONICAL_LABELS) & application)
    assert collisions == [], (
        f"canonical labels an application read path would match: {collisions}"
    )


def test_the_collision_check_can_fail() -> None:
    """Obligation 5 for the check above.

    A set-intersection assertion over two sets is exactly the shape that passes
    when one of them is accidentally empty. Here is the same computation with a
    colliding label, going red.
    """
    application = labels_read()
    assert "Problem" in application
    collisions = sorted({"Canon__Identity", "Problem"} & application)
    assert collisions == ["Problem"]


def test_every_canonical_label_is_prefixed() -> None:
    """The mechanism behind the disjointness, not just its current result.

    Disjointness could hold by luck. The prefix is the rule that keeps it
    holding for labels nobody has added yet.
    """
    from agentic_kg.migration.neo4j.schema import CANONICAL_LABELS

    unprefixed = [label for label in CANONICAL_LABELS if not label.startswith("Canon__")]
    assert unprefixed == []


# ---------------------------------------------------------------------------
# Behavioural
# ---------------------------------------------------------------------------


def _canonical_node_count(repo: Any) -> int:
    from agentic_kg.migration.neo4j.schema import CANONICAL_LABELS

    with repo.session() as session:
        return session.execute_read(
            lambda tx: tx.run(
                "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
                "RETURN count(n) AS n",
                labels=list(CANONICAL_LABELS),
            ).single()["n"]
        )


def _write_canonical_data(repo: Any, namespace: str) -> None:
    """Commit a small real curation batch through the real store."""
    from agentic_kg.migration.neo4j import Neo4jCanonicalGraphStore
    from kg_contracts.curation import CurationOperation, CurationOperationType
    from kg_contracts.stores import GraphMutationBatch
    from kg_contracts.testing.factories import make_assertion, make_entity

    store = Neo4jCanonicalGraphStore(repo.driver, namespace=namespace)
    store.ensure_schema()
    entity = make_entity(key="compat-co-residence")
    assertion = make_assertion(
        subject_identity=entity.identity_id, predicate="compat_probe"
    )
    result = store.apply(
        GraphMutationBatch(
            plan_id="pl_compat_coresidence",
            operations=(
                CurationOperation(
                    type=CurationOperationType.CREATE_IDENTITY,
                    payload=entity.model_dump(mode="json"),
                ),
                CurationOperation(
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload=assertion.model_dump(mode="json"),
                ),
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, (
        f"the canonical write did not commit ({result.error!r}), so nothing "
        f"co-resident was created and the test below would be vacuous"
    )


@pytest.mark.integration
def test_canonical_data_co_resident_in_one_database_is_invisible_to_every_probe(
    legacy_surface: Any, neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """The real question for a Community-edition deployment (spec U-1).

    Write canonical state into the same database as the application data, then
    re-run every application read. Nothing may move.
    """
    before = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )
    assert before, "no probes ran"
    assert all(r.rows for r in before.values()), (
        "a probe was already empty before the canonical write, so 'unchanged' "
        "would be uninformative for it"
    )

    assert _canonical_node_count(neo4j_repository) == 0
    _write_canonical_data(neo4j_repository, namespace=f"compat{compat_graph.token}")
    canonical_nodes = _canonical_node_count(neo4j_repository)
    assert canonical_nodes > 0, (
        "no canonical nodes were written, so this test proves nothing about "
        "co-residence"
    )

    after = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )
    drifted = {
        pid: (before[pid].rows, after[pid].rows)
        for pid in before
        if before[pid].rows != after[pid].rows
    }
    assert drifted == {}, (
        f"canonical state leaked into application reads: {sorted(drifted)}"
    )


@pytest.mark.integration
def test_a_leaky_node_would_have_been_caught(
    legacy_surface: Any, neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """Obligation 5 for the test above: the probes are sensitive to co-residence.

    "Nothing changed" is only evidence if something *could* have changed. A
    provisional row carrying an application label — the shape a ledger would
    take if it reused ``:Problem`` instead of prefixing — is written and wired
    to a real Problem, and the probes must notice. Two of them specifically:
    the bare ``MATCH (p:Problem)`` counts, and
    ``GET /api/graph/node/{id}``'s untyped ``OPTIONAL MATCH (p)-[r]-(neighbor)``,
    which renders whatever is adjacent regardless of label.
    """
    before = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )

    with neo4j_repository.session() as session:
        session.run(
            "MATCH (real:Problem {id: $real}) "
            "CREATE (leak:Problem {id: $leak, statement: $stmt, status: 'open', "
            "provisional: true}) "
            "CREATE (real)-[:EXTENDS]->(leak)",
            real=compat_graph.problem_ids[0],
            leak=f"{compat_graph.token}_leaked_candidate",
            stmt=f"{compat_graph.token} a provisional candidate that must not be read",
        )

    after = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )
    moved = sorted(pid for pid in before if before[pid].rows != after[pid].rows)
    assert "api.stats.by_status" in moved, (
        "the bare label count did not notice a co-resident :Problem node, so "
        "the co-residence test above cannot be trusted"
    )
    assert "api.graph.problem_relations" in moved
    assert "api.graph.node_neighbourhood" in moved, (
        "the untyped neighbourhood query did not notice an adjacent node -- "
        "this is the widest leak surface in the application and the probe must "
        "be able to see through it"
    )
