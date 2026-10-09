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

3. **One write surface is recorded as ``SCOPED_OUT``** with the reason. It is
   non-functional today (see :mod:`agentic_kg.migration.compat` docstring), so a
   compatibility test that depended on it would be asserting over a code path
   that raises before it reaches the graph. Sibling surfaces have been retired
   from this set as they were repaired: PUT ``/api/problems/{id}`` in #110, and
   ``SynthesisAgent``'s write-back plus ``ContinuationAgent``'s related-problem
   read in #115 (see :data:`SCOPED_OUT_SURFACES`).

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
        labels=("Problem", "ProblemConcept", "ProblemMention"),
        relationships=("INSTANCE_OF",),
        properties=("id", "statement", "status", "created_at", "extraction_metadata"),
        ordering=("c.created_at DESC", "p.created_at DESC"),
        compat=CompatClass.PARITY,
        note=(
            "Delegates to SearchService.structured_search; falls back to "
            "Neo4jRepository.list_problems when no search service is injected. "
            "Since #110 both reach the concept+legacy union (ProblemConcept "
            "unioned with :Problem), so canonical problems now appear here too.\n"
            "NEW DEFECT D-4: `state.get('status_filter', 'open')` never yields "
            "'open' — create_initial_state stores the key with value None, so "
            "the default beside it is dead and the agent runs unfiltered. When a "
            "caller DOES set it (POST /api/agents/workflows accepts "
            "`status_filter` as a plain str, routers/agents.py:50), "
            "repository.py's structured_problem_views evaluates "
            "`status.value` on that str and raises AttributeError, which "
            "ranking.py's blanket `except Exception` turns into a workflow with "
            "zero candidates. Masked in unit tests by a MagicMock search service."
        ),
    ),
    ReadPath(
        id="agent.ranking.candidates_by_topic",
        surface="RankingAgent._query_candidates (topic_filter set)",
        source_file=f"{CORE}/agents/ranking.py",
        snippet="topic_id=topic_id,",
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "statement", "status", "created_at"),
        ordering=("c.created_at DESC", "p.created_at DESC"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Reaches the concept+legacy union: a direct Problem->Topic "
            "BELONGS_TO edge has no automated writer, so on a pipeline-built "
            "graph the legacy half is empty; the projection derives the edge and "
            "adds the paper-RESEARCHES leg (spec §5.2)."
        ),
    ),
    ReadPath(
        id="agent.continuation.topic_name",
        surface="ContinuationAgent._lookup_topic_name",
        source_file=f"{CORE}/agents/continuation.py",
        snippet="MATCH (n {id: $id})",
        also=(
            "OPTIONAL MATCH (n)-[:BELONGS_TO]->(t1:Topic)",
            "OPTIONAL MATCH (n)<-[:INSTANCE_OF]-(:ProblemMention)",
        ),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "name"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Concept-aware since #110: a direct ``(n)-[:BELONGS_TO]->(Topic)`` "
            "(legacy :Problem or an explicit ProblemConcept edge) OR, concept-side, "
            "a mention extracted from a paper that ``RESEARCHES`` the topic. On a "
            "legacy graph only the direct edge exists, so this is a declared "
            "change that adds the paper-resolved topic after cutover. Swallows "
            "every exception and returns 'unspecified', so it degrades silently "
            "rather than failing."
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
        snippet="MATCH (p)-[r{rel_pattern}]->(related:Problem)",
        also=(
            "MATCH (p)<-[r{rel_pattern}]-(related:Problem)",
            "MATCH (p)-[r{rel_pattern}]-(related:Problem)",
        ),
        labels=("Problem", "ProblemConcept"),
        relationships=("EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("id", "statement", "confidence", "evidence_doi"),
        via=("get_related_problems",),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "REPAIRED in #115. continuation.py used to call "
            "``get_related_problems(..., limit=10)`` against a method with no "
            "``limit`` parameter; the TypeError was swallowed and the "
            "related-problem leg of the prompt was unconditionally empty (D-1). "
            "It now passes only ``direction='both'`` and consumes the real "
            "``(Problem, ProblemRelation)`` tuples (D-2). This entry's "
            "``relationships`` field lists the four projected problem-relation "
            "types; the traversal pattern is actually built from every "
            "``RelationType`` member, so it also matches ``RELATED_TO``, a "
            "synthesis-only generic association that §4.4 does not project "
            "(same recorded-gap shape as api.graph.problem_relations' untyped "
            "pattern). The source endpoint resolves canonical "
            "``ProblemConcept`` as well as legacy ``:Problem`` since #115, so a "
            "canonical source can seed the traversal for the first time — a "
            "declared change, not a regression."
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
            "The read half of SynthesisAgent works. Its writes were repaired in "
            "#115 (a real ``Problem`` through ``create_problem``, an "
            "``EXTENDS``/``RELATED_TO`` relation through ``create_relation``, a "
            "label-agnostic ``set_problem_status``, and a ``DERIVED_FROM`` "
            "provenance edge), so they are no longer scoped out."
        ),
    ),
    # ------------------------------------------------------------------
    # Retrieval / search
    # ------------------------------------------------------------------
    ReadPath(
        id="search.structured.by_topic",
        surface=(
            "SearchService.structured_search(topic_id=...) "
            "-> Neo4jRepository.structured_problem_views"
        ),
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="topic_id=topic_id,",
        also=("views = self._repo.structured_problem_views(",),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "statement", "status", "created_at", "datasets"),
        ordering=("c.created_at DESC", "p.created_at DESC"),
        compat=CompatClass.DECLARED_CHANGE,
        via=("structured_problem_views", "problem_ids_for_topic"),
        note=(
            "Since #110 the query lives in repository.py and the union is "
            "concept+legacy: a topic association is a direct ``BELONGS_TO`` or, "
            "concept-side, a mention extracted from a paper that ``RESEARCHES`` "
            "the topic. The Problem->Topic edge this filters on has no automated "
            "writer, so on a pipeline-built graph the legacy half returns empty "
            "for every topic; the projection derives the edge and the paper leg "
            "(spec §5.2), so results appear for the first time — a declared "
            "change, not a regression."
        ),
    ),
    ReadPath(
        id="search.structured.by_status",
        surface=(
            "SearchService.structured_search(status=...) "
            "-> Neo4jRepository.structured_problem_views"
        ),
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="status=status,",
        also=("views = self._repo.structured_problem_views(",),
        labels=("Problem", "ProblemConcept", "ProblemMention"),
        relationships=("INSTANCE_OF",),
        properties=("id", "statement", "status", "created_at"),
        ordering=("c.created_at DESC", "p.created_at DESC"),
        compat=CompatClass.PARITY,
        via=("structured_problem_views", "list_problem_views"),
    ),
    ReadPath(
        id="search.structured.by_year",
        surface=(
            "SearchService.structured_search(year_from/year_to) "
            "-> Neo4jRepository.structured_problem_views"
        ),
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="year_from=year_from,",
        also=("views = self._repo.structured_problem_views(",),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper"),
        relationships=("INSTANCE_OF", "EXTRACTED_FROM"),
        properties=("year",),
        compat=CompatClass.PARITY,
        via=("structured_problem_views", "list_problem_views"),
        note=(
            "Since #110 the year filter runs in Python over the view's "
            "``papers`` list rather than joining ``(p)-[:EXTRACTED_FROM]->(paper)`` "
            "in Cypher. That list is populated from a concept's mentions, so the "
            "legacy :Problem half of the union supplies no year and is dropped by "
            "a year filter -- recorded, not hidden."
        ),
    ),
    ReadPath(
        id="search.semantic",
        surface=(
            "SearchService.semantic_search / POST /api/search "
            "-> Neo4jRepository.semantic_problem_views"
        ),
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="views = self._repo.semantic_problem_views(",
        labels=("Problem", "ProblemConcept"),
        relationships=(),
        properties=("embedding",),
        vector_indexes=("concept_embedding_idx", "problem_embedding_idx"),
        compat=CompatClass.PARITY,
        via=("semantic_problem_views",),
        note=(
            "Since #110 this unions vector search over canonical "
            "``concept_embedding_idx`` and legacy ``problem_embedding_idx`` so "
            "freshly ingested ProblemConcepts are found. Both index names are "
            "literals in repository.py, so the projection's DDL must recreate "
            "them under exactly these names. Not exercised by the deterministic "
            "baseline: it needs a live embedding provider."
        ),
    ),
    ReadPath(
        id="search.hybrid.topic_leg",
        surface=(
            "SearchService.hybrid_search(topic_id=...) "
            "-> Neo4jRepository.problem_ids_for_topic"
        ),
        source_file=f"{CORE}/knowledge_graph/search.py",
        snippet="self._repo.problem_ids_for_topic(topic_id)",
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id",),
        compat=CompatClass.DECLARED_CHANGE,
        via=("problem_ids_for_topic",),
        note=(
            "Resolves the topic's problem-id set up front, then filters the "
            "semantic hits against it. Since #110 the set is the concept+legacy "
            "union (direct BELONGS_TO or a mention from a RESEARCHES paper). An "
            "empty set silently drops every semantic result, so a topic-filtered "
            "hybrid search returns nothing today no matter how good the embedding "
            "match was."
        ),
    ),
    ReadPath(
        id="repo.list_problems",
        via=("list_problem_views", "list_problems", "get_problem_view"),
        surface=(
            "Neo4jRepository canonical problem reads "
            "(GET /api/problems, GET /api/problems/{id})"
        ),
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet=(
            "        MATCH (c:ProblemConcept)\n"
            "        WHERE ($status IS NULL OR c.status = $status)"
        ),
        labels=("Problem", "ProblemConcept", "ProblemMention"),
        relationships=("INSTANCE_OF",),
        properties=("id", "statement", "status", "created_at"),
        ordering=("c.created_at DESC", "p.created_at DESC"),
        compat=CompatClass.PARITY,
        note=(
            "Since #110 ``list_problem_views`` / ``get_problem_view`` union "
            "canonical ProblemConcept nodes with legacy :Problem nodes, and the "
            "router surfaces serve that union. The legacy half is unchanged on a "
            "legacy graph; the concept half is empty there, so the deterministic "
            "baseline records legacy only. Since #115 both legs also take an "
            "optional ``origin`` filter (a missing property is treated as "
            "``extracted``); with no filter the row set is unchanged."
        ),
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
        surface="GET /api/stats -> Neo4jRepository.get_problem_stats",
        source_file=f"{API}/main.py",
        snippet="stats = repo.get_problem_stats()",
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "RESEARCHES",
            "EXTRACTED_FROM",
            "INSTANCE_OF",
        ),
        properties=("name",),
        compat=CompatClass.DECLARED_CHANGE,
        via=("get_problem_stats",),
        note=(
            "``problems_by_topic`` is ``{}`` on any pipeline-built legacy graph "
            "for the BELONGS_TO half. Since #110 the count unions the canonical "
            "ProblemConcept read model and adds the paper-RESEARCHES leg, so the "
            "dashboard histogram becomes populated after cutover."
        ),
    ),
    ReadPath(
        id="api.stats.counts",
        surface="GET /api/stats (totals + by status) -> Neo4jRepository.get_problem_stats",
        source_file=f"{API}/main.py",
        snippet="stats = repo.get_problem_stats()",
        labels=("Problem", "ProblemConcept", "Paper", "Topic"),
        relationships=(),
        properties=("status",),
        compat=CompatClass.PARITY,
        via=("get_problem_stats",),
        note=(
            "Since #110 the problem total and by-status counts cover "
            "ProblemConcept OR Problem in one query. On a legacy graph that is "
            "exactly the :Problem count, so the baseline is unchanged. If "
            "canonical ledger rows ever carried one of these labels in the same "
            "database, this endpoint would silently over-count — see "
            "test_no_ledger_leak.py."
        ),
    ),
    ReadPath(
        id="api.graph.problems_by_topic",
        surface="GET /api/graph?topic_id=",
        source_file=f"{API}/routers/graph.py",
        snippet="WHERE (c)-[:BELONGS_TO]->(:Topic {id: $topic_id})",
        also=("MATCH (p:Problem)-[:BELONGS_TO]->(:Topic {id: $topic_id})",),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("canonical_statement", "statement", "status", "confidence"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Since #110 the topic branch unions ProblemConcept (direct "
            "BELONGS_TO or via a mention from a RESEARCHES paper) with legacy "
            ":Problem (direct BELONGS_TO only). LIMIT without ORDER BY — "
            "ordering is incidental (spec U-8)."
        ),
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
            "endpoint's output without touching its code. Since #110 this "
            "unfiltered query serves both the topic and unfiltered branches; the "
            "old topic-filtered relation leg was removed (its read-path entry and "
            "probe were retired with it)."
        ),
    ),
    ReadPath(
        id="api.graph.problems_papers",
        surface="GET /api/graph?include_papers=true",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (c:ProblemConcept)<-[:INSTANCE_OF]-(:ProblemMention)",
        also=("MATCH (p:Problem)-[:EXTRACTED_FROM]->(paper:Paper)",),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper"),
        relationships=("INSTANCE_OF", "EXTRACTED_FROM"),
        properties=("title", "doi", "year", "authors"),
        compat=CompatClass.PARITY,
        note="Concept-side paper leg reads ProblemMention->Paper; legacy leg unchanged.",
    ),
    ReadPath(
        id="api.graph.include_topics",
        surface="GET /api/graph?include_topics=true",
        source_file=f"{API}/routers/graph.py",
        snippet="MATCH (c:ProblemConcept)-[:BELONGS_TO]->(topic:Topic)",
        also=(
            "MATCH (c:ProblemConcept)<-[:INSTANCE_OF]-(:ProblemMention)",
            "MATCH (p:Problem)-[:BELONGS_TO]->(topic:Topic)",
        ),
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "name", "level", "problem_count"),
        compat=CompatClass.DECLARED_CHANGE,
        note=(
            "Since #110 unions three legs: concept BELONGS_TO, concept via a "
            "mention from a RESEARCHES paper, and legacy BELONGS_TO. Also reads "
            "Topic.problem_count, a denormalized counter the projection "
            "recomputes from edges (spec §4.4) — so the rendered value can change "
            "even where the edge set does not."
        ),
    ),
    ReadPath(
        id="api.graph.node_neighbourhood",
        surface="GET /api/graph/node/{node_id}",
        source_file=f"{API}/routers/graph.py",
        snippet="OPTIONAL MATCH (n)-[r]-(neighbor)",
        labels=(
            "Problem",
            "ProblemConcept",
            "ProblemMention",
            "Paper",
            "Topic",
        ),
        relationships=(),
        properties=("statement", "canonical_statement", "status"),
        compat=CompatClass.PARITY,
        note=(
            "UNTYPED and UNLABELLED neighbour pattern keyed on elementId — it "
            "renders whatever is adjacent to a ProblemConcept, ProblemMention, "
            "Problem, Paper or Topic. The strongest ledger-leak surface in the "
            "application; asserted behaviourally in test_no_ledger_leak.py."
        ),
    ),
    ReadPath(
        id="api.topics.problems",
        surface="GET /api/topics/{id}/problems -> Neo4jRepository.list_problem_views_for_topic",
        source_file=f"{API}/routers/topics.py",
        snippet="views = repo.list_problem_views_for_topic(",
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "statement", "status", "created_at"),
        compat=CompatClass.DECLARED_CHANGE,
        via=("list_problem_views_for_topic",),
        note=(
            "Since #110 serves the concept+legacy union: a problem is associated "
            "with the topic via a direct BELONGS_TO edge or, concept-side, a "
            "mention extracted from a paper that RESEARCHES it. LIMIT with no "
            "ORDER BY (spec U-8): row order is whatever the planner produces, so "
            "this endpoint has no stable pagination contract to preserve in the "
            "first place. The probe adds an ORDER BY so the baseline is "
            "reproducible, and that difference is deliberate."
        ),
    ),
    ReadPath(
        id="api.topics.problems_subtopics",
        surface=(
            "GET /api/topics/{id}/problems?include_subtopics=true "
            "-> Neo4jRepository.list_problem_views_for_topic"
        ),
        source_file=f"{API}/routers/topics.py",
        snippet="views = repo.list_problem_views_for_topic(",
        labels=("Problem", "ProblemConcept", "ProblemMention", "Paper", "Topic"),
        relationships=(
            "SUBTOPIC_OF",
            "BELONGS_TO",
            "INSTANCE_OF",
            "EXTRACTED_FROM",
            "RESEARCHES",
        ),
        properties=("id", "statement", "status"),
        compat=CompatClass.DECLARED_CHANGE,
        via=("list_problem_views_for_topic",),
        note=(
            "Variable-length SUBTOPIC_OF traversal resolves the descendant topic "
            "ids; the projection must keep the direction. The concept+legacy "
            "association union is the same as the direct-route sibling above."
        ),
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
            "consequence 2 used to claim 'Pagination is preserved' and was "
            "false as written; it has since been corrected to name this path "
            "and the three others below, so the spec and this classification "
            "now agree."
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
        snippet='concept_query = "MATCH (c:ProblemConcept) RETURN c LIMIT $limit"',
        also=('legacy_query = "MATCH (p:Problem) RETURN p LIMIT $limit"',),
        labels=("Problem", "ProblemConcept"),
        relationships=(),
        properties=("canonical_statement", "statement", "status", "confidence"),
        compat=CompatClass.PARITY,
        note=(
            "The unfiltered branch of the same endpoint, unioning canonical "
            "ProblemConcept with legacy :Problem since #110. Missing from the "
            "first cut — found by the completeness scan, not by reading. LIMIT "
            "with no ORDER BY."
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
        snippet=(
            "CALL {\n"
            "                    MATCH (from:Problem {id: $from_id}) RETURN from"
        ),
        also=("MATCH (from)-[r:{rel_type}]->(to)",),
        labels=("Problem", "ProblemConcept"),
        relationships=("EXTENDS", "CONTRADICTS", "DEPENDS_ON", "REFRAMES"),
        properties=("id",),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "Two reads that exist only inside a write path — they verify both "
            "endpoints exist and that the edge is not already present. Scoped "
            "out with the write they guard: §4.5 turns every mutation endpoint "
            "into a curation request, so the guard has no post-cutover "
            "equivalent. #115 label-scoped both matches to "
            "``(n:Problem OR n:ProblemConcept)``; a follow-up found that a "
            "label predicate in ``WHERE`` cannot use either per-label unique id "
            "index, so the shape is now a ``CALL { ... UNION ... }`` of two "
            "label-scoped ``NodeUniqueIndexSeek`` lookups per endpoint "
            "(``test_label_or_index_seek.py``). The read is recorded with that "
            "shape but stays scoped out. Inventoried rather than ignored because "
            "it is a read in a scanned module, and an un-inventoried read is how "
            "the completeness check gets hollowed out."
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
        id="agent.synthesis.derived_from_lineage",
        surface="Neo4jRepository.get_derived_from (synthesis provenance lineage)",
        source_file=f"{CORE}/knowledge_graph/repository.py",
        snippet="MATCH (from)-[r:DERIVED_FROM]->(to)",
        labels=("Problem", "ProblemConcept"),
        relationships=(),
        properties=("id",),
        compat=CompatClass.SCOPED_OUT,
        note=(
            "New in #115. Returns the ``DERIVED_FROM`` sources of an "
            "agent-derived problem (id + edge props). ``DERIVED_FROM`` is a "
            "provenance edge, not an extracted or projected relation — §4.4 "
            "projects fifteen relation types and this is not one of them — so "
            "it is held out of the parity contract rather than listed in "
            "``relationships`` (listing it would widen the "
            "only-REVIEWS-outside-the-contract invariant). A follow-up found "
            "that the #115 label predicate in ``WHERE`` cannot use either "
            "per-label unique id index; the source endpoint is now resolved by "
            "a ``CALL { ... UNION ... }`` of the two label-scoped "
            "``NodeUniqueIndexSeek`` lookups and the ``:Problem`` / "
            "``:ProblemConcept`` filter stays on the traversed target "
            "(``test_label_or_index_seek.py``). No router reaches it today; it "
            "is consumed by the synthesis write-back path."
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
#: ``test_scoped_out_write_surfaces.py`` asserts each remaining surface is
#: *still* broken, and asserts the repaired ones now bind correctly. That is
#: deliberate: if someone repairs a remaining one, the scope-out stops being
#: justified and the harness must be widened, so the test going red is the
#: notification.
#:
#: Two surfaces were retired here as they were repaired:
#:
#: * ``write.api.put_problem`` — removed in #110; the router now fetches a
#:   canonical view, writes a ProblemConcept through ``update_problem_concept``
#:   or a legacy Problem through ``update_problem``, and no longer makes the
#:   positional misbind. Its tripwire became the positive test
#:   ``test_put_problem_binds_its_arguments_correctly``.
#: * ``write.agent.synthesis`` and ``read.agent.continuation_related`` —
#:   removed in #115. ``SynthesisAgent`` now writes a real ``Problem`` and
#:   relations through the real signatures, and ``ContinuationAgent`` calls
#:   ``get_related_problems`` with accepted arguments and consumes the returned
#:   ``(Problem, ProblemRelation)`` tuples. Their tripwires became the positive
#:   tests ``test_synthesis_agent_binds_its_repository_writes_correctly`` /
#:   ``test_synthesis_agent_binds_its_create_relation_call_correctly`` and
#:   ``test_continuation_agent_binds_its_relation_context_call`` /
#:   ``test_continuation_agent_reads_the_relation_service_tuples``.
SCOPED_OUT_SURFACES: tuple[ScopedOutSurface, ...] = (
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
