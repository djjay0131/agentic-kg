"""Deterministic probes over a graph surface, plus the anti-vacuity guard.

A *probe* captures what an application read path retrieves **before any model
call**. That is the only thing worth comparing across the cutover: the owner's
rule for this phase is *compare deterministic retrieval/context, not exact prose
from live LLMs*, and a probe is the machine-checkable half of that sentence.

Every probe is the same shape — Cypher plus parameters, issued through a
:class:`GraphSurface` — so the identical probe set runs against the legacy graph
today and against the canonical projection later. Nothing here imports the
projector or the canonical store; a surface is anything that can run read-only
Cypher.

The anti-vacuity guard
----------------------
Mapping spec §9.0 obligation 5: *a criterion must be able to fail for the reason
it names.* The specific way a compatibility suite fails that obligation is
well-documented here — eight read paths traverse an edge no writer produces, so
"legacy == projection" is satisfied by ``[] == []`` and proves nothing.

:func:`require_non_vacuous` and :func:`assert_parity` make that unrepresentable:
a comparison cannot be *performed* on an empty side, because the emptiness check
raises :class:`VacuousProbe` **before** any equality is evaluated. The order
matters — a guard written as ``assert a == b and a`` would still let the
equality pass first.

``test_anti_vacuity_guard.py`` proves the guard by pointing the entire probe set
at an empty graph and asserting every probe raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CYPHER_PROBES",
    "CypherProbe",
    "GraphSurface",
    "ProbeInputs",
    "ProbeResult",
    "VacuousProbe",
    "assert_parity",
    "canonicalise",
    "require_non_vacuous",
    "run_all_probes",
    "run_probe",
]


class VacuousProbe(AssertionError):
    """A compatibility assertion was attempted over an empty result set.

    Raised *instead of* performing the comparison, never after it. An
    ``AssertionError`` subclass so pytest reports it as a failed assertion
    rather than an error in the harness.
    """


@runtime_checkable
class GraphSurface(Protocol):
    """Anything that can run read-only Cypher and return plain dict rows.

    Deliberately minimal: the legacy adapter wraps ``Neo4jRepository.session()``
    and a future projection adapter wraps the projection driver. Neither has to
    know about the other, and neither is a write surface.
    """

    @property
    def name(self) -> str:
        """Identifies the surface in failure messages ('legacy', 'projection')."""
        ...

    def run(self, cypher: str, /, **params: Any) -> list[dict[str, Any]]:
        """Execute a read query and return its rows in result order."""
        ...


@dataclass(frozen=True)
class ProbeInputs:
    """The bound parameters for one frozen research query.

    Held separately from the probes so the same probe definitions can be
    replayed against a different fixture without editing the Cypher.
    """

    topic_id: str
    root_topic_id: str
    problem_id: str
    paper_doi: str
    concept_id: str
    trace_id: str
    status: str = "open"
    limit: int = 20
    offset: int = 0

    def bind(self, needs: tuple[str, ...]) -> dict[str, Any]:
        """Project just the parameters one probe declares it needs."""
        available = {
            "topic_id": self.topic_id,
            "root_topic_id": self.root_topic_id,
            "problem_id": self.problem_id,
            "paper_doi": self.paper_doi,
            "concept_id": self.concept_id,
            "trace_id": self.trace_id,
            "status": self.status,
            "limit": self.limit,
            "offset": self.offset,
        }
        return {key: available[key] for key in needs}


@dataclass(frozen=True)
class CypherProbe:
    """One read path, reduced to the query the application actually issues.

    ``read_path_ids`` ties the probe back to
    :data:`agentic_kg.migration.compat.read_paths.READ_PATHS`, and a test
    asserts that every id resolves — so a probe can never describe a read path
    the inventory does not know about, and vice versa. It is a tuple because
    several read paths issue the same query: the Ranking, Continuation,
    Evaluation and Synthesis agents all reach ``Neo4jRepository.get_problem``,
    and a probe that pretended to cover only one of them would leave the other
    three looking unproven.
    """

    id: str
    read_path_ids: tuple[str, ...]
    cypher: str
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    """A probe's rows, in result order, canonicalised for comparison."""

    probe_id: str
    surface: str
    rows: tuple[dict[str, Any], ...]

    def __len__(self) -> int:
        return len(self.rows)


