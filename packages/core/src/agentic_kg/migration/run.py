"""The runnable, observable KGIS -> KGCS -> canonical path.

This is the composition root for the adoption plan's Phase 8 first slice: it
takes the committed corpus (or a DOI/slug subset of it), runs the KGIS shadow
ingestion, curates the candidates through KGCS into an **isolated** canonical
store, and returns a JSON-ready summary of exactly what happened. It provisions
nothing and reads no environment at module import; the CLI and the Cloud Run Job
are the two callers.

Isolation (ADR-0005)
--------------------
The canonical store is built with a dedicated namespace over the one staging
Community database. Every canonical label carries the ``Canon__`` prefix and
every read and write is namespace-scoped, so nothing here can touch the legacy
graph. The KGIS ledger/evidence is job-local SQLite (or in-memory); the durable,
observable artifact is the canonical epoch in Neo4j.

Idempotency
-----------
Every identifier on this path is deterministic: the run id is fixed, candidates
are id-derived, and a paper's evidence id is derived from its DOI and source
file. A second run against the same namespace re-plans the same
``CREATE_IDENTITY`` operations; the store refuses them (its ``entity_version``
and ``assertion_absent`` preconditions were fixed by the first run), so
``committed`` is ``False`` and the canonical entity count does not move.
``tests/migration/test_run.py`` measures this against the reference memory
store.

What is deliberately not wired
------------------------------
Entity resolution, the bounded LLM adviser and the human review queue are all
absent, so every alias-subject assertion is *deferred* rather than committed —
reported in ``deferral_reasons`` and ``honest_nulls``, never as a silent zero.
The replay client reproduces the committed importer output rather than a live
model, which is stated in the summary because it changes how extraction
quality may be read.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agentic_kg.migration.canonical_read import (
    DEFAULT_CANONICAL_NAMESPACE,
    ENV_CANONICAL_NAMESPACE,
)
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    STRUCTURED_IDENTITY_POLICY,
    CurationRunResult,
    run_curation,
)
from agentic_kg.migration.ingestion import (
    CorpusError,
    CorpusPaper,
    ShadowStores,
    WriterLease,
    acquire_writer_lease,
    importer_replay_client,
    load_corpus,
    normalize_doi,
    run_shadow_ingestion,
)
from agentic_kg.migration.ingestion.pipeline import DEFAULT_RUN_ID, ShadowRunResult

#: Environment variables the Cloud Run Job / CLI read. ``KGIS_LEDGER_DIR`` unset
#: means an in-memory ledger (nothing persisted); the Job sets it to a
#: job-local directory because the canonical epoch is the durable artifact.
ENV_LEDGER_DIR = "KGIS_LEDGER_DIR"


class MigrationRunDisabled(RuntimeError):
    """The run was requested with one or both opt-in flags off."""


@dataclass(frozen=True)
class MigrateRunSummary:
    """Everything one migration run did, in a form safe to serialise to JSON."""

    run_id: str
    namespace: str
    papers: tuple[str, ...]
    dois: tuple[str, ...]
    candidates_by_kind: dict[str, int]
    routes: dict[str, int]
    planned_operations: dict[str, int]
    committed_operations: dict[str, int]
    planned_candidates: int
    committed_candidates: int
    deferred_candidates: int
    rejected_candidates: int
    deferral_reasons: dict[str, int]
    epoch: int
    published_epoch_this_run: int | None
    committed: bool
    execution_outcome: str | None
    deterministic_client: bool
    evidence_refs: int
    honest_nulls: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "namespace": self.namespace,
            "papers": list(self.papers),
            "dois": list(self.dois),
            "candidates_by_kind": self.candidates_by_kind,
            "routes": self.routes,
            "planned_operations": self.planned_operations,
            "committed_operations": self.committed_operations,
            "planned_candidates": self.planned_candidates,
            "committed_candidates": self.committed_candidates,
            "deferred_candidates": self.deferred_candidates,
            "rejected_candidates": self.rejected_candidates,
            "deferral_reasons": self.deferral_reasons,
            "epoch": self.epoch,
            "published_epoch_this_run": self.published_epoch_this_run,
            "committed": self.committed,
            "execution_outcome": self.execution_outcome,
            "deterministic_client": self.deterministic_client,
            "evidence_refs": self.evidence_refs,
            "honest_nulls": self.honest_nulls,
        }


def select_papers(
    *,
    slugs: Sequence[str] | None = None,
    dois: Sequence[str] | None = None,
) -> tuple[CorpusPaper, ...]:
    """The committed corpus, or the requested subset of it.

    ``dois`` selects into the frozen eight-paper corpus by DOI — this slice runs
    the committed corpus, not live PDF acquisition, so an unknown DOI is a loud
    error rather than a silently empty selection. ``slugs`` selects by slug.
    """
    if slugs and dois:
        raise ValueError("pass slugs or dois, not both")
    papers = load_corpus()
    if slugs:
        known = {paper.slug for paper in papers}
        unknown = [slug for slug in slugs if slug not in known]
        if unknown:
            raise CorpusError(
                f"unknown corpus slug(s): {', '.join(sorted(unknown))}; known: "
                f"{', '.join(sorted(known))}"
            )
        wanted = set(slugs)
        return tuple(paper for paper in papers if paper.slug in wanted)
    if dois:
        by_doi = {normalize_doi(paper.doi): paper for paper in papers}
        selected: list[CorpusPaper] = []
        unknown_dois: list[str] = []
        for doi in dois:
            paper = by_doi.get(normalize_doi(doi))
            if paper is None:
                unknown_dois.append(doi)
            else:
                selected.append(paper)
        if unknown_dois:
            raise CorpusError(
                "DOI(s) are not in the frozen corpus; this slice does not fetch "
                f"live papers: {', '.join(unknown_dois)}"
            )
        # dict.fromkeys preserves first-seen order while de-duplicating.
        return tuple(dict.fromkeys(selected))
    return papers


def _reason_code(reason: str) -> str:
    """A short, stable code for a deferral reason sentence."""
    if reason.startswith("candidate_kind 'artifact'"):
        return "artifact_no_operation"
    if reason.startswith("no resolved identity"):
        return "unresolved_identity_no_entity_resolution"
    if reason.startswith("validation rejected"):
        return "validation_rejected"
    if reason.startswith("routed "):
        return "awaiting_adviser_or_review"
    return "other"


def _deferral_reason_codes(curation: CurationRunResult) -> dict[str, int]:
    codes: dict[str, int] = {}
    for deferral in (*curation.rejected, *curation.deferred):
        code = _reason_code(deferral.reason)
        codes[code] = codes.get(code, 0) + 1
    return dict(sorted(codes.items()))


def _evidence_ref_count(candidates: Sequence[Any]) -> int:
    ids: set[str] = set()
    for candidate in candidates:
        for ref in getattr(candidate, "evidence_refs", ()) or ():
            evidence_id = getattr(ref, "evidence_id", None)
            if evidence_id:
                ids.add(str(evidence_id))
    return len(ids)


def _honest_nulls(*, deterministic_client: bool, stores: ShadowStores) -> dict[str, str]:
    nulls: dict[str, str] = {
        "entity_resolution": "not_wired: alias subjects stay unresolved, so every "
        "assertion about one is deferred rather than committed",
        "llm_adviser": "not_wired: LLM_ASSESS candidates are deferred, not adjudicated",
        "human_review": "not_wired: HUMAN candidates are deferred, not queued",
        "live_provider": "not_wired: this path runs a replay client only",
        "evidence_content": "ledger only: assertions carry evidence_refs; the KGIS "
        "ledger is a separate access path (KGIS ADR-0011) and is not projected",
    }
    if deterministic_client:
        nulls["extraction_quality"] = (
            "not_measured: the replay client reproduces the committed importer "
            "output, not a live model recording"
        )
    if stores.root is None:
        nulls["ledger_persistence"] = "in_memory: nothing was persisted for this run"
    else:
        nulls["ledger_persistence"] = (
            f"sqlite at {stores.root}: single-writer; on the staging GCS FUSE "
            "mount the filesystem does not provide the locking SQLite expects, "
            "so durability is guarded by Cloud Run parallelism=1 and a "
            "directory writer lease, not by SQLite locking. ADR-0012's backend "
            "swap behind CandidateSink/LedgerReader remains the real fix."
        )
    return nulls


def run_migration(
    *,
    config: MigrationConfig,
    papers: Sequence[CorpusPaper],
    client: Any,
    stores: ShadowStores,
    store: Any,
    namespace: str,
    run_id: str = DEFAULT_RUN_ID,
    confidence_policy: Any = STRUCTURED_IDENTITY_POLICY,
    supported_operations: Any = None,
) -> MigrateRunSummary:
    """Run ingestion and curation, returning a summary. No store is built here."""
    if not config.use_kgis_ingestion or not config.use_kgcs_resolution:
        raise MigrationRunDisabled(
            "the migration run requires both KGIS_INGESTION_ENABLED=1 and "
            "KGCS_RESOLUTION_ENABLED=1 (or an explicitly enabled MigrationConfig); "
            "no run was attempted."
        )

    shadow: ShadowRunResult = run_shadow_ingestion(
        papers, config=config, client=client, stores=stores, run_id=run_id
    )
    candidates = (*shadow.paper_candidates, *shadow.candidates)
    curation = run_curation(
        candidates,
        config=config,
        store=store,
        confidence_policy=confidence_policy,
        supported_operations=supported_operations,
        executed_by="agentic_kg.migration.run",
    )
    epoch = int(store.current_epoch())
    outcome = (
        curation.execution.outcome.value  # type: ignore[union-attr]
        if curation.execution is not None
        else None
    )
    return MigrateRunSummary(
        run_id=run_id,
        namespace=namespace,
        papers=tuple(paper.slug for paper in papers),
        dois=tuple(paper.doi for paper in papers),
        candidates_by_kind=shadow.kind_counts(),
        routes=curation.route_counts(),
        planned_operations=curation.operation_counts(),
        committed_operations=curation.operation_counts() if curation.committed else {},
        planned_candidates=len(curation.planned_candidate_ids),
        committed_candidates=len(curation.committed_candidate_ids),
        deferred_candidates=len(curation.deferred),
        rejected_candidates=len(curation.rejected),
        deferral_reasons=_deferral_reason_codes(curation),
        epoch=epoch,
        published_epoch_this_run=curation.published_epoch,
        committed=curation.committed,
        execution_outcome=outcome,
        deterministic_client=shadow.deterministic_client,
        evidence_refs=_evidence_ref_count(candidates),
        honest_nulls=_honest_nulls(deterministic_client=shadow.deterministic_client, stores=stores),
    )


def execute_migration(
    *,
    config: MigrationConfig,
    slugs: Sequence[str] | None = None,
    dois: Sequence[str] | None = None,
    namespace: str | None = None,
    ledger_dir: str | None = None,
    run_id: str = DEFAULT_RUN_ID,
) -> MigrateRunSummary:
    """Build the isolated stores from the environment and run the pipeline.

    This is the CLI/Job entry point. It owns the canonical driver and closes it
    (and the ledger) on every exit path.
    """
    from agentic_kg.config import get_config
    from agentic_kg.migration.neo4j import (
        SUPPORTED_OPERATIONS,
        canonical_store_from_config,
    )

    resolved_namespace = (
        namespace or os.getenv(ENV_CANONICAL_NAMESPACE) or DEFAULT_CANONICAL_NAMESPACE
    )
    resolved_ledger = ledger_dir or os.getenv(ENV_LEDGER_DIR) or None
    neo = get_config().neo4j

    # The on-disk ledger is single-writer SQLite. The staging Job mounts its
    # ledger directory from GCS, whose FUSE semantics the runbook documents as
    # not providing the file locking SQLite expects, so take the directory
    # writer lease before opening the stores. Terraform's `parallelism = 1` is
    # the hard barrier; this is the code-level backstop.
    lease: WriterLease | None = None
    if resolved_ledger:
        lease = acquire_writer_lease(resolved_ledger, owner=f"{run_id}:{os.getpid()}")

    stores: ShadowStores | None = None
    store: Any = None
    try:
        stores = (
            ShadowStores.at(resolved_ledger)
            if resolved_ledger
            else ShadowStores.in_memory()
        )
        store = canonical_store_from_config(
            config,
            uri=neo.uri,
            auth=(neo.username, neo.password),
            namespace=resolved_namespace,
            database=neo.database,
        )
        papers = select_papers(slugs=slugs, dois=dois)
        return run_migration(
            config=config,
            papers=papers,
            client=importer_replay_client(papers),
            stores=stores,
            store=store,
            namespace=resolved_namespace,
            run_id=run_id,
            supported_operations=SUPPORTED_OPERATIONS,
        )
    finally:
        # Release the lease only after the stores are closed, so the ledger files
        # are flushed while this run still holds the directory.
        if stores is not None:
            stores.close()
        if lease is not None:
            lease.release()
        if store is not None:
            store.close()


__all__ = [
    "ENV_LEDGER_DIR",
    "MigrateRunSummary",
    "MigrationRunDisabled",
    "execute_migration",
    "run_migration",
    "select_papers",
]
