"""Persistence for research-progression cycles in Neo4j.

The node label is ``:Canon__ResearchCycle`` under the canonical ``ns``
namespace (ADR-0004/0005): app-namespace workflow state, never canonical graph
content. One node per scheduling tick, keyed by ``uid`` (``ns`` + cycle id).

Reads are tolerant of a JSON string that was not decoded by an older writer
(``decode_json_field``), matching the ``PipelineRun`` / ``ReviewItem``
convention.
"""

from __future__ import annotations

from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.models.research_cycle import ResearchCycle
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    RepositoryError,
    get_repository,
)

LABEL_RESEARCH_CYCLE = "Canon__ResearchCycle"

#: Default page size for ``list``.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ResearchCycleNotFoundError(RepositoryError):
    """Raised when a cycle id is not present in the graph."""


class ResearchCycleRepository:
    """Read/write access to ``:Canon__ResearchCycle`` nodes for one namespace."""

    def __init__(self, repository: Neo4jRepository, *, namespace: str = "canon") -> None:
        self._repo = repository
        self.namespace = namespace

    def save(self, cycle: ResearchCycle) -> ResearchCycle:
        """Upsert a cycle by ``uid``."""
        if cycle.namespace != self.namespace:
            raise RepositoryError(
                f"cycle namespace {cycle.namespace!r} does not match this "
                f"store's namespace {self.namespace!r}"
            )
        props = cycle.to_neo4j_properties()

        def _save(tx: ManagedTransaction) -> None:
            tx.run(
                f"MERGE (c:{LABEL_RESEARCH_CYCLE} {{uid: $uid}}) SET c = $props",
                uid=cycle.uid,
                props=props,
            )

        with self._repo.session() as session:
            session.execute_write(_save)
        return cycle

    def get(self, cycle_id: str) -> ResearchCycle:
        """Return one cycle, or raise ``ResearchCycleNotFoundError``."""

        def _get(tx: ManagedTransaction) -> Optional[dict]:
            record = tx.run(
                f"MATCH (c:{LABEL_RESEARCH_CYCLE} {{ns: $ns, cycle_id: $cycle_id}}) "
                "RETURN c LIMIT 1",
                ns=self.namespace,
                cycle_id=cycle_id,
            ).single()
            return dict(record["c"]) if record else None

        with self._repo.session() as session:
            props = session.execute_read(_get)
        if props is None:
            raise ResearchCycleNotFoundError(f"ResearchCycle not found: {cycle_id}")
        return ResearchCycle.from_neo4j_properties(props)

    def find_status(self, statuses: tuple[str, ...]) -> list[ResearchCycle]:
        """Cycles in ``statuses``, newest first (used for the running guard)."""

        def _find(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                f"MATCH (c:{LABEL_RESEARCH_CYCLE} {{ns: $ns}}) "
                "WHERE c.status IN $statuses "
                "RETURN c ORDER BY c.started_at DESC, c.cycle_id DESC",
                ns=self.namespace,
                statuses=list(statuses),
            )
            return [dict(record["c"]) for record in result]

        with self._repo.session() as session:
            rows = session.execute_read(_find)
        return [ResearchCycle.from_neo4j_properties(row) for row in rows]

    def find_running(self) -> list[ResearchCycle]:
        """Cycles that are executing right now (``awaiting_gate`` does not block)."""
        return self.find_status(("running",))

    def list(self, limit: int = DEFAULT_LIMIT) -> list[ResearchCycle]:
        """Return cycles newest first."""

        def _list(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                f"MATCH (c:{LABEL_RESEARCH_CYCLE} {{ns: $ns}}) "
                "RETURN c ORDER BY c.started_at DESC, c.cycle_id DESC LIMIT $limit",
                ns=self.namespace,
                limit=limit,
            )
            return [dict(record["c"]) for record in result]

        with self._repo.session() as session:
            rows = session.execute_read(_list)
        return [ResearchCycle.from_neo4j_properties(row) for row in rows]

    def list_pending_gates(self, limit: int = DEFAULT_LIMIT) -> list[ResearchCycle]:
        """Cycles that still have at least one workflow waiting at a gate.

        Filtering happens in Python because the gate list is an encoded JSON
        property; the candidate set is small (one node per cycle), and the
        Cypher query stays index-friendly on ``(ns, status)``.
        """
        return [
            cycle
            for cycle in self.find_status(("awaiting_gate",))
            if cycle.pending_gates()
        ][:limit]


def save_research_cycle(
    cycle: ResearchCycle, repository: Optional[Neo4jRepository] = None
) -> ResearchCycle:
    """Save ``cycle`` using ``repository`` (or the process default)."""
    return ResearchCycleRepository(
        repository or get_repository(), namespace=cycle.namespace
    ).save(cycle)


def get_research_cycle(
    cycle_id: str,
    *,
    namespace: str = "canon",
    repository: Optional[Neo4jRepository] = None,
) -> ResearchCycle:
    """Fetch one cycle by id (or raise ``ResearchCycleNotFoundError``)."""
    return ResearchCycleRepository(
        repository or get_repository(), namespace=namespace
    ).get(cycle_id)


def list_research_cycles(
    *,
    namespace: str = "canon",
    limit: int = DEFAULT_LIMIT,
    repository: Optional[Neo4jRepository] = None,
) -> list[ResearchCycle]:
    """List cycles newest first."""
    return ResearchCycleRepository(
        repository or get_repository(), namespace=namespace
    ).list(limit=limit)


__all__ = [
    "DEFAULT_LIMIT",
    "LABEL_RESEARCH_CYCLE",
    "MAX_LIMIT",
    "ResearchCycleNotFoundError",
    "ResearchCycleRepository",
    "get_research_cycle",
    "list_research_cycles",
    "save_research_cycle",
]
