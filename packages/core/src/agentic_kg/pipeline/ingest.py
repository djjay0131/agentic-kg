"""In-process bridge from the nightly orchestrator to the ingest entrypoints.

The nightly Job runs the *same* ``job`` image and the *same* core code as the
staging ingest Job, so it executes ingestion in-process rather than shelling
out to ``gcloud``. Which entrypoint is selected mirrors the ingest Job's own
``INGEST_MODE`` dispatch in :mod:`agentic_kg.job_runner`:

* ``legacy``    -> :func:`agentic_kg.ingestion.ingest_papers` (query-driven
  discovery + import + extraction).
* ``kgis_kgcs`` -> :func:`agentic_kg.migration.run.execute_migration` (the
  KGIS -> KGCS -> canonical path).

Honest nulls
------------
The KGIS/KGCS path runs the committed eight-paper corpus; it does **not** yet
consume a free-text query to select live papers. When it is the backend, each
planned query is executed as its own migration run (so per-query summaries stay
attributable) and the outcome carries an ``honest_null`` saying the query text
did not select papers. Live query-driven acquisition over that path is P3.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

#: ``INGEST_MODE`` values that select the migration path (see job_runner.main).
_KGIS_MODES = frozenset({"kgis_kgcs", "kgis-kgcs"})


@dataclass
class QueryOutcome:
    """The per-query facts the report needs, independent of the backend."""

    papers_seen: int = 0
    papers_new: int = 0
    committed_operations: int = 0
    deferred_candidates: int = 0
    deferral_reasons: dict[str, int] = field(default_factory=dict)
    honest_nulls: dict[str, str] = field(default_factory=dict)
    est_llm_usd: float = 0.0
    error: str | None = None


def resolve_mode(mode: str | None = None) -> str:
    """The effective ingest mode: explicit argument, else ``INGEST_MODE``."""
    if mode is not None:
        return mode.strip().lower()
    return os.environ.get("INGEST_MODE", "legacy").strip().lower()


def run_query_ingest(
    query: str,
    limit: int,
    *,
    run_id: str,
    namespace: str,
    mode: str | None = None,
) -> QueryOutcome:
    """Run one planned query through the configured in-process backend.

    Never raises for an expected backend failure: errors are returned as
    ``QueryOutcome.error`` so the orchestrator records them per query instead of
    aborting the whole night. Programming errors (ImportError handled below
    excepted) are still allowed to propagate.
    """
    effective = resolve_mode(mode)
    if effective in _KGIS_MODES:
        return _run_kgis_kgcs(query, limit, run_id=run_id, namespace=namespace)
    return _run_legacy(query, limit, run_id=run_id)


def _run_legacy(query: str, limit: int, *, run_id: str) -> QueryOutcome:
    from agentic_kg.ingestion import ingest_papers

    result = asyncio.run(ingest_papers(query=query, limit=limit))
    return QueryOutcome(
        papers_seen=result.papers_found,
        papers_new=result.papers_imported,
        honest_nulls={
            "committed_operations": (
                "not_applicable: legacy ingestion writes the graph directly and "
                "does not run KGCS curation"
            ),
            "deferred_candidates": (
                "not_applicable: legacy ingestion does not defer candidates"
            ),
        },
    )


def _run_kgis_kgcs(
    query: str,
    limit: int,
    *,
    run_id: str,
    namespace: str,
) -> QueryOutcome:
    try:
        from agentic_kg.migration.config import get_migration_config
        from agentic_kg.migration.run import execute_migration
    except Exception as exc:  # noqa: BLE001 - the message is the actionable part
        return QueryOutcome(
            error=(
                "could not import the migration pipeline "
                f"({exc}); the job image must install the 'migration' extra"
            )
        )

    config = get_migration_config()
    if not (config.use_kgis_ingestion and config.use_kgcs_resolution):
        return QueryOutcome(
            error=(
                "INGEST_MODE=kgis_kgcs requires KGIS_INGESTION_ENABLED=1 and "
                "KGCS_RESOLUTION_ENABLED=1"
            )
        )

    ledger_dir = (
        os.environ.get("NIGHTLY_LEDGER_DIR")
        or os.environ.get("INGEST_LEDGER_DIR")
        or None
    )
    try:
        summary = execute_migration(
            config=config,
            namespace=namespace,
            ledger_dir=ledger_dir,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - recorded per query, not fatal
        return QueryOutcome(error=f"migration run failed: {exc}")

    return QueryOutcome(
        papers_seen=len(summary.papers),
        papers_new=summary.committed_candidates,
        committed_operations=sum(summary.committed_operations.values()),
        deferred_candidates=summary.deferred_candidates,
        deferral_reasons=dict(summary.deferral_reasons),
        honest_nulls={
            **dict(summary.honest_nulls),
            "query_scoping": (
                "not_wired: the KGIS/KGCS backend runs the committed corpus; the "
                "planned query is recorded but does not select live papers (P3)"
            ),
        },
    )


__all__ = ["QueryOutcome", "resolve_mode", "run_query_ingest"]