#: The probe set. Each Cypher body is the query the cited application code
#: issues, reduced to the properties that code actually consumes — a probe that
#: returned whole nodes would fail on volatile properties (``ingested_at``,
#: ``embedding``) that no reader looks at.
CYPHER_PROBES: tuple[CypherProbe, ...] = (
    # --- agents -------------------------------------------------------
    CypherProbe(
        id="ranking.candidates",
        read_path_ids=("agent.ranking.candidates",),
        # No status predicate, and that is not a simplification. `ResearchState`
        # carries `status_filter=None` by default, so `state.get(...)` returns
        # None rather than the "open" default written next to it, and the agent
        # reaches `structured_search(status=None)` -- the unfiltered query.
        # When a caller *does* set status_filter, the agent raises instead of
        # filtering: defect D-4, recorded on the read-path entry.
        cypher=(
            "MATCH (p:Problem) "
            "RETURN p.id AS id, p.statement AS statement, p.status AS status "
            "ORDER BY p.created_at DESC LIMIT $limit"
        ),
        needs=("limit",),
    ),
    CypherProbe(
        id="search.by_status",
        read_path_ids=("search.structured.by_status",),
        cypher=(
            "MATCH (p:Problem) WHERE p.status = $status "
            "RETURN p.id AS id, p.statement AS statement, p.status AS status "
            "ORDER BY p.created_at DESC LIMIT $limit"
        ),
        needs=("status", "limit"),
    ),
    CypherProbe(
        id="ranking.candidates_by_topic",
        read_path_ids=("agent.ranking.candidates_by_topic", "search.structured.by_topic"),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id}) "
            "RETURN p.id AS id, p.statement AS statement, p.status AS status "
            "ORDER BY p.created_at DESC LIMIT $limit"
        ),
        needs=("topic_id", "limit"),
    ),
    CypherProbe(
        id="continuation.topic_name",
        read_path_ids=("agent.continuation.topic_name",),
        cypher=(
            "MATCH (p:Problem {id: $problem_id})-[:BELONGS_TO]->(t:Topic) "
            "RETURN t.name AS name LIMIT 1"
        ),
        needs=("problem_id",),
    ),
    CypherProbe(
        id="continuation.problem_context",
        read_path_ids=(
            "agent.continuation.problem_context",
            "agent.evaluation.problem_context",
            "agent.synthesis.problem_context",
        ),
        cypher=(
            "MATCH (p:Problem {id: $problem_id}) "
            "RETURN p.id AS id, p.statement AS statement, p.status AS status, "
            "p.constraints AS constraints, p.datasets AS datasets, "
            "p.baselines AS baselines, p.metrics AS metrics"
        ),
        needs=("problem_id",),
    ),
    # --- retrieval ----------------------------------------------------
    CypherProbe(
        id="search.hybrid_topic_leg",
        read_path_ids=("search.hybrid.topic_leg",),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id}) "
            "RETURN p.id AS id ORDER BY p.id"
        ),
        needs=("topic_id",),
    ),
    CypherProbe(
        id="search.by_year",
        read_path_ids=("search.structured.by_year",),
        cypher=(
            "MATCH (p:Problem) MATCH (p)-[:EXTRACTED_FROM]->(paper:Paper) "
            "WHERE paper.year >= 1900 "
            "RETURN p.id AS id, paper.doi AS doi, paper.year AS year "
            "ORDER BY p.created_at DESC LIMIT $limit"
        ),
        needs=("limit",),
    ),
    CypherProbe(
        id="repo.list_problems",
        read_path_ids=("repo.list_problems",),
        cypher=(
            "MATCH (p:Problem) WHERE p.status = $status "
            "RETURN p.id AS id, p.statement AS statement "
            "ORDER BY p.created_at DESC SKIP $offset LIMIT $limit"
        ),
        needs=("status", "offset", "limit"),
    ),
    CypherProbe(
        id="relations.paper_authors",
        read_path_ids=("relations.paper_authors",),
        cypher=(
            "MATCH (paper:Paper {doi: $paper_doi})-[r:AUTHORED_BY]->(a:Author) "
            "RETURN a.id AS id, a.name AS name, r.author_position AS position "
            "ORDER BY r.author_position"
        ),
        needs=("paper_doi",),
    ),
    # --- API routers --------------------------------------------------
    CypherProbe(
        id="api.stats.problems_by_topic",
        read_path_ids=("api.stats.problems_by_topic",),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic) "
            "RETURN t.name AS name, count(p) AS count ORDER BY t.name"
        ),
    ),
    CypherProbe(
        id="api.stats.by_status",
        read_path_ids=("api.stats.counts",),
        cypher=(
            "MATCH (p:Problem) RETURN p.status AS status, count(p) AS count "
            "ORDER BY p.status"
        ),
    ),
    CypherProbe(
        id="api.graph.problems_by_topic",
        read_path_ids=("api.graph.problems_by_topic",),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id}) "
            "RETURN p.statement AS statement, p.status AS status ORDER BY p.statement "
            "LIMIT $limit"
        ),
        needs=("topic_id", "limit"),
    ),
    CypherProbe(
        id="api.graph.problem_relations",
        read_path_ids=("api.graph.problem_relations",),
        cypher=(
            "MATCH (p1:Problem)-[r]->(p2:Problem) "
            "RETURN p1.id AS source, type(r) AS rel_type, p2.id AS target "
            "ORDER BY source, rel_type, target LIMIT $limit"
        ),
        needs=("limit",),
    ),
    CypherProbe(
        id="api.graph.problems_papers",
        read_path_ids=("api.graph.problems_papers",),
        cypher=(
            "MATCH (p:Problem)-[r:EXTRACTED_FROM]->(paper:Paper) "
            "RETURN p.id AS problem_id, paper.doi AS doi, paper.title AS title, "
            "paper.year AS year ORDER BY problem_id, doi LIMIT $limit"
        ),
        needs=("limit",),
    ),
    CypherProbe(
        id="api.graph.include_topics",
        read_path_ids=("api.graph.include_topics",),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic) "
            "RETURN p.id AS problem_id, t.id AS topic_id, t.name AS name, "
            "t.level AS level, t.problem_count AS problem_count "
            "ORDER BY problem_id, topic_id"
        ),
    ),
    CypherProbe(
        id="api.topics.problems",
        read_path_ids=("api.topics.problems",),
        cypher=(
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id}) "
            "RETURN p.id AS id, p.statement AS statement ORDER BY p.id LIMIT $limit"
        ),
        needs=("topic_id", "limit"),
    ),
    CypherProbe(
        id="api.topics.problems_subtopics",
        read_path_ids=("api.topics.problems_subtopics",),
        cypher=(
            "MATCH (descendant:Topic)-[:SUBTOPIC_OF*0..]->(root:Topic {id: $root_topic_id}) "
            "WITH collect(descendant) AS topics "
            "MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic) WHERE t IN topics "
            "RETURN DISTINCT p.id AS id, p.statement AS statement "
            "ORDER BY id LIMIT $limit"
        ),
        needs=("root_topic_id", "limit"),
    ),
    CypherProbe(
        id="api.concepts.list",
        read_path_ids=("api.concepts.list",),
        cypher=(
            "MATCH (rc:ResearchConcept) "
            "RETURN rc.id AS id, rc.name AS name, rc.mention_count AS mention_count, "
            "rc.paper_count AS paper_count "
            "ORDER BY rc.mention_count DESC, rc.name SKIP $offset LIMIT $limit"
        ),
        needs=("offset", "limit"),
    ),
    CypherProbe(
        id="api.models.list",
        read_path_ids=("api.models.list",),
        cypher=(
            "MATCH (m:Model) "
            "RETURN m.id AS id, m.name AS name, m.is_canonical AS is_canonical, "
            "m.usage_count AS usage_count "
            "ORDER BY m.is_canonical DESC, m.usage_count DESC, m.name "
            "SKIP $offset LIMIT $limit"
        ),
        needs=("offset", "limit"),
    ),
    CypherProbe(
        id="api.methods.list",
        read_path_ids=("api.methods.list",),
        cypher=(
            "MATCH (m:Method) "
            "RETURN m.id AS id, m.name AS name, m.usage_count AS usage_count "
            "ORDER BY m.usage_count DESC, m.name SKIP $offset LIMIT $limit"
        ),
        needs=("offset", "limit"),
    ),
    CypherProbe(
        id="api.papers.list",
        read_path_ids=("api.papers.list",),
        cypher=(
            "MATCH (p:Paper) "
            "RETURN p.doi AS doi, p.title AS title, p.year AS year, "
            "p.is_stub AS is_stub, p.citation_count AS citation_count "
            "ORDER BY p.year DESC SKIP $offset LIMIT $limit"
        ),
        needs=("offset", "limit"),
    ),
    CypherProbe(
        id="api.concepts.linked_problems",
        read_path_ids=("api.concepts.linked_problems",),
        cypher=(
            "MATCH (pc:ProblemConcept)-[:INVOLVES_CONCEPT]->(rc:ResearchConcept {id: $concept_id}) "
            "RETURN pc.id AS id, pc.canonical_statement AS canonical_statement, "
            "pc.mention_count AS mention_count ORDER BY pc.mention_count DESC"
        ),
        needs=("concept_id",),
    ),
    CypherProbe(
        id="api.concepts.linked_papers",
        read_path_ids=("api.concepts.linked_papers",),
        cypher=(
            "MATCH (p:Paper)-[:DISCUSSES]->(rc:ResearchConcept {id: $concept_id}) "
            "RETURN p.doi AS doi, p.title AS title, p.year AS year "
            "ORDER BY coalesce(p.year, 0) DESC"
        ),
        needs=("concept_id",),
    ),
    CypherProbe(
        id="api.ingest.run_status",
        read_path_ids=("api.ingest.run_status",),
        cypher=(
            "MATCH (r:IngestionRun {trace_id: $trace_id}) "
            "RETURN r.trace_id AS trace_id, r.status AS status, "
            "r.papers_found AS papers_found, r.papers_imported AS papers_imported"
        ),
        needs=("trace_id",),
    ),
    CypherProbe(
        id="api.graph.node_neighbourhood",
        read_path_ids=("api.graph.node_neighbourhood",),
        cypher=(
            "MATCH (p:Problem {id: $problem_id}) "
            "OPTIONAL MATCH (p)-[r]-(neighbor) "
            "RETURN type(r) AS rel_type, labels(neighbor) AS neighbour_labels "
            "ORDER BY rel_type, neighbour_labels"
        ),
        needs=("problem_id",),
    ),
)


