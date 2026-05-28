"""Completeness query contract (E-8 AC-14).

Any analytical Cypher query that filters on a Paper's extracted entities
(topics, concepts, problems) MUST either:

- compose ``complete_papers_filter()`` into its WHERE clause, OR
- carry an in-code comment stating it accepts partial-extraction papers
  and why.

The verify gate runs a grep-driven audit over ``packages/core/src/`` and
the verification record captures any exemptions. Drifting an analytical
query off this contract has historically caused stats dashboards to
include corrupt rows; the helper is the gate.

Three helpers:

- ``complete_papers_filter()`` — pure-string Cypher fragment for WHERE.
- ``incomplete_papers_by_extractor(repo, extractor)`` — audit list.
- ``completeness_health_check(repo)`` — ``{extractor: fraction_incomplete}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository


_COMPLETE_FILTER = (
    "AND (p.extraction_incomplete IS NULL OR p.extraction_incomplete = false)"
)


def complete_papers_filter() -> str:
    """Return the Cypher fragment to drop incomplete papers from a query.

    Caller is expected to bind it as a suffix on a WHERE clause that has
    already introduced at least one predicate, e.g.::

        MATCH (p:Paper)
        WHERE p.doi IS NOT NULL
          {filter}
        RETURN p

    The filter explicitly tolerates papers ingested before E-8 (which do
    not carry the property at all) by treating NULL as "complete enough".
    """
    return _COMPLETE_FILTER


def incomplete_papers_by_extractor(
    repo: "Neo4jRepository", extractor: str
) -> list[dict[str, Any]]:
    """Return paper rows whose ``extraction_failed_extractors`` mentions ``extractor``.

    Useful for the operator's audit query — "which papers need re-ingestion
    because the topic extractor crashed last week?" Returns a list of
    serialized Paper property dicts; the caller decides how to project.
    """
    query = """
    MATCH (p:Paper)
    WHERE p.extraction_incomplete = true
      AND p.extraction_failed_extractors CONTAINS $extractor
    RETURN p
    ORDER BY p.doi
    """
    with repo.session() as session:
        records = session.run(query, extractor=extractor)
        return [dict(record["p"]) for record in records]


def completeness_health_check(repo: "Neo4jRepository") -> dict[str, float]:
    """Report the fraction of papers flagged incomplete per extractor.

    Returns ``{"problem": 0.05, "topic": 0.12, "concept": 0.00, ...}`` —
    a single number per extractor name that has at least one incident.
    Extractors with zero incidents are omitted to keep the dashboard tight.

    A return value of ``{}`` means every paper is complete.
    """
    incomplete_counts: dict[str, int] = {}
    total_papers = 0

    with repo.session() as session:
        # Total paper count, no completeness filter — the denominator.
        total_row = session.run("MATCH (p:Paper) RETURN count(p) AS total").single()
        if total_row is not None:
            total_papers = total_row["total"]

        if total_papers == 0:
            return {}

        rows = session.run(
            """
            MATCH (p:Paper)
            WHERE p.extraction_incomplete = true
              AND p.extraction_failed_extractors IS NOT NULL
            UNWIND split(p.extraction_failed_extractors, ',') AS extractor
            RETURN trim(extractor) AS extractor, count(p) AS count
            """
        )
        for record in rows:
            name = record["extractor"]
            if name:
                incomplete_counts[name] = (
                    incomplete_counts.get(name, 0) + record["count"]
                )

    return {
        name: count / total_papers
        for name, count in incomplete_counts.items()
        if count > 0
    }
