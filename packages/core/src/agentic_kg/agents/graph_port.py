"""The graph source the research agents read, behind one port.

The agents used to reach into ``Neo4jRepository`` / ``SearchService`` /
``RelationService`` directly, which meant the only graph they could see was
the legacy projection. ADR-0009 makes the source a choice without forking the
agents:

* :class:`LegacyGraphPort` reproduces the pre-ADR behaviour exactly — the same
  repository, search and relation calls, no new semantics.
* :class:`CanonicalGraphPort` reads KGCS ``Canon__Identity`` records at the
  latest epoch through :mod:`agentic_kg.migration.canonical_read`. It is
  **read-only by construction**: it is handed a canonical reader, which is
  structurally not a ``GraphMutationStore`` (ADR-0010 / AC-1), and it never
  imports a write surface.

``AGENT_GRAPH_SOURCE`` selects the adapter (``legacy`` default, ``canonical``
opt-in). Nothing about the choice is inferred: an unknown value fails loudly
instead of silently falling back to legacy.

The canonical ranking score is computed here, deterministically, from the four
graph-native features the proposal names. The formula and its rationale are
documented in ``llm/governance/adr/0009-research-agents-canonical-graph.md``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ENV_AGENT_GRAPH_SOURCE = "AGENT_GRAPH_SOURCE"
SOURCE_LEGACY = "legacy"
SOURCE_CANONICAL = "canonical"
VALID_SOURCES = (SOURCE_LEGACY, SOURCE_CANONICAL)

#: Canonical entity type the agents rank. The ingestion ontology declares
#: ``Problem``; the legacy read model calls the same concept ``ProblemConcept``.
CANONICAL_PROBLEM_ENTITY_TYPE = "Problem"

#: Weights of the deterministic canonical ranking score. They sum to 1.0.
WEIGHT_EVIDENCE = 0.35
WEIGHT_DATA_AVAILABILITY = 0.25
WEIGHT_CONNECTIVITY = 0.20
WEIGHT_FRESHNESS = 0.20

#: A problem reaches "full" data availability at this many structured
#: dataset/metric/baseline assertions; more than that does not add signal.
DATA_AVAILABILITY_TARGET = 3

#: Connectivity saturates at this many assertion + neighbour endpoints.
CONNECTIVITY_TARGET = 10

#: Freshness is a one-year half-life: a problem recorded today scores ~1.0,
#: one recorded a year ago ~0.5, one recorded five years ago ~0.17.
FRESHNESS_HALF_LIFE_DAYS = 365.0


def graph_source_from_env(environ: dict[str, str] | None = None) -> str:
    """Return the configured graph source, validating it.

    An unknown value is a configuration error, not a reason to guess. Falling
    back to legacy would make a mis-typed canonical opt-in look like a healthy
    legacy run.
    """
    env = os.environ if environ is None else environ
    source = (env.get(ENV_AGENT_GRAPH_SOURCE) or SOURCE_LEGACY).strip().lower()
    if source not in VALID_SOURCES:
        raise ValueError(f"{ENV_AGENT_GRAPH_SOURCE}={source!r} is not one of {VALID_SOURCES}")
    return source


class GraphProblem(BaseModel):
    """A candidate problem as the agents consume it, with graph-native facts."""

    problem_id: str
    statement: str
    status: str = "open"
    confidence: float | None = None
    topic_id: str | None = None
    entity_type: str = CANONICAL_PROBLEM_ENTITY_TYPE

    # Graph-native features (populated by the canonical adapter; ``None`` on
    # legacy, where the agents keep the historical LLM-only ranking).
    age_days: float | None = None
    assertion_count: int = 0
    dataset_count: int = 0
    metric_count: int = 0
    baseline_count: int = 0
    evidence_strength: float | None = None
    data_availability: float | None = None
    connectivity: float | None = None
    freshness: float | None = None
    graph_native_score: float | None = None

    # Provenance the agent used to produce its output.
    assertion_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    assertion_evidence: dict[str, list[str]] = Field(default_factory=dict)


class MetricInfo(BaseModel):
    name: str
    baseline_value: float | None = None
    description: str | None = None


class DatasetInfo(BaseModel):
    name: str
    available: bool = False
    url: str | None = None


class ProblemContext(BaseModel):
    """Everything the continuation / evaluation agents need about one problem."""

    problem_id: str
    statement: str
    topic: str = "unspecified"
    status: str = "open"
    constraints: list[str] = Field(default_factory=list)
    datasets: list[DatasetInfo] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[MetricInfo] = Field(default_factory=list)
    related_problems: list[str] = Field(default_factory=list)
    assertion_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    assertion_evidence: dict[str, list[str]] = Field(default_factory=dict)


class GraphPort(Protocol):
    """The graph operations the research agents depend on."""

    source: str

    def list_candidates(
        self,
        *,
        topic_filter: str | None,
        status_filter: str | None,
        max_problems: int,
    ) -> list[GraphProblem]:
        """Ranking candidates matching the workflow filters."""

    def load_context(self, problem_id: str) -> ProblemContext:
        """Full problem context for continuation and evaluation."""


# =============================================================================
# Legacy adapter
# =============================================================================


class LegacyGraphPort:
    """The historical Neo4j/search/relation reads, unchanged."""

    source = SOURCE_LEGACY

    def __init__(
        self,
        repository: Any,
        search_service: Any | None = None,
        relation_service: Any | None = None,
    ) -> None:
        self._repo = repository
        self._search = search_service
        self._relations = relation_service

    def list_candidates(
        self,
        *,
        topic_filter: str | None,
        status_filter: str | None,
        max_problems: int,
    ) -> list[GraphProblem]:
        status = status_filter or "open"
        if self._search:
            if topic_filter:
                results = self._search.structured_search(
                    topic_id=topic_filter, status=status, top_k=max_problems
                )
            else:
                results = self._search.structured_search(status=status, top_k=max_problems)
            problems = [r.problem for r in results]
        else:
            problems = self._repo.list_problems(status=status, limit=max_problems)

        out: list[GraphProblem] = []
        for problem in problems:
            confidence = None
            metadata = getattr(problem, "extraction_metadata", None)
            if metadata is not None:
                confidence = getattr(metadata, "confidence_score", None)
            out.append(
                GraphProblem(
                    problem_id=problem.id,
                    statement=problem.statement,
                    status=(problem.status.value if getattr(problem, "status", None) else "open"),
                    confidence=confidence,
                )
            )
        return out

    def load_context(self, problem_id: str) -> ProblemContext:
        problem = self._repo.get_problem(problem_id)
        context = ProblemContext(
            problem_id=problem.id,
            statement=problem.statement,
            topic=self._lookup_topic_name(problem.id),
            status=problem.status.value if problem.status else "open",
        )
        for c in problem.constraints or []:
            context.constraints.append(f"[{c.type.value}] {c.text}")
        for d in problem.datasets or []:
            context.datasets.append(
                DatasetInfo(name=d.name, available=bool(d.available), url=d.url)
            )
        for b in problem.baselines or []:
            doi = f" (DOI: {b.paper_doi})" if b.paper_doi else ""
            context.baselines.append(f"{b.name}{doi}")
        for m in problem.metrics or []:
            context.metrics.append(
                MetricInfo(
                    name=m.name,
                    baseline_value=m.baseline_value,
                    description=m.description,
                )
            )
        if self._relations:
            try:
                related = self._relations.get_related_problems(problem_id, direction="both")
                for related_problem, relation in related:
                    context.related_problems.append(
                        f"[{relation.relation_type.value}] {related_problem.statement}"
                    )
            except Exception as exc:  # noqa: BLE001 - context is best effort
                logger.warning("Could not load related problems: %s", exc)
        return context

    def _lookup_topic_name(self, problem_id: str) -> str:
        """Return the Topic name for a Problem, or 'unspecified' if none."""
        try:
            with self._repo.session() as session:
                result = session.run(
                    """
                    MATCH (n {id: $id})
                    WHERE n:Problem OR n:ProblemConcept
                    OPTIONAL MATCH (n)-[:BELONGS_TO]->(t1:Topic)
                    OPTIONAL MATCH (n)<-[:INSTANCE_OF]-(:ProblemMention)
                          -[:EXTRACTED_FROM]->(:Paper)-[:RESEARCHES]->(t2:Topic)
                    WITH coalesce(t1, t2) AS t
                    WHERE t IS NOT NULL
                    RETURN t.name AS name
                    LIMIT 1
                    """,
                    id=problem_id,
                )
                record = result.single()
                name = record["name"] if record else None
                return name if isinstance(name, str) and name else "unspecified"
        except Exception:  # noqa: BLE001 - a missing topic is not an agent failure
            return "unspecified"


# =============================================================================
# Canonical adapter
# =============================================================================


class CanonicalGraphPort:
    """Read-only adapter over the KGCS canonical graph at the latest epoch."""

    source = SOURCE_CANONICAL

    def __init__(self, reader: Any, *, now: datetime | None = None) -> None:
        self._reader = reader
        self._now = now or datetime.now(timezone.utc)

    def list_candidates(
        self,
        *,
        topic_filter: str | None,
        status_filter: str | None,
        max_problems: int,
    ) -> list[GraphProblem]:
        from agentic_kg.migration.canonical_read import (
            MAX_LIMIT,
            entity_detail,
            list_entities,
        )

        page = list_entities(
            self._reader,
            entity_type=CANONICAL_PROBLEM_ENTITY_TYPE,
            limit=MAX_LIMIT,
        )
        problems: list[GraphProblem] = []
        for entity in page.get("entities", []):
            identity_id = str(entity.get("identity_id") or "")
            if not identity_id:
                continue
            detail = entity_detail(self._reader, identity_id)
            if detail is None:
                continue
            problem = self._problem_from_detail(detail)
            if status_filter and problem.status != status_filter:
                continue
            if topic_filter and problem.topic_id != topic_filter:
                continue
            problems.append(problem)
            if len(problems) >= max_problems:
                break
        return problems

    def load_context(self, problem_id: str) -> ProblemContext:
        from agentic_kg.migration.canonical_read import entity_detail

        detail = entity_detail(self._reader, problem_id)
        if detail is None:
            raise KeyError(f"canonical problem not visible: {problem_id}")
        problem = self._problem_from_detail(detail)
        context = ProblemContext(
            problem_id=problem.problem_id,
            statement=problem.statement,
            topic=problem.topic_id or "unspecified",
            status=problem.status,
            assertion_ids=problem.assertion_ids,
            evidence_ids=problem.evidence_ids,
            assertion_evidence=problem.assertion_evidence,
        )
        for assertion in detail.get("assertions", []):
            predicate = str(assertion.get("predicate") or "").lower()
            value = assertion.get("object_value")
            if predicate == "constraint" and value is not None:
                context.constraints.append(str(value))
            elif predicate == "dataset" and value is not None:
                context.datasets.append(DatasetInfo(name=str(value), available=True))
            elif predicate == "baseline" and value is not None:
                context.baselines.append(str(value))
            elif predicate == "metric" and value is not None:
                baseline = None
                if isinstance(value, dict):
                    baseline = value.get("baseline_value")
                    name = str(value.get("name") or "")
                else:
                    name = str(value)
                context.metrics.append(MetricInfo(name=name, baseline_value=baseline))
            elif predicate in {"related_to", "extends", "belongs_to"}:
                target = assertion.get("object_identity")
                if target and target != problem_id:
                    context.related_problems.append(str(target))
        return context

    def _problem_from_detail(self, detail: dict[str, Any]) -> GraphProblem:
        assertions = [a for a in detail.get("assertions", []) if isinstance(a, dict)]
        assertion_ids = [str(a["assertion_id"]) for a in assertions if a.get("assertion_id")]
        evidence_ids = [str(e) for e in detail.get("evidence_refs", [])]
        assertion_evidence: dict[str, list[str]] = {}
        for assertion in assertions:
            aid = assertion.get("assertion_id")
            if not aid:
                continue
            refs = [
                str(ref.get("evidence_id"))
                for ref in assertion.get("evidence_refs") or []
                if isinstance(ref, dict) and ref.get("evidence_id")
            ]
            assertion_evidence[str(aid)] = refs

        status = "open"
        topic_id = None
        datasets = metrics = baselines = 0
        object_ids: set[str] = set()
        for assertion in assertions:
            predicate = str(assertion.get("predicate") or "").lower()
            if predicate == "status" and assertion.get("object_value") is not None:
                status = str(assertion["object_value"])
            if predicate == "belongs_to" and assertion.get("object_identity"):
                topic_id = str(assertion["object_identity"])
            if predicate == "dataset":
                datasets += 1
            elif predicate == "metric":
                metrics += 1
            elif predicate == "baseline":
                baselines += 1
            if assertion.get("object_identity"):
                object_ids.add(str(assertion["object_identity"]))

        assertion_count = len(assertions)
        evidence_strength = (
            min(1.0, len(set(evidence_ids)) / assertion_count) if assertion_count else 0.0
        )
        data_availability = min(1.0, (datasets + metrics + baselines) / DATA_AVAILABILITY_TARGET)
        connectivity = min(1.0, (assertion_count + len(object_ids)) / CONNECTIVITY_TARGET)
        age_days = self._age_days(detail.get("created_at"))
        freshness = 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS)
        graph_score = canonical_graph_score(
            evidence_strength=evidence_strength,
            data_availability=data_availability,
            connectivity=connectivity,
            freshness=freshness,
        )

        statement = (
            detail.get("display_name")
            or self._statement_from_assertions(assertions)
            or str(detail.get("identity_id") or "")
        )
        return GraphProblem(
            problem_id=str(detail.get("identity_id") or ""),
            statement=str(statement),
            status=status,
            confidence=evidence_strength,
            topic_id=topic_id,
            entity_type=str(detail.get("entity_type") or CANONICAL_PROBLEM_ENTITY_TYPE),
            age_days=age_days,
            assertion_count=assertion_count,
            dataset_count=datasets,
            metric_count=metrics,
            baseline_count=baselines,
            evidence_strength=evidence_strength,
            data_availability=data_availability,
            connectivity=connectivity,
            freshness=freshness,
            graph_native_score=graph_score,
            assertion_ids=assertion_ids,
            evidence_ids=evidence_ids,
            assertion_evidence=assertion_evidence,
        )

    @staticmethod
    def _statement_from_assertions(assertions: list[dict[str, Any]]) -> str | None:
        for assertion in assertions:
            if str(assertion.get("predicate") or "").lower() == "statement":
                value = assertion.get("object_value")
                if value is not None:
                    return str(value)
        return None

    def _age_days(self, created_at: Any) -> float:
        if not isinstance(created_at, datetime):
            return 0.0
        moment = created_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        delta = self._now - moment
        return max(0.0, delta.total_seconds() / 86400.0)


def canonical_graph_score(
    *,
    evidence_strength: float,
    data_availability: float,
    connectivity: float,
    freshness: float,
) -> float:
    """The documented deterministic canonical ranking score.

    ``0.35*evidence + 0.25*data + 0.20*connectivity + 0.20*freshness``, each
    term in ``[0, 1]``. Evidence and data carry the most weight because they
    say whether the problem is *workable*; connectivity and freshness break
    ties toward problems the graph and the literature are still talking about.
    """
    score = (
        WEIGHT_EVIDENCE * evidence_strength
        + WEIGHT_DATA_AVAILABILITY * data_availability
        + WEIGHT_CONNECTIVITY * connectivity
        + WEIGHT_FRESHNESS * freshness
    )
    return round(max(0.0, min(1.0, score)), 4)


# =============================================================================
# Selection
# =============================================================================


@dataclass
class GraphPorts:
    """The port plus the optional synthesis review sink for one source."""

    port: GraphPort
    source: str
    synthesis_sink: Any | None = None
    reader: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def build_graph_port(
    source: str,
    *,
    repository: Any,
    search_service: Any | None = None,
    relation_service: Any | None = None,
    reader: Any | None = None,
) -> GraphPort:
    """Build the adapter named by ``source`` (validated)."""
    if source == SOURCE_LEGACY:
        return LegacyGraphPort(repository, search_service, relation_service)
    if source == SOURCE_CANONICAL:
        if reader is None:
            from agentic_kg.migration.canonical_read import (
                canonical_read_target_from_env,
                open_canonical_reader,
            )

            reader = open_canonical_reader(canonical_read_target_from_env())
        return CanonicalGraphPort(reader)
    raise ValueError(f"unknown graph source: {source!r}")


def format_problems(candidates: list[GraphProblem]) -> str:
    """Format problems as numbered text for the LLM, with graph facts if any."""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. (ID: {c.problem_id})\n   Statement: {c.statement}\n   Status: {c.status}"
        )
        if c.graph_native_score is not None:
            lines.append(
                "   Graph features: "
                f"age_days={c.age_days:.1f}, "
                f"datasets={c.dataset_count}, metrics={c.metric_count}, "
                f"baselines={c.baseline_count}, "
                f"evidence_strength={c.evidence_strength:.2f}, "
                f"connectivity={c.connectivity:.2f}, "
                f"graph_score={c.graph_native_score:.2f}"
            )
    return "\n\n".join(lines)


def blend_canonical_score(llm_score: float, graph_score: float) -> float:
    """Blend the LLM judgement with the deterministic graph-native score.

    The LLM still reads the statements; the graph supplies facts the text does
    not (how much evidence backs the problem, whether datasets/metrics exist,
    how connected and how recent it is). The 60/40 split is a documented
    decision, not a tuned constant: neither source alone is authoritative.
    """
    return round(max(0.0, min(1.0, 0.6 * llm_score + 0.4 * graph_score)), 4)


def age_from_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (datetime.now(timezone.utc) - moment).total_seconds() / 86400.0,
    )


__all__ = [
    "CANONICAL_PROBLEM_ENTITY_TYPE",
    "ENV_AGENT_GRAPH_SOURCE",
    "FRESHNESS_HALF_LIFE_DAYS",
    "GraphPort",
    "GraphPorts",
    "GraphProblem",
    "LegacyGraphPort",
    "CanonicalGraphPort",
    "DatasetInfo",
    "MetricInfo",
    "ProblemContext",
    "SOURCE_CANONICAL",
    "SOURCE_LEGACY",
    "VALID_SOURCES",
    "age_from_iso",
    "blend_canonical_score",
    "build_graph_port",
    "canonical_graph_score",
    "format_problems",
    "graph_source_from_env",
]
