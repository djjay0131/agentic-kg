"""The application read-path inventory, as data rather than as prose.

Every entry names one place where application code (an agent, an API router,
the retrieval layer) reads the graph, and records exactly what that read
depends on: node labels, relationship types, node properties, ordering keys and
named vector indexes. Phase 7's job is to prove those dependencies keep holding
against the canonical projection, so the inventory has to be machine-readable —
a markdown table cannot be asserted against.

Four things make this registry more than a transcription:

1. **Each entry carries a ``snippet``** that must occur verbatim in the file it
   cites. ``test_read_path_inventory.py`` checks every one. An inventory that
   drifts from the code it describes is worse than no inventory, and this is the
   cheapest way to make drift a test failure instead of a discovery.

1b. **The reverse direction is checked too, and it is where the gaps were.**
   The snippet anchor proves every *entry* describes real code; for a while
   nothing proved that real code had an *entry*. Review found the consequence:
   the eighth ``BELONGS_TO`` site the merged spec itself names, the ``CITES`` /
   ``USES_MODEL`` / ``APPLIES_METHOD`` traversals, and four of the six vector
   indexes all had no entry at all — so the index assertion was quantifying
   over a one-element set. Three scans now close it (inline Cypher in the
   application trees, repository reads reached from a router via ``via``, and
   vector-index names anywhere in the source), and the scans themselves found
   three further gaps that reading had missed.

   A second review pass found the next layer of the same problem: the scan was
   sound, but leg B consulted a **hand-maintained allow-list** of repository
   methods held to carry no contract, and that list did not obey its own
   stated criterion. It exempted ``get_topic_children`` (traverses
   ``SUBTOPIC_OF``, orders on ``c.name``) and ``get_topic_tree`` (orders on
   ``t.name``); auditing the rest mechanically turned up three more
   (``get_topic_by_name``'s ``CASE t.level`` tie-break, ``get_model_by_name``,
   ``get_method_by_name``), one redundant entry, and two names that are not
   repository methods at all and so exempted nothing. The list is gone: the
   exemption is now *computed* from the criterion, so there is no longer a
   place to record an exemption the criterion does not justify.

2. **Each entry is classified** (:class:`CompatClass`). Not every read path can
   be held to behaviour preservation. Eight of them traverse
   ``(:Problem)-[:BELONGS_TO]->(:Topic)``, which **no automated writer
   produces** — mapping spec §5.2. The projection *derives* that edge, which is
   a declared behaviour **change**, so a parity assertion over those paths would
   be comparing two empty results today and a non-empty result against an empty
   one tomorrow. They are marked ``DECLARED_CHANGE`` and are held to a
   *reachability* contract instead of a parity one.

3. **Three write surfaces are recorded as ``SCOPED_OUT``** with the reason. They
   are non-functional today (see :mod:`agentic_kg.migration.compat` docstring),
   so a compatibility test that depended on them would be asserting over a code
   path that raises before it reaches the graph.

Read-only: nothing in this module opens a driver or mutates anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "CompatClass",
    "ReadPath",
    "READ_PATHS",
    "SCOPED_OUT_SURFACES",
    "ScopedOutSurface",
    "labels_read",
    "ordering_keys_read",
    "paths_for_class",
    "relationships_read",
    "vector_indexes_read",
]


class CompatClass(str, Enum):
    """How a read path may legitimately be asserted across the cutover."""

    #: Legacy and projection must return the same rows in the same order.
    PARITY = "parity"

    #: The projection deliberately returns *different* (non-empty) results
    #: where legacy returns nothing. Mapping spec §5.2 disposition 2. Parity is
    #: the wrong assertion; reachability is the right one.
    DECLARED_CHANGE = "declared_change"

    #: Not assertable: the code path does not reach the graph today.
    SCOPED_OUT = "scoped_out"


@dataclass(frozen=True)
class ReadPath:
    """One application read of the graph, and what it depends on."""

    id: str
    #: Human-facing surface: an HTTP route, an agent method, a service method.
    surface: str
    #: Repo-relative path of the file that issues the read.
    source_file: str
    #: A literal fragment that must occur in ``source_file``. The anti-drift
    #: anchor — see :func:`test_read_path_inventory.test_every_snippet_is_real`.
    snippet: str
    labels: tuple[str, ...]
    relationships: tuple[str, ...]
    properties: tuple[str, ...]
    #: Additional literal anchors, for a surface that issues more than one
    #: query (``GET /api/stats`` runs four) or whose query is assembled from
    #: several f-string fragments (``GET /api/models``). Checked exactly like
    #: ``snippet``, so they carry the same anti-drift guarantee; they exist so
    #: the completeness scan can see every literal an entry accounts for.
    also: tuple[str, ...] = ()
    ordering: tuple[str, ...] = ()
    vector_indexes: tuple[str, ...] = ()
    compat: CompatClass = CompatClass.PARITY
    note: str = ""
    #: ``Neo4jRepository`` / service method names this entry covers.
    #:
    #: The snippet anchor proves an entry describes real code. It does **not**
    #: prove the inventory is *complete* — nothing stopped a graph read from
    #: having no entry at all, and four `CITES` / `USES_MODEL` /
    #: `APPLIES_METHOD` reads and four of the six vector indexes were missing
    #: for exactly that reason. ``via`` closes the other direction: a router
    #: calling ``repo.X`` where ``X``'s body contains a ``MATCH`` must find
    #: ``X`` named here, or ``test_read_path_inventory`` fails. See
    #: ``test_every_repository_read_reached_from_a_router_is_inventoried``.
    via: tuple[str, ...] = ()


API = "packages/api/src/agentic_kg_api"
CORE = "packages/core/src/agentic_kg"

#: The read-path inventory. Verified against the tree at
#: ``d40166e`` (integration/kgis-kgcs-adoption).
READ_PATHS: tuple[ReadPath, ...] = (
    # ------------------------------------------------------------------
    # Research agents — the deterministic context assembled BEFORE any LLM call
    # ------------------------------------------------------------------
    ReadPath(
        id="agent.ranking.candidates",
        surface="RankingAgent._query_candidates (no topic filter)",
        source_file=f"{CORE}/agents/ranking.py",
        snippet="results = self.search.structured_search(",
        labels=("Problem",),
        relationships=(),
        properties=("id", "statement", "status", "created_at", "extraction_metadata"),
        ordering=("p.created_at DESC",),
        compat=CompatClass.PARITY,
        note=(
            "Delegates to SearchService.structured_search; falls back to "
            "Neo4jRepository.list_problems when no search service is injected. "
            "Both order on p.created_at DESC.\n"
            "NEW DEFECT D-4: `state.get('status_filter', 'open')` never yields "
            "'open' — create_initial_state stores the key with value None, so "
            "the default beside it is dead and the agent runs unfiltered. When a "
            "caller DOES set it (POST /api/agents/workflows accepts "
            "`status_filter` as a plain str, routers/agents.py:50), "
            "search.py:208 evaluates `status.value` on that str and raises "
            "AttributeError, which ranking.py's blanket `except Exception` turns "
            "into a workflow with zero candidates. Masked in unit tests by a "
            "MagicMock search service."
        ),
    ),
    ReadPath(
        id="agent.ranking.candidates_by_topic",
        surface="RankingAgent._query_candidates (topic_filter set)",
        source_file=f"{CORE}/agents/ranking.py",
        snippet="topic_id=topic_id,",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id", "statement", "status", "created_at"),
        ordering=("p.created_at DESC",),
        compat=CompatClass.DECLARED_CHANGE,
        note="Reaches search.py:169, which matches the unwritten Problem->Topic edge.",
    ),
    ReadPath(
        id="agent.continuation.topic_name",
        surface="ContinuationAgent._lookup_topic_name",
        source_file=f"{CORE}/agents/continuation.py",
        snippet="MATCH (p:Problem {id: $id})-[:BELONGS_TO]->(t:Topic)",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id", "name"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Swallows every exception and returns 'unspecified', so it degrades "
            "silently rather than failing. Today it always returns 'unspecified' "
            "on a pipeline-built graph."
        ),
    ),
    ReadPath(
        id="agent.continuation.problem_context",
        surface="ContinuationAgent._load_problem_context",
        source_file=f"{CORE}/agents/continuation.py",
        snippet="problem = self.repo.get_problem(problem_id)",
        labels=("Problem",),
        relationships=(),
        properties=(
            "id",
            "statement",
            "status",
            "constraints",
            "datasets",
            "baselines",
            "metrics",
        ),
        compat=CompatClass.PARITY,
        note="JSON-string blobs decoded by Neo4jRepository._problem_from_neo4j.",
    ),
    ReadPath(
        id="agent.continuation.related_problems",
        surface="ContinuationAgent._load_problem_context -> RelationService",
        source_file=f"{CORE}/knowledge_graph/relations.py",
        snippet="MATCH (p:Problem {{id: $id}})-[r{rel_pattern}]-(related:Problem)",
        also=(
            "MATCH (p:Problem {{id: $id}})-[r{rel_pattern}]->(related:Problem)",
            "MATCH (p:Problem {{id: $id}})<-[r{rel_pattern}]-(related:Problem)",
        ),
        labels=("Problem",),
        relationships=("EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("id", "statement", "confidence", "evidence_doi"),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "DEFECT D-1: continuation.py:128 calls get_related_problems(..., "
            "limit=10) but relations.py:261 declares no `limit` parameter. The "
            "TypeError is swallowed by `logger.warning`, so related_problems is "
            "unconditionally empty in production. Cannot be held to a "
            "compatibility contract until the call is fixed."
        ),
    ),
    ReadPath(
        id="agent.evaluation.problem_context",
        surface="EvaluationAgent._generate_code (pre-LLM read)",
        source_file=f"{CORE}/agents/evaluation.py",
        snippet="problem = self.repo.get_problem(proposal.problem_id)",
        labels=("Problem",),
        relationships=(),
        properties=("id", "statement", "datasets", "metrics"),
        compat=CompatClass.PARITY,
        note="Only the dataset and metric *names* reach the prompt.",
    ),
    ReadPath(
        id="agent.synthesis.problem_context",
        surface="SynthesisAgent.run (pre-LLM read)",
        source_file=f"{CORE}/agents/synthesis.py",
        snippet="problem = self.repo.get_problem(problem_id or proposal.problem_id)",
        labels=("Problem",),
        relationships=(),
        properties=("id", "statement"),
        compat=CompatClass.PARITY,
        note=(
            "The read half of SynthesisAgent works. Its four writes do not — see "
            "SCOPED_OUT_SURFACES."
        ),
    ),
    # ------------------------------------------------------------------
    # Retrieval / search
    # ------------------------------------------------------------------
    ReadPath(
        id="search.structured.by_topic",
        surface="SearchService.structured_search(topic_id=...)",
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet='"MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id})"',
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id", "statement", "status", "created_at", "datasets"),
        ordering=("p.created_at DESC",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "The Problem->Topic edge this filters on has no automated writer, so "
            "on a pipeline-built graph this returns empty for every topic. The "
            "projection derives the edge (spec §5.2), so results appear for the "
            "first time — a declared change, not a regression."
        ),
    ),
    ReadPath(
        id="search.structured.by_status",
        surface="SearchService.structured_search(status=...)",
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet='query += " RETURN p ORDER BY p.created_at DESC LIMIT $limit"',
        also=('query = "MATCH (p:Problem)"',),
        labels=("Problem",),
        relationships=(),
        properties=("id", "statement", "status", "created_at"),
        ordering=("p.created_at DESC",),
        compat=CompatClass.PARITY,
    ),
    ReadPath(
        id="search.structured.by_year",
        surface="SearchService.structured_search(year_from/year_to)",
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet='query += " MATCH (p)-[:EXTRACTED_FROM]->(paper:Paper)"',
        labels=("Problem", "Paper"),
        relationships=("EXTRACTED_FROM",),
        properties=("year",),
        ordering=("p.created_at DESC",),
        compat=CompatClass.PARITY,
        note="The Problem->Paper shape of EXTRACTED_FROM, not the mention shape.",
    ),
    ReadPath(
        id="search.semantic",
        surface="SearchService.semantic_search / POST /api/search",
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="'problem_embedding_idx',",
        labels=("Problem",),
        relationships=(),
        properties=("embedding",),
        vector_indexes=("problem_embedding_idx",),
        compat=CompatClass.PARITY,
        note=(
            "The index is named as a string literal, so the projection's DDL must "
            "recreate it under exactly this name. Not exercised by the "
            "deterministic baseline: it needs a live embedding provider."
        ),
    ),
    ReadPath(
        id="search.hybrid.topic_leg",
        surface="SearchService.hybrid_search(topic_id=...)",
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $tid})",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Resolves the topic's problem-id set up front, then filters the "
            "semantic hits against it. An empty set silently drops every "
            "semantic result, so a topic-filtered hybrid search returns nothing "
            "today no matter how good the embedding match was."
        ),
    ),
    ReadPath(
        id="repo.list_problems",
        via=("list_problems",),
        surface="Neo4jRepository.list_problems (GET /api/problems)",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet='query += " RETURN p ORDER BY p.created_at DESC SKIP $offset LIMIT $limit"',
        labels=("Problem",),
        relationships=(),
        properties=("id", "statement", "status", "created_at"),
        ordering=("p.created_at DESC",),
        compat=CompatClass.PARITY,
    ),
    ReadPath(
        id="relations.paper_authors",
        surface="RelationService.get_paper_authors",
        source_file=f"{CORE}/knowledge_graph/relations.py",
        snippet="ORDER BY r.author_position",
        labels=("Paper", "Author"),
        relationships=("AUTHORED_BY",),
        properties=("id", "name", "author_position"),
        ordering=("r.author_position",),
        compat=CompatClass.PARITY,
        note=(
            "repository.link_paper_to_author writes `position`; this reader sorts "
            "on `author_position`. Mapping spec §4.4 settles it on author_position."
        ),
    ),
    # ------------------------------------------------------------------
    # API routers
    # ------------------------------------------------------------------
    ReadPath(
        id="api.stats.problems_by_topic",
        surface="GET /api/stats",
        source_file=f"{API}/main.py",
        snippet="MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic)",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("name",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "`problems_by_topic` is `{}` on any pipeline-built graph. The "
            "dashboard histogram is empty today and becomes populated after "
            "cutover."
        ),
    ),
    ReadPath(
        id="api.stats.counts",
        surface="GET /api/stats (totals + by status)",
        source_file=f"{API}/main.py",
        snippet='"MATCH (p:Problem) RETURN p.status as status, count(p) as count"',
        also=(
            '"MATCH (p:Problem) RETURN count(p) as count"',
            '"MATCH (p:Paper) RETURN count(p) as count"',
            '"MATCH (t:Topic) RETURN count(t) as count"',
        ),
        labels=("Problem", "Paper", "Topic"),
        relationships=(),
        properties=("status",),
        compat=CompatClass.PARITY,
        note=(
            "Bare label counts. If canonical ledger rows ever carried one of "
            "these labels in the same database, this endpoint would silently "
            "over-count — see test_no_ledger_leak.py."
        ),
    ),
    ReadPath(
        id="api.graph.problems_by_topic",
        surface="GET /api/graph?topic_id=",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id})",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("statement", "status", "confidence"),
        compat=CompatClass.DECLARED_CHANGE,
        note="LIMIT without ORDER BY — ordering is incidental (spec U-8).",
    ),
    ReadPath(
        id="api.graph.problem_relations",
        surface="GET /api/graph (problem-problem links)",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (p1:Problem)-[r]->(p2:Problem)",
        labels=("Problem",),
        relationships=("EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("statement", "status"),
        compat=CompatClass.PARITY,
        note=(
            "UNTYPED relationship pattern: any Problem->Problem edge is rendered. "
            "A projection that adds a new Problem->Problem edge type changes this "
            "endpoint's output without touching its code."
        ),
    ),
    ReadPath(
        id="api.graph.problems_papers",
        surface="GET /api/graph?include_papers=true",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (p:Problem)-[r:EXTRACTED_FROM]->(paper:Paper)",
        labels=("Problem", "Paper"),
        relationships=("EXTRACTED_FROM",),
        properties=("title", "doi", "year", "authors"),
        compat=CompatClass.PARITY,
    ),
    ReadPath(
        id="api.graph.include_topics",
        surface="GET /api/graph?include_topics=true",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic)",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id", "name", "level", "problem_count"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Also reads Topic.problem_count, a denormalized counter the "
            "projection recomputes from edges (spec §4.4) — so the rendered "
            "value can change even where the edge set does not."
        ),
    ),
    ReadPath(
        id="api.graph.node_neighbourhood",
        surface="GET /api/graph/node/{node_id}",
        source_file=f"{API}/routers/graph.py",
        snippet="OPTIONAL MATCH (p)-[r]-(neighbor)",
        labels=("Problem",),
        relationships=(),
        properties=("statement", "status"),
        compat=CompatClass.PARITY,
        note=(
            "UNTYPED and UNLABELLED neighbour pattern — it renders whatever is "
            "adjacent. The strongest ledger-leak surface in the application; "
            "asserted behaviourally in test_no_ledger_leak.py."
        ),
    ),
    ReadPath(
        id="api.topics.problems",
        surface="GET /api/topics/{id}/problems",
        source_file=f"{API}/routers/topics.py",
        snippet="MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id})",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO",),
        properties=("id", "statement", "status", "created_at"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "LIMIT with no ORDER BY (spec U-8): row order is whatever the "
            "planner produces, so this endpoint has no stable pagination "
            "contract to preserve in the first place. The probe adds an ORDER BY "
            "so the baseline is reproducible, and that difference is deliberate."
        ),
    ),
    ReadPath(
        id="api.topics.problems_subtopics",
        surface="GET /api/topics/{id}/problems?include_subtopics=true",
        source_file=f"{API}/routers/topics.py",
        snippet="MATCH (descendant:Topic)-[:SUBTOPIC_OF*0..]->(root:Topic {id: $topic_id})",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO", "SUBTOPIC_OF"),
        properties=("id", "statement", "status"),
        compat=CompatClass.DECLARED_CHANGE,
        note="Variable-length SUBTOPIC_OF traversal; the projection must keep the direction.",
    ),
    ReadPath(
        id="api.concepts.list",
        surface="GET /api/concepts",
        source_file=f"{API}/routers/concepts.py",
        snippet="ORDER BY rc.mention_count DESC, rc.name",
        labels=("ResearchConcept",),
        relationships=(),
        properties=("id", "name", "description", "aliases", "mention_count", "paper_count"),
        ordering=("rc.mention_count DESC", "rc.name"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Denormalized counter used as the primary pagination sort key. "
            "Reclassified PARITY -> DECLARED_CHANGE: this harness's own evidence "
            "(test_ordering_keys_depend_on_counters) shows the stored-counter "
            "page order and the recomputed-from-edges order differ, so holding "
            "it to parity would assert something already disproved. Spec §5.1 "
            "consequence 2 ('Pagination is preserved') is false as written."
        ),
    ),
    ReadPath(
        id="api.models.list",
        surface="GET /api/models",
        source_file=f"{API}/routers/models.py",
        snippet="ORDER BY m.is_canonical DESC, m.usage_count DESC, m.name",
        also=("MATCH (m:Model)",),
        labels=("Model",),
        relationships=(),
        properties=("id", "name", "architecture", "is_canonical", "usage_count"),
        ordering=("m.is_canonical DESC", "m.usage_count DESC", "m.name"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "usage_count has NO reconciler in legacy (spec §5.1). Reclassified "
            "PARITY -> DECLARED_CHANGE: the page order is proved to move when "
            "the counter is recomputed from USES_MODEL degree."
        ),
    ),
    ReadPath(
        id="api.methods.list",
        surface="GET /api/methods",
        source_file=f"{API}/routers/methods.py",
        snippet="ORDER BY m.usage_count DESC, m.name",
        also=("MATCH (m:Method)",),
        labels=("Method",),
        relationships=(),
        properties=("id", "name", "method_type", "usage_count"),
        ordering=("m.usage_count DESC", "m.name"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "usage_count has NO reconciler in legacy (spec §5.1). Reclassified "
            "PARITY -> DECLARED_CHANGE for the same reason as GET /api/models: "
            "recomputing the counter from APPLIES_METHOD degree reorders the page."
        ),
    ),
    ReadPath(
        id="api.papers.list",
        surface="GET /api/papers",
        source_file=f"{API}/routers/papers.py",
        snippet="MATCH (p:Paper) RETURN p ORDER BY p.year DESC SKIP $offset LIMIT $limit",
        labels=("Paper",),
        relationships=(),
        properties=("doi", "title", "authors", "year", "venue", "is_stub", "citation_count"),
        ordering=("p.year DESC",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Order is stable (p.year DESC, no counter in the sort key) but the "
            "payload is not: §4.4 redefines Paper.citation_count as the "
            "source-asserted global count and moves today's in-graph CITES "
            "degree to a new in_graph_citation_count. Same class of change as "
            "api.topics.by_level — a value a client reads changes meaning — so "
            "it gets the same classification rather than PARITY."
        ),
    ),
    ReadPath(
        id="api.concepts.linked_problems",
        via=("get_problems_for_concept",),
        surface="GET /api/concepts/{id}/problems",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="ORDER BY pc.mention_count DESC",
        labels=("ProblemConcept", "ResearchConcept"),
        relationships=("INVOLVES_CONCEPT",),
        properties=("id", "canonical_statement", "mention_count"),
        ordering=("pc.mention_count DESC",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Reclassified PARITY -> DECLARED_CHANGE. `pc.mention_count` is the "
            "sole sort key and legacy never decrements it (auto_linker.py:281 "
            "increments; nothing reconciles), so recomputing it from "
            "INSTANCE_OF degree reorders this page too."
        ),
    ),
    ReadPath(
        id="api.concepts.linked_papers",
        via=("get_papers_for_concept",),
        surface="GET /api/concepts/{id}/papers",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="ORDER BY coalesce(p.year, 0) DESC",
        labels=("Paper", "ResearchConcept"),
        relationships=("DISCUSSES",),
        properties=("doi", "title", "year"),
        ordering=("coalesce(p.year, 0) DESC",),
        compat=CompatClass.PARITY,
    ),
    ReadPath(
        id="api.ingest.run_status",
        surface="GET /api/ingest/{trace_id}",
        source_file=f"{API}/routers/ingest.py",
        snippet="MATCH (r:IngestionRun {trace_id: $trace_id}) RETURN r",
        labels=("IngestionRun",),
        relationships=(),
        properties=("trace_id", "status", "papers_found", "papers_imported"),
        compat=CompatClass.PARITY,
        note=(
            "job_runner.py:91 uses a bare CREATE with no uniqueness constraint, so "
            "a re-run duplicates the row and this read takes whichever comes back. "
            "The projection keys on trace_id (spec §4.4), which is a fix."
        ),
    ),
    # ------------------------------------------------------------------
    # Found by the completeness check, not by reading. Every entry below was
    # missing from the first cut of this inventory: the snippet anchor proves
    # an entry matches code, but nothing proved code had an entry.
    # ------------------------------------------------------------------
    ReadPath(
        id="api.graph.problems",
        surface="GET /api/graph (no topic filter)",
        source_file=f"{API}/routers/graph.py",
        snippet="                MATCH (p:Problem)\n                RETURN p\n",
        labels=("Problem",),
        relationships=(),
        properties=("statement", "status", "confidence"),
        compat=CompatClass.PARITY,
        note=(
            "The unfiltered branch of the same endpoint whose topic branch was "
            "inventoried. Missing from the first cut — found by the "
            "completeness scan, not by reading. LIMIT with no ORDER BY."
        ),
    ),
    ReadPath(
        id="api.topics.by_level",
        surface="GET /api/topics?level=",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (t:Topic {level: $level})",
        labels=("Topic",),
        relationships=(),
        properties=("id", "name", "level", "problem_count", "paper_count"),
        ordering=("t.name",),
        compat=CompatClass.DECLARED_CHANGE,
        via=("get_topics_by_level",),
        note=(
            "Surfaces Topic.problem_count / paper_count, both recomputed by "
            "§4.4. Reclassified PARITY -> DECLARED_CHANGE: its own note said the "
            "counters are recomputed while the class said the response is "
            "preserved, and its sibling api.graph.include_topics — reading the "
            "same property — was already DECLARED_CHANGE. The row ORDER is "
            "stable here (t.name); it is the payload that changes."
        ),
    ),
    ReadPath(
        id="api.graph.problem_relations_by_topic",
        surface="GET /api/graph?topic_id= (problem-problem link leg)",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (p1:Problem)-[:BELONGS_TO]->(:Topic {id: $topic_id})",
        labels=("Problem", "Topic"),
        relationships=("BELONGS_TO", "EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("statement", "status"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "The eighth BELONGS_TO site, named by spec §5.2 ('graph.py:39,77,173') "
            "and missing from the first cut of this inventory — so the original "
            "31 entries covered 7 of the 8 sites the spec itself lists. Untyped "
            "second hop, like its unfiltered sibling."
        ),
    ),
    ReadPath(
        id="api.papers.references",
        surface="GET /api/papers/{doi}/references",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (p:Paper {doi: $doi})-[:CITES]->(r:Paper)",
        labels=("Paper",),
        relationships=("CITES",),
        properties=("doi", "title", "year", "is_stub"),
        ordering=("r.title",),
        compat=CompatClass.PARITY,
        via=("get_references",),
        note="Outbound CITES. Sorted on title, not on either citation counter.",
    ),
    ReadPath(
        id="api.papers.citations",
        surface="GET /api/papers/{doi}/citations",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (c:Paper)-[:CITES]->(p:Paper {doi: $doi})",
        labels=("Paper",),
        relationships=("CITES",),
        properties=("doi", "title", "year", "is_stub"),
        ordering=("c.title",),
        compat=CompatClass.PARITY,
        via=("get_citing_papers",),
        note=(
            "Inbound CITES, scoped to the corpus — so it counts in-graph "
            "citations, not the global count. §4.4 splits these into "
            "`citation_count` (source-asserted) and `in_graph_citation_count`, "
            "which does not change this traversal but does change what "
            "`Paper.citation_count` means beside it."
        ),
    ),
    ReadPath(
        id="api.models.papers",
        surface="GET /api/models/{id}/papers",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (p:Paper)-[:USES_MODEL]->(m:Model {id: $mid})",
        labels=("Paper", "Model"),
        relationships=("USES_MODEL",),
        properties=("doi", "title", "year"),
        ordering=("p.title",),
        compat=CompatClass.PARITY,
        via=("get_papers_for_model",),
    ),
    ReadPath(
        id="api.methods.papers",
        surface="GET /api/methods/{id}/papers",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (p:Paper)-[:APPLIES_METHOD]->(m:Method {id: $mid})",
        labels=("Paper", "Method"),
        relationships=("APPLIES_METHOD",),
        properties=("doi", "title", "year"),
        ordering=("p.title",),
        compat=CompatClass.PARITY,
        via=("get_papers_for_method",),
    ),
    ReadPath(
        id="api.topics.search",
        surface="GET /api/topics/search",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="CALL db.index.vector.queryNodes('topic_embedding_idx', $limit, $embedding)",
        labels=("Topic",),
        relationships=(),
        properties=("id", "name", "level", "embedding"),
        vector_indexes=("topic_embedding_idx",),
        compat=CompatClass.PARITY,
        via=("search_topics_by_embedding",),
        note="Index named as a string literal; the projection DDL must recreate it.",
    ),
    ReadPath(
        id="api.concepts.search",
        surface="GET /api/concepts/search",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="'research_concept_embedding_idx', $top_k, $embedding",
        labels=("ResearchConcept",),
        relationships=(),
        properties=("id", "name", "embedding"),
        vector_indexes=("research_concept_embedding_idx",),
        compat=CompatClass.PARITY,
        via=("search_research_concepts_by_embedding",),
    ),
    ReadPath(
        id="api.models.search",
        surface="GET /api/models/search",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="'model_embedding_idx', $top_k, $embedding",
        labels=("Model",),
        relationships=(),
        properties=("id", "name", "embedding"),
        vector_indexes=("model_embedding_idx",),
        compat=CompatClass.PARITY,
        via=("search_models_by_embedding",),
    ),
    ReadPath(
        id="api.methods.search",
        surface="GET /api/methods/search",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="'method_embedding_idx', $top_k, $embedding",
        labels=("Method",),
        relationships=(),
        properties=("id", "name", "embedding"),
        vector_indexes=("method_embedding_idx",),
        compat=CompatClass.PARITY,
        via=("search_methods_by_embedding",),
    ),
    ReadPath(
        id="er.concept_matcher",
        surface="ConceptMatcher — ER matching (pipeline-internal, no HTTP route)",
        source_file=f"{CORE}/knowledge_graph/concept_matcher.py",
        snippet="'concept_embedding_idx',",
        labels=("ProblemConcept",),
        relationships=(),
        properties=("id", "canonical_statement", "embedding"),
        vector_indexes=("concept_embedding_idx",),
        compat=CompatClass.PARITY,
        note=(
            "Not an API surface, but it names the sixth projected vector index "
            "as a string literal, so the projection owes it the same DDL. "
            "Included because the index-name contract is what matters here."
        ),
    ),
    ReadPath(
        id="api.topics.children",
        surface="GET /api/topics/{id} (children leg)",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (c:Topic)-[:SUBTOPIC_OF]->(p:Topic {id: $id})",
        labels=("Topic",),
        relationships=("SUBTOPIC_OF",),
        properties=("id", "name", "level", "parent_id"),
        ordering=("c.name",),
        compat=CompatClass.PARITY,
        via=("get_topic_children",),
        note=(
            "Found by re-auditing the exemption list against its own stated "
            "criterion: it sat in `_implicitly_claimed()`, whose docstring "
            "asserted its members never traverse a relationship or order a "
            "page. This does both. The allow-list is gone; the criterion is now "
            "computed."
        ),
    ),
    ReadPath(
        id="api.topics.tree",
        surface="GET /api/topics/tree",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (t:Topic {level: 'domain'})",
        labels=("Topic",),
        relationships=("SUBTOPIC_OF",),
        properties=("id", "name", "level", "problem_count", "paper_count"),
        ordering=("t.name",),
        compat=CompatClass.DECLARED_CHANGE,
        via=("get_topic_tree",),
        note=(
            "Recurses into get_topic_children, so it inherits the SUBTOPIC_OF "
            "traversal, and it renders Topic.problem_count / paper_count — both "
            "recomputed by §4.4, so the payload changes even where the tree "
            "shape does not. Same reasoning as api.graph.include_topics."
        ),
    ),
    ReadPath(
        id="relations.create_relation.guard",
        surface="RelationService.create_relation (existence + duplicate guard)",
        source_file=f"{CORE}/knowledge_graph/relations.py",
        snippet="MATCH (from:Problem {id: $from_id})",
        also=("MATCH (from:Problem {{id: $from_id}})-[r:{rel_type}]->(to:Problem {{id: $to_id}})",),
        labels=("Problem",),
        relationships=("EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("id",),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "Two reads that exist only inside a write path — they verify both "
            "endpoints exist and that the edge is not already present. Scoped "
            "out with the write they guard: §4.5 turns every mutation endpoint "
            "into a curation request, so the guard has no post-cutover "
            "equivalent. Inventoried rather than ignored because it is a read "
            "in a scanned module, and an un-inventoried read is how the "
            "completeness check gets hollowed out."
        ),
    ),
    ReadPath(
        id="relations.get_source_paper",
        surface="RelationService.get_source_paper",
        source_file=f"{CORE}/knowledge_graph/relations.py",
        snippet="MATCH (p:Problem {id: $id})-[:EXTRACTED_FROM]->(paper:Paper)",
        labels=("Problem", "Paper"),
        relationships=("EXTRACTED_FROM",),
        properties=("doi", "title", "year"),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "Dead code: zero non-test callers (confirmed by review, which "
            "checked whether it implied a 46th live read path — it does not). "
            "The traversal it performs is already covered behaviourally by "
            "api.graph.problems_papers and search.structured.by_year. Recorded "
            "so the scan has an entry to match rather than a gap to ignore; "
            "delete the method and this entry together."
        ),
    ),
    ReadPath(
        id="api.reviews.queue",
        surface="GET /api/reviews",
        source_file=f"{CORE}/knowledge_graph/review_queue.py",
        snippet="await self._repo.read_transaction(_tx)",
        also=(
            "MATCH (r:PendingReview)",
            "MATCH (r:PendingReview {id: $review_id})",
            "MATCH (r:PendingReview)-[:REVIEWS]->(m:ProblemMention {id: $mention_id})",
        ),
        labels=("PendingReview",),
        relationships=("REVIEWS",),
        properties=("id", "status", "priority", "sla_deadline"),
        ordering=("r.priority ASC", "r.sla_deadline ASC"),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "ReviewQueueService calls Neo4jRepository.read_transaction / "
            "write_transaction, neither of which exists. Every /api/reviews/* "
            "route raises AttributeError before touching the graph."
        ),
    ),
)


@dataclass(frozen=True)
class ScopedOutSurface:
    """An application surface deliberately excluded from the compat contract."""

    id: str
    surface: str
    source_file: str
    #: The call the code makes.
    call: str
    #: The declaration it is wrong about.
    declaration_file: str
    declaration: str
    failure: str
    reason: str


#: Write surfaces that do not reach the graph today. A compatibility test built
#: on any of these would be asserting over a code path that raises first —
#: precisely the "quantify over an empty set" shape §9.0 forbids.
#:
#: ``test_scoped_out_write_surfaces.py`` asserts each of these is *still*
#: broken. That is deliberate: if someone repairs one, the scope-out stops being
#: justified and the harness must be widened, so the test going red is the
#: notification.
SCOPED_OUT_SURFACES: tuple[ScopedOutSurface, ...] = (
    ScopedOutSurface(
        id="write.api.put_problem",
        surface="PUT /api/problems/{id}",
        source_file=f"{API}/routers/problems.py",
        call="repo.update_problem(problem_id, problem)",
        declaration_file=f"{CORE}/knowledge_graph/repository.py",
        declaration=(
            "def update_problem(self, problem: Problem, "
            "regenerate_embedding: bool = False)"
        ),
        failure="AttributeError: 'str' object has no attribute 'id' -> uncaught -> HTTP 500",
        reason=(
            "The positional call binds problem_id to `problem` and the Problem "
            "to `regenerate_embedding`."
        ),
    ),
    ScopedOutSurface(
        id="write.api.reviews",
        surface="POST /api/reviews/{id}/{assign,unassign,resolve} and GET /api/reviews",
        source_file=f"{CORE}/knowledge_graph/review_queue.py",
        call="self._repo.write_transaction(_tx) / self._repo.read_transaction(_tx)",
        declaration_file=f"{CORE}/knowledge_graph/repository.py",
        declaration="Neo4jRepository defines neither write_transaction nor read_transaction",
        failure="AttributeError before any Cypher runs",
        reason=(
            "The whole human-review surface is unreachable; PendingReview and "
            "REVIEWS are dead vocabulary."
        ),
    ),
    ScopedOutSurface(
        id="write.agent.synthesis",
        surface="SynthesisAgent._apply_graph_updates (4 writes)",
        source_file=f"{CORE}/agents/synthesis.py",
        call=(
            "repo.create_problem(id=, statement=, status=); "
            "relations.create_relation(source_id=, target_id=, relation_type=); "
            "repo.update_problem(id, status=)"
        ),
        declaration_file=f"{CORE}/knowledge_graph/repository.py",
        declaration=(
            "create_problem(problem: Problem, ...); "
            "create_relation(from_problem_id, to_problem_id, relation_type, ...); "
            "update_problem(problem: Problem, ...)"
        ),
        failure="TypeError on every call, swallowed by `logger.warning`",
        reason=(
            "SynthesisAgent has never written to Neo4j. Its *read* half is in "
            "the contract; its writes are not."
        ),
    ),
    ScopedOutSurface(
        id="read.agent.continuation_related",
        surface="ContinuationAgent related-problem context",
        source_file=f"{CORE}/agents/continuation.py",
        call="self.relations.get_related_problems(problem_id, direction='both', limit=10)",
        declaration_file=f"{CORE}/knowledge_graph/relations.py",
        declaration=(
            "def get_related_problems(self, problem_id, relation_type=None, "
            "direction='both')"
        ),
        failure="TypeError: unexpected keyword argument 'limit', swallowed by logger.warning",
        reason=(
            "NEW DEFECT found by this phase (D-1). The related-problem leg of "
            "the continuation prompt is unconditionally empty in production, "
            "and a second defect (D-2) waits behind it: the method returns "
            "list[tuple[Problem, ProblemRelation]] while the caller does "
            "rel.get('type') / rel.get('statement'). Correction to the first "
            "report of D-2 — a naive 'return the dicts instead' fix does NOT "
            "repair it either: the internal dicts (relations.py:306-311) are "
            "keyed 'problem' / 'relation' / 'rel_type' / 'direction', so "
            "rel.get('type') still misses (the key is 'rel_type') and "
            "rel.get('statement') still misses (the statement is nested inside "
            "'problem'). A D-1 + naive-D-2 fix therefore still renders every "
            "entry as '[RELATED] Unknown'. Both are masked by a MagicMock in "
            "packages/core/tests/agents/conftest.py:100 that returns "
            "{'type': ..., 'statement': ...} — a shape the real service has "
            "never produced."
        ),
    ),
)


def paths_for_class(compat: CompatClass) -> tuple[ReadPath, ...]:
    """Every read path in one compatibility class."""
    return tuple(p for p in READ_PATHS if p.compat is compat)


def labels_read() -> frozenset[str]:
    """Every node label any application read path depends on."""
    return frozenset(label for p in READ_PATHS for label in p.labels)


def relationships_read() -> frozenset[str]:
    """Every relationship type any application read path traverses."""
    return frozenset(rel for p in READ_PATHS for rel in p.relationships)


def ordering_keys_read() -> frozenset[str]:
    """Every ORDER BY key the application depends on for pagination."""
    return frozenset(key for p in READ_PATHS for key in p.ordering)


def vector_indexes_read() -> frozenset[str]:
    """Every vector index the application names as a string literal."""
    return frozenset(idx for p in READ_PATHS for idx in p.vector_indexes)
