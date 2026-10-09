"""Pagination order is part of the contract, and denormalized counters set it.

Three of the application's ``ORDER BY`` keys are denormalized counters:
``rc.mention_count DESC, rc.name`` (``GET /api/concepts``),
``m.is_canonical DESC, m.usage_count DESC, m.name`` (``GET /api/models``) and
``m.usage_count DESC, m.name`` (``GET /api/methods``). Legacy increments them on
write and has **no reconciler at all** for ``Model.usage_count`` or
``Method.usage_count`` (mapping spec §5.1). The projection recomputes every
counter from the projected edges (§4.4).

So the question this module answers is narrow and answerable: *if the counters
are recomputed, does the page order move?* On a graph carrying the drift legacy
actually accumulates, yes — and that is a compatibility break the spec's
"all ten stay sortable" sentence does not by itself cover. Sortable is not the
same as sorted the same way.

Two assertions, in the order that makes them mean something:

1. **The ORDER BY output really is a function of the stored counter.** Proved by
   mutating one counter and watching the page move, with the edges untouched. A
   test that merely observed a stable order would pass against a query that
   ignored the counter entirely.
2. **Stored counters and edge degrees disagree on this fixture, and the two
   orders differ.** The drift is created the way legacy creates it — edges
   deleted with raw Cypher, counter never decremented, exactly
   ``re_ingestion.purge_paper_extraction``.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import FixtureGraph

pytestmark = pytest.mark.integration

#: GET /api/models, verbatim from routers/models.py:80-84 minus pagination.
MODELS_STORED_ORDER = (
    "MATCH (m:Model) WHERE m.id STARTS WITH $tok "
    "RETURN m.id AS id, m.usage_count AS usage_count "
    "ORDER BY m.is_canonical DESC, m.usage_count DESC, m.name"
)

#: The same page, ordered by what §4.4 defines the counter to be: the in-degree
#: of USES_MODEL. This is what the projection would sort on.
MODELS_DEGREE_ORDER = (
    "MATCH (m:Model) WHERE m.id STARTS WITH $tok "
    "OPTIONAL MATCH (:Paper)-[u:USES_MODEL]->(m) "
    "WITH m, count(u) AS degree "
    "RETURN m.id AS id, degree AS usage_count "
    "ORDER BY m.is_canonical DESC, degree DESC, m.name"
)

#: GET /api/concepts, verbatim from routers/concepts.py:69-73 minus pagination.
CONCEPTS_STORED_ORDER = (
    "MATCH (rc:ResearchConcept) WHERE rc.id STARTS WITH $tok "
    "RETURN rc.id AS id, rc.mention_count AS mention_count "
    "ORDER BY rc.mention_count DESC, rc.name"
)

CONCEPTS_DEGREE_ORDER = (
    "MATCH (rc:ResearchConcept) WHERE rc.id STARTS WITH $tok "
    "OPTIONAL MATCH ()-[i:INVOLVES_CONCEPT]->(rc) "
    "WITH rc, count(i) AS degree "
    "RETURN rc.id AS id, degree AS mention_count "
    "ORDER BY degree DESC, rc.name"
)


def _ids(surface: Any, cypher: str, token: str) -> list[str]:
    return [row["id"] for row in surface.run(cypher, tok=token)]


# ---------------------------------------------------------------------------
# 1. The order really is driven by the stored counter
# ---------------------------------------------------------------------------


def test_mutating_a_stored_counter_moves_the_page(
    legacy_surface: Any, neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """Obligation 5 for everything below: the probe is sensitive to the counter.

    The edge set is never touched. Only ``usage_count`` changes, and the page
    order must follow it. If it did not, every "the counters drive pagination"
    claim in this module would be unfounded and the divergence test below would
    be measuring something else.
    """
    token = compat_graph.token
    before = _ids(legacy_surface, MODELS_STORED_ORDER, token)
    assert len(before) == 3, f"expected the three fixture models, got {before}"

    last = before[-1]
    with neo4j_repository.session() as session:
        session.run(
            "MATCH (m:Model {id: $id}) SET m.usage_count = 999, m.is_canonical = true",
            id=last,
        )
    after = _ids(legacy_surface, MODELS_STORED_ORDER, token)

    assert after != before, (
        "raising one model's usage_count to 999 did not change the page order, "
        "so GET /api/models is not in fact ordered by the stored counter and "
        "the rest of this module proves nothing"
    )
    assert after[0] == last


# ---------------------------------------------------------------------------
# 2. Stored counters and edge degrees give different pages
# ---------------------------------------------------------------------------


def test_stored_counters_and_edge_degrees_disagree_on_this_fixture(
    legacy_surface: Any, compat_graph: FixtureGraph
) -> None:
    """The precondition, asserted before the conclusion is drawn from it.

    If the fixture's stored counters happened to equal the degrees, the
    divergence test below would pass or fail for reasons unrelated to
    recomputation.
    """
    token = compat_graph.token
    stored = {
        row["id"]: row["usage_count"]
        for row in legacy_surface.run(MODELS_STORED_ORDER, tok=token)
    }
    degrees = {
        row["id"]: row["usage_count"]
        for row in legacy_surface.run(MODELS_DEGREE_ORDER, tok=token)
    }
    assert stored and degrees
    assert stored.keys() == degrees.keys()
    assert stored != degrees, (
        "the fixture's stored counters match the edge degrees, so it no longer "
        "reproduces the drift legacy accumulates (no reconciler exists for "
        "Model.usage_count). Restore the purge-without-decrement step in the "
        "fixture before trusting this module."
    )


@pytest.mark.parametrize(
    ("label", "stored_query", "degree_query"),
    (
        ("GET /api/models", MODELS_STORED_ORDER, MODELS_DEGREE_ORDER),
        ("GET /api/concepts", CONCEPTS_STORED_ORDER, CONCEPTS_DEGREE_ORDER),
    ),
)
def test_recomputing_the_counter_changes_the_page_order(
    legacy_surface: Any,
    compat_graph: FixtureGraph,
    label: str,
    stored_query: str,
    degree_query: str,
) -> None:
    """The finding: a counter-recomputing projection reorders paginated reads.

    Not a bug in the projection — §4.4 is right that recomputation is the only
    honest option, and the legacy value is simply wrong. It is a *behaviour
    change on a paginated endpoint*, and it belongs in the release notes next to
    the BELONGS_TO change rather than being discovered by a client whose
    "page 2" stopped meaning what it meant.
    """
    token = compat_graph.token
    stored_order = _ids(legacy_surface, stored_query, token)
    degree_order = _ids(legacy_surface, degree_query, token)

    assert stored_order, f"{label}: the stored-counter page is empty"
    assert degree_order, f"{label}: the recomputed page is empty"
    assert sorted(stored_order) == sorted(degree_order), (
        f"{label}: the two queries are not paging over the same set"
    )
    assert stored_order != degree_order, (
        f"{label}: the stored-counter order and the recomputed order agree on "
        f"this fixture, so it is not exercising the divergence it exists to "
        f"demonstrate: {stored_order}"
    )
