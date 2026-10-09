"""Integration test (testcontainers): dual-label problem lookups must seek.

Follow-up to the #115 review. That review flagged relation endpoints
(``RelationService.create_relation``) and the synthesis provenance lineage
(``Neo4jRepository.get_derived_from`` / ``create_derived_from``), which resolve
a problem id across both ``:Problem`` and ``:ProblemConcept`` with

    MATCH (n) WHERE n.id = $id AND (n:Problem OR n:ProblemConcept)

as a possible full scan: a label predicate inside ``WHERE`` is an expression,
not a node pattern, so it *looks* like neither per-label unique id index
(``problem_id_unique`` / ``problem_concept_id_unique``) can be used.

**Investigation result (measured here, not assumed):** it does not scan. The
planner splits the label disjunction and emits a ``Union`` of two
``NodeUniqueIndexSeek`` operators, one per label, using the unique indexes. So
the shipped queries already seek; no rewrite is required on the server the
suite runs against. This module pins that, so a future server (or a query
edit) that regresses to ``AllNodesScan`` / ``NodeByLabelScan`` fails here.

The test works on the *actual* query strings the production methods issue —
captured by wrapping the managed-transaction ``run`` — rather than a copy
pasted into the test, so it checks the shipped Cypher.

Runs only under the ``integration`` marker; skips cleanly without Docker /
``NEO4J_URI`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from agentic_kg.knowledge_graph.models import (
    Problem,
    ProblemConcept,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.relations import RelationService
from neo4j import ManagedTransaction

pytestmark = pytest.mark.integration

#: Operators that inspect every node (of a label or of the graph). Neither may
#: appear for an id lookup backed by a unique index.
SCAN_OPERATORS = frozenset({"AllNodesScan", "NodeByLabelScan"})

#: Operators that answer the lookup from an index.
SEEK_OPERATORS = frozenset(
    {
        "NodeIndexSeek",
        "NodeUniqueIndexSeek",
        "NodeIndexSeekByRange",
        "NodeUniqueIndexSeekByRange",
    }
)

#: The pre-existing label-OR predicate this suite was written to investigate.
LABEL_OR_PREDICATE = (
    "MATCH (n) "
    "WHERE n.id = $id AND (n:Problem OR n:ProblemConcept) "
    "RETURN n"
)


def operator_types(plan) -> list[str]:
    """Every ``operatorType`` in a plan tree, depth-first, database-stripped.

    The driver exposes ``ResultSummary.plan`` as the server's plan map
    (``operatorType`` / ``children`` / ...) rather than a typed object, so the
    walk handles both that mapping and an object with ``operator_type``.
    Neo4j 5.26 qualifies operator names with the database
    (``NodeUniqueIndexSeek@neo4j``), so the ``@<database>`` suffix is removed
    to keep the operator vocabulary stable across server versions.
    """
    types: list[str] = []
    stack = [plan] if plan is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            operator = node.get("operatorType") or node.get("operator_type")
            children = node.get("children") or []
        else:
            operator = getattr(node, "operator_type", None)
            children = getattr(node, "children", None) or []
        if operator:
            types.append(operator.split("@", 1)[0])
        stack.extend(children)
    return types


def explain_operator_types(repository, query: str, **params) -> list[str]:
    """Run ``EXPLAIN`` on ``query`` against ``repository`` and return its ops."""
    with repository.session() as session:
        summary = session.run("EXPLAIN " + query, **params).consume()
    return operator_types(summary.plan)


def assert_index_seeked(repository, query: str, **params) -> list[str]:
    ops = explain_operator_types(repository, query, **params)
    scans = SCAN_OPERATORS.intersection(ops)
    assert not scans, (
        f"the plan scans nodes ({sorted(scans)}) instead of seeking an index "
        f"for a label-scoped id lookup: {ops}"
    )
    assert SEEK_OPERATORS.intersection(ops), (
        f"the plan has no index seek; a label-OR lookup cannot use the "
        f"per-label unique id indexes: {ops}"
    )
    return ops


@contextmanager
def capture_transaction_queries():
    """Record the exact Cypher passed to ``tx.run`` by production methods.

    Binding the assertion to the captured string — rather than a copy pasted
    into the test — is what makes this a check of the shipped query: if the
    production Cypher drifts, the captured string drifts with it.
    """
    captured: list[str] = []
    original = ManagedTransaction.run

    def recording_run(self, query, *args, **kwargs):
        # ``execute_write`` may retry a unit of work, so a query can be issued
        # more than once; dedupe to keep the identity assertions stable.
        if query not in captured:
            captured.append(query)
        return original(self, query, *args, **kwargs)

    ManagedTransaction.run = recording_run
    try:
        yield captured
    finally:
        ManagedTransaction.run = original


@pytest.fixture
def endpoints(neo4j_repository):
    """A legacy :Problem and a canonical :ProblemConcept, both TEST_-marked."""
    token = uuid.uuid4().hex[:10]
    problem = Problem(
        id=f"TEST_SEEK_P_{token}",
        statement=f"TEST_SEEK_{token} How does label-OR resolution scale?",
        status=ProblemStatus.OPEN,
    )
    neo4j_repository.create_problem(problem)

    concept = ProblemConcept(
        id=f"TEST_SEEK_C_{token}",
        canonical_statement=(
            f"TEST_SEEK_{token} How should canonical concepts be indexed?"
        ),
        status=ProblemStatus.OPEN,
    )
    with neo4j_repository.session() as session:
        session.run(
            "CREATE (c:ProblemConcept) SET c = $properties",
            properties=concept.to_neo4j_properties(),
        )
    return problem, concept


class TestInvestigationResult:
    def test_label_or_predicate_seeks_on_this_server(self, neo4j_repository):
        """The measured finding: the #115 predicate does not full-scan.

        The planner splits ``(n:Problem OR n:ProblemConcept)`` and seeks each
        per-label unique id index, so the shipped relation-create and lineage
        queries already resolve by index. If this ever becomes a scan, either
        the server regressed or the query was edited — both worth failing on.
        """
        ops = explain_operator_types(
            neo4j_repository, LABEL_OR_PREDICATE, id="TEST_SEEK_absent"
        )
        assert not SCAN_OPERATORS.intersection(ops), (
            f"the label-OR predicate now scans the graph: {ops}"
        )
        assert SEEK_OPERATORS.intersection(ops), (
            f"the label-OR predicate does not seek an index: {ops}"
        )


class TestRelationEndpointPlan:
    def test_create_relation_queries_seek_indexes(self, neo4j_repository, endpoints):
        problem, concept = endpoints
        service = RelationService(repository=neo4j_repository)

        with capture_transaction_queries() as captured:
            service.create_extends_relation(problem.id, concept.id)

        queries = [
            q for q in captured if "(from:Problem OR from:ProblemConcept)" in q
        ]
        assert len(queries) == 3, (
            f"expected the guard, duplicate-check and create queries; got {queries}"
        )

        params = {"from_id": problem.id, "to_id": concept.id, "props": {}}
        for query in queries:
            assert_index_seeked(neo4j_repository, query, **params)


class TestDerivedFromPlan:
    def test_get_derived_from_query_seeks_index(self, neo4j_repository, endpoints):
        problem, _ = endpoints
        with capture_transaction_queries() as captured:
            neo4j_repository.get_derived_from(problem.id)

        queries = [q for q in captured if "DERIVED_FROM" in q]
        assert len(queries) == 1, f"expected one lineage read, got {queries}"

        assert_index_seeked(neo4j_repository, queries[0], id=problem.id)

    def test_create_derived_from_query_seeks_index(self, neo4j_repository, endpoints):
        problem, concept = endpoints
        with capture_transaction_queries() as captured:
            neo4j_repository.create_derived_from(
                problem.id, concept.id, method="synthesis"
            )

        queries = [q for q in captured if "MERGE" in q and "DERIVED_FROM" in q]
        assert len(queries) == 1, f"expected one lineage write, got {queries}"

        assert_index_seeked(
            neo4j_repository,
            queries[0],
            from_id=problem.id,
            to_id=concept.id,
            props={},
        )


class TestBehaviourPreserved:
    def test_relation_to_canonical_concept_still_created(
        self, neo4j_repository, endpoints
    ):
        """The label-OR resolution must reach a canonical ProblemConcept."""
        problem, concept = endpoints
        service = RelationService(repository=neo4j_repository)

        relation = service.create_extends_relation(problem.id, concept.id)

        assert relation.from_problem_id == problem.id
        assert relation.to_problem_id == concept.id

    def test_derived_from_canonical_concept_round_trips(
        self, neo4j_repository, endpoints
    ):
        """get_derived_from must surface a canonical source."""
        problem, concept = endpoints
        assert neo4j_repository.create_derived_from(
            problem.id, concept.id, method="synthesis"
        )

        sources = neo4j_repository.get_derived_from(problem.id)

        assert [s["id"] for s in sources] == [concept.id]
        assert sources[0]["method"] == "synthesis"
