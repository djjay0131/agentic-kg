"""Fixtures for the research-agent compatibility harness.

Two graphs and one fixture builder.

**The fixture graph is built through the legacy writers wherever a legacy writer
exists** (``Neo4jRepository``, ``RelationService``), so the baseline records what
the application would really see — not what a hand-written Cypher script decided
it should see. The two exceptions are called out inline: ``ProblemConcept`` has
no repository creator at all, and ``(:Problem)-[:BELONGS_TO]->(:Topic)`` is
written through ``assign_entity_to_topic``, which is the same call the manual CLI
command and the manual API endpoint make — and the *only* way that edge ever
comes into existence, because no automated writer produces it (mapping spec
§5.2).

The second graph is a genuinely empty Neo4j, used by
``test_anti_vacuity_guard.py`` to prove the guard fires. It is a separate
container rather than a scoped-down query because Neo4j Community offers exactly
one user database, so "empty" cannot be faked inside the populated instance
without making the probes lie about what the application runs.

Neo4j comes from the per-run ``neo4j_container`` fixture in
``packages/core/tests/conftest.py`` (#75). It skips cleanly without Docker and
refuses to run against a shared database. Nothing here reintroduces a shared
path.

No OpenAI: every creator is called with ``generate_embedding=False``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from agentic_kg.migration.compat import ProbeInputs

REPO_ROOT = Path(__file__).resolve().parents[5]
BASELINE_FILE = Path(__file__).resolve().parent / "baseline" / "legacy_pre_llm_context.json"

NEO4J_IMAGE = "neo4j:5.26-community"
EMPTY_GRAPH_PASSWORD = "testpassword"


def _at(month: int, day: int = 1) -> datetime:
    """A fixed timestamp, so ``ORDER BY p.created_at DESC`` is deterministic."""
    return datetime(2024, month, day, 12, 0, 0, tzinfo=timezone.utc)


class SessionGraphSurface:
    """A read-only :class:`GraphSurface` over a ``Neo4jRepository``-style session.

    Read-only by construction: it exposes exactly one method, which runs the
    query inside ``session.execute_read``. Neo4j refuses writes in a read
    transaction, so a probe that grew a ``CREATE`` would fail rather than mutate
    the graph underneath the other probes.
    """

    def __init__(self, repository: Any, name: str = "legacy") -> None:
        self._repo = repository
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, cypher: str, /, **params: Any) -> list[dict[str, Any]]:
        with self._repo.session() as session:
            return session.execute_read(
                lambda tx: [dict(record) for record in tx.run(cypher, **params)]
            )


class DriverGraphSurface:
    """The same surface over a bare ``neo4j.Driver`` (used for the empty graph)."""

    def __init__(self, driver: Any, name: str) -> None:
        self._driver = driver
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, cypher: str, /, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return session.execute_read(
                lambda tx: [dict(record) for record in tx.run(cypher, **params)]
            )


@dataclass(frozen=True)
class FixtureGraph:
    """Handles into the graph the baseline was captured from."""

    token: str
    inputs: ProbeInputs
    #: Model ids in fixture-declaration order; the counter tests need them.
    model_ids: tuple[str, ...]
    concept_ids: tuple[str, ...]
    problem_ids: tuple[str, ...]
    paper_dois: tuple[str, ...]
    topic_ids: tuple[str, ...]


@pytest.fixture
def compat_token() -> str:
    """The per-run marker. Also what makes the teardown sweep catch the fixture."""
    return f"TEST_COMPAT_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def compat_graph(neo4j_repository: Any, compat_token: str) -> Iterator[FixtureGraph]:
    """Build the frozen research-query fixture through the legacy writers."""
    from agentic_kg.knowledge_graph.models import (
        Author,
        Constraint,
        ConstraintType,
        Dataset,
        Evidence,
        ExtractionMetadata,
        Method,
        Metric,
        Model,
        Paper,
        Problem,
        ProblemStatus,
        RelationType,
        ResearchConcept,
        Topic,
        TopicLevel,
    )
    from agentic_kg.knowledge_graph.relations import RelationService

    repo = neo4j_repository
    tok = compat_token
    relations = RelationService(repository=repo)

    # --- topics -------------------------------------------------------
    root = Topic(
        id=f"{tok}_topic_root", name=f"{tok} machine learning", level=TopicLevel.DOMAIN
    )
    area = Topic(
        id=f"{tok}_topic_area",
        name=f"{tok} natural language processing",
        level=TopicLevel.AREA,
        parent_id=root.id,
    )
    repo.create_topic(root, generate_embedding=False)
    repo.create_topic(area, generate_embedding=False)
    repo.link_topic_parent(area.id, root.id)

    # --- papers + authors --------------------------------------------
    paper1 = Paper(
        doi=f"10.TEST_{tok}/paper-one",
        title=f"{tok} Attention Reconsidered",
        authors=["Ada Lovelace", "Alan Turing"],
        year=2021,
        venue="NeurIPS",
    )
    paper2 = Paper(
        doi=f"10.TEST_{tok}/paper-two",
        title=f"{tok} Long Context Transformers",
        authors=["Grace Hopper"],
        year=2023,
        venue="ICML",
    )
    repo.create_paper(paper1)
    repo.create_paper(paper2)

    for position, person in enumerate(("Ada Lovelace", "Alan Turing")):
        author = Author(id=f"{tok}_author_{position}", name=f"{tok} {person}")
        repo.create_author(author)
        repo.link_paper_to_author(paper1.doi, author.id, position=position)
        # repository.link_paper_to_author writes `position`; the reader
        # (relations.get_paper_authors) sorts on `author_position`. Both are
        # written here so the collision is visible in the baseline rather than
        # hidden by it -- the projection resolves it in favour of
        # `author_position` (spec §4.4), and that is a change this baseline can
        # be diffed against.
        with repo.session() as session:
            session.run(
                "MATCH (p:Paper {doi: $doi})-[r:AUTHORED_BY]->(a:Author {id: $aid}) "
                "SET r.author_position = $pos",
                doi=paper1.doi,
                aid=author.id,
                pos=position,
            )

    # --- problems -----------------------------------------------------
    # Evidence and extraction metadata are supplied because they are NOT
    # optional in practice: Problem declares both Optional with default None,
    # but Problem.to_neo4j_properties() dereferences them unconditionally
    # (entities.py:104-107), so create_problem raises AttributeError on a
    # Problem built from the model's own defaults. Reported as D-3; the fixture
    # works around it rather than depending on the broken shape.
    problems = []
    for index, (month, status) in enumerate(
        ((1, ProblemStatus.OPEN), (2, ProblemStatus.OPEN), (3, ProblemStatus.IN_PROGRESS)),
        start=1,
    ):
        problem = Problem(
            id=f"{tok}_problem_{index}",
            statement=(
                f"{tok} How can transformer efficiency be improved for problem {index}?"
            ),
            status=status,
            created_at=_at(month),
            updated_at=_at(month),
            evidence=Evidence(
                source_doi=paper1.doi if index == 1 else paper2.doi,
                source_title=f"{tok} source title {index}",
                section="Introduction",
                quoted_text=f"{tok} quoted text {index}",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="fixture",
                confidence_score=0.5 + index / 10,
                extractor_version="compat-1",
            ),
            datasets=[Dataset(name=f"{tok} dataset {index}", available=True)],
            metrics=[Metric(name=f"{tok} metric {index}", baseline_value=0.5)],
            constraints=[
                Constraint(text=f"{tok} constraint {index}", type=ConstraintType.COMPUTATIONAL)
            ],
        )
        repo.create_problem(problem, generate_embedding=False)
        problems.append(problem)

    relations.link_problem_to_paper(problems[0].id, paper1.doi, section="Introduction")
    relations.link_problem_to_paper(problems[1].id, paper2.doi, section="Discussion")
    relations.create_relation(
        from_problem_id=problems[0].id,
        to_problem_id=problems[1].id,
        relation_type=RelationType.EXTENDS,
    )

    # --- topic edges --------------------------------------------------
    # BELONGS_TO exists only because this call makes it. No automated writer
    # produces it; this is the manual CLI/API path (spec §5.2). Without it the
    # DECLARED_CHANGE probes would all be empty on both sides, which is the
    # failure mode this whole harness exists to make impossible.
    repo.assign_entity_to_topic(problems[0].id, area.id, entity_label="Problem")
    repo.assign_entity_to_topic(problems[1].id, area.id, entity_label="Problem")
    repo.assign_entity_to_topic(paper1.doi, area.id, entity_label="Paper")

    # --- research concepts, models, methods ---------------------------
    # Counters are set explicitly rather than accumulated, because the ordering
    # tests need to know what the *stored* counter says independently of what
    # the edges say. That divergence is the point: legacy has no reconciler for
    # Model.usage_count or Method.usage_count at all (spec §5.1).
    concepts = []
    for suffix, mention_count, paper_count in (
        ("alpha", 5, 2),
        ("bravo", 9, 1),
        ("charlie", 5, 0),
    ):
        concept = ResearchConcept(
            id=f"{tok}_concept_{suffix}",
            name=f"{tok} {suffix} concept",
            mention_count=mention_count,
            paper_count=paper_count,
        )
        repo.create_research_concept(concept, generate_embedding=False)
        concepts.append(concept)
    repo.link_paper_to_concept(paper1.doi, concepts[0].id)
    repo.link_paper_to_concept(paper2.doi, concepts[0].id)

    model_ids = []
    for suffix, canonical in (("alpha", True), ("bravo", False), ("charlie", False)):
        model = Model(
            id=f"{tok}_model_{suffix}",
            name=f"{tok} {suffix} model",
            is_canonical=canonical,
            usage_count=0,
        )
        repo.create_model(model, generate_embedding=False)
        model_ids.append(model.id)

    # Real edges first, so usage_count is genuinely earned...
    repo.link_paper_to_model(paper1.doi, f"{tok}_model_bravo")
    repo.link_paper_to_model(paper1.doi, f"{tok}_model_charlie")
    repo.link_paper_to_model(paper2.doi, f"{tok}_model_charlie")
    # ...then the drift legacy actually has. This is exactly what
    # re_ingestion.purge_paper_extraction does: delete the edges with raw
    # Cypher and never touch the counter. Model.usage_count has no reconciler
    # at all (spec §5.1), so the inflation is permanent. After this, stored
    # counts are alpha=0 bravo=1 charlie=2 while true degrees are
    # alpha=0 bravo=1 charlie=0 -- and those two disagree about the ORDER BY.
    with repo.session() as session:
        session.run(
            "MATCH (:Paper)-[r:USES_MODEL]->(m:Model {id: $mid}) DELETE r",
            mid=f"{tok}_model_charlie",
        )

    for suffix, usage in (("alpha", 3), ("bravo", 7)):
        method = Method(
            id=f"{tok}_method_{suffix}",
            name=f"{tok} {suffix} method",
            usage_count=usage,
        )
        repo.create_method(method, generate_embedding=False)

    # --- ProblemConcept ------------------------------------------------
    # No repository creator exists for this label (only AutoLinker writes it,
    # and only as a side effect of matching a mention), so the fixture writes
    # the node shape the readers expect directly.
    with repo.session() as session:
        for suffix, mention_count in (("alpha", 3), ("bravo", 7)):
            session.run(
                "CREATE (pc:ProblemConcept {id: $id, canonical_statement: $stmt, "
                "status: 'active', mention_count: $mc, paper_count: 1, version: 1, "
                "human_edited: false})",
                id=f"{tok}_pconcept_{suffix}",
                stmt=f"{tok} canonical statement {suffix}",
                mc=mention_count,
            )
            session.run(
                "MATCH (pc:ProblemConcept {id: $pcid}), (rc:ResearchConcept {id: $rcid}) "
                "CREATE (pc)-[:INVOLVES_CONCEPT]->(rc)",
                pcid=f"{tok}_pconcept_{suffix}",
                rcid=concepts[0].id,
            )

    # --- IngestionRun --------------------------------------------------
    # job_runner.persist_ingestion_run uses a bare CREATE with no constraint;
    # the fixture reproduces that shape exactly (including the missing
    # uniqueness) so the baseline records what GET /api/ingest/{trace_id}
    # really reads today.
    trace_id = f"{tok}_trace"
    with repo.session() as session:
        session.run(
            "CREATE (r:IngestionRun {trace_id: $trace_id, id: $trace_id, "
            "status: 'completed', query: 'compat fixture', papers_found: 2, "
            "papers_imported: 2, papers_extracted: 2, total_problems: 3})",
            trace_id=trace_id,
        )

    yield FixtureGraph(
        token=tok,
        inputs=ProbeInputs(
            topic_id=area.id,
            root_topic_id=root.id,
            problem_id=problems[0].id,
            paper_doi=paper1.doi,
            concept_id=concepts[0].id,
            trace_id=trace_id,
            status=ProblemStatus.OPEN.value,
            limit=20,
            offset=0,
        ),
        model_ids=tuple(model_ids),
        concept_ids=tuple(c.id for c in concepts),
        problem_ids=tuple(p.id for p in problems),
        paper_dois=(paper1.doi, paper2.doi),
        topic_ids=(root.id, area.id),
    )
    # Teardown is the session sweep in packages/core/tests/conftest.py: every
    # node above carries a TEST_ prefixed id, name, statement or DOI.


@pytest.fixture
def legacy_surface(neo4j_repository: Any) -> SessionGraphSurface:
    """The read-only surface the baseline is captured from."""
    return SessionGraphSurface(neo4j_repository, name="legacy")


@pytest.fixture(scope="session")
def empty_graph_driver() -> Iterator[Any]:
    """A driver onto a Neo4j that contains nothing at all.

    A *separate container*, not a scoped-down query. Neo4j Community offers one
    user database, so emptiness cannot be simulated inside the populated
    instance without rewriting the probes into something the application does
    not run -- and a guard proved against rewritten probes proves nothing about
    the real ones.
    """
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover - environment-dependent
        pytest.skip("testcontainers[neo4j] not installed")
        return

    try:
        import docker

        docker.from_env().ping()
    except Exception:  # pragma: no cover - environment-dependent
        pytest.skip("Docker is not available")
        return

    from neo4j import GraphDatabase

    container = Neo4jContainer(NEO4J_IMAGE, password=EMPTY_GRAPH_PASSWORD)
    container.start()
    driver = GraphDatabase.driver(
        container.get_connection_url(), auth=("neo4j", EMPTY_GRAPH_PASSWORD)
    )
    try:
        driver.verify_connectivity()
        yield driver
    finally:
        driver.close()
        container.stop()


@pytest.fixture
def empty_surface(empty_graph_driver: Any) -> DriverGraphSurface:
    """A :class:`GraphSurface` over the empty graph."""
    return DriverGraphSurface(empty_graph_driver, name="empty")