def canonicalise(rows: list[dict[str, Any]], token: str) -> tuple[dict[str, Any], ...]:
    """Strip the per-run fixture token so results are comparable to a baseline.

    The fixture mints a fresh token per run (ids and DOIs must be unique inside
    one Neo4j), so raw rows differ run to run in exactly one way. Replacing the
    token with a placeholder removes that difference and nothing else — it is
    not a normalisation that could hide a real diff, because any value not
    containing the token is passed through untouched.
    """
    def _clean(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(token, "<T>")
        if isinstance(value, list):
            return [_clean(item) for item in value]
        return value

    return tuple({key: _clean(val) for key, val in row.items()} for row in rows)


def run_probe(
    surface: GraphSurface, probe: CypherProbe, inputs: ProbeInputs, *, token: str
) -> ProbeResult:
    """Execute one probe against one surface."""
    rows = surface.run(probe.cypher, **inputs.bind(probe.needs))
    return ProbeResult(
        probe_id=probe.id,
        surface=surface.name,
        rows=canonicalise(rows, token),
    )


def run_all_probes(
    surface: GraphSurface, inputs: ProbeInputs, *, token: str
) -> dict[str, ProbeResult]:
    """Execute the whole probe set against one surface."""
    return {
        probe.id: run_probe(surface, probe, inputs, token=token)
        for probe in CYPHER_PROBES
    }


def require_non_vacuous(result: ProbeResult) -> ProbeResult:
    """Return ``result``, or raise if it is empty.

    This is the anti-vacuity guard in its single-sided form. Call it on any
    result a test is about to draw a conclusion from.
    """
    if not result.rows:
        raise VacuousProbe(
            f"probe {result.probe_id!r} returned no rows on surface "
            f"{result.surface!r}. An assertion over an empty result cannot fail "
            f"for the reason it names (mapping spec §9.0 obligation 5). Either "
            f"the fixture does not populate this read path, or the read path is "
            f"one of the DECLARED_CHANGE set that legacy cannot satisfy at all."
        )
    return result


def assert_parity(baseline: ProbeResult, observed: ProbeResult) -> None:
    """Assert two surfaces agree — and refuse to compare empty sides.

    The emptiness check runs on **both** sides **before** the equality, so
    ``[] == []`` can never be reported as parity. That ordering is the whole
    point: a guard applied after the comparison would let the vacuous case pass
    and only then complain.
    """
    require_non_vacuous(baseline)
    require_non_vacuous(observed)
    if baseline.rows != observed.rows:
        raise AssertionError(
            f"probe {baseline.probe_id!r} diverged between "
            f"{baseline.surface!r} and {observed.surface!r}:\n"
            f"  {baseline.surface}: {baseline.rows!r}\n"
            f"  {observed.surface}: {observed.rows!r}"
        )
