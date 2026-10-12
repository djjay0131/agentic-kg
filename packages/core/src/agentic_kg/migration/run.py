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
from datetime import UTC, datetime
from typing import Any, Callable

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
    SourceRecord,
    WriterLease,
    acquire_writer_lease,
    importer_replay_client,
    is_deterministic,
    load_corpus,
    normalize_doi,
    run_shadow_ingestion,
)
from agentic_kg.migration.ingestion.pipeline import DEFAULT_RUN_ID, ShadowRunResult
from agentic_kg.migration.live import (
    ENV_MIGRATION_SOURCE,
    SOURCE_CORPUS,
    SOURCE_LIVE,
    AcquisitionResult,
    LiveAcquisitionError,
)

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
    source: str
    query: str | None
    limit: int | None
    papers_seen: int
    papers_new: int
    papers_skipped_duplicate: int
    papers_skipped_no_text: int
    papers_skipped_no_doi: int
    acquisition_errors: dict[str, int]
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
            "source": self.source,
            "query": self.query,
            "limit": self.limit,
            "papers_seen": self.papers_seen,
            "papers_new": self.papers_new,
            "papers_skipped_duplicate": self.papers_skipped_duplicate,
            "papers_skipped_no_text": self.papers_skipped_no_text,
            "papers_skipped_no_doi": self.papers_skipped_no_doi,
            "acquisition_errors": self.acquisition_errors,
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


def _honest_nulls(
    *, source: str, deterministic_client: bool, stores: ShadowStores
) -> dict[str, str]:
    live = source == SOURCE_LIVE
    nulls: dict[str, str] = {
        "entity_resolution": "not_wired: alias subjects stay unresolved, so every "
        "assertion about one is deferred rather than committed",
        "llm_adviser": "not_wired: LLM_ASSESS candidates are deferred, not adjudicated",
        "human_review": "not_wired: HUMAN candidates are deferred, not queued",
        "evidence_content": "ledger only: assertions carry evidence_refs; the KGIS "
        "ledger is a separate access path (KGIS ADR-0011) and is not projected",
    }
    if deterministic_client:
        # A live run that was handed the deterministic client (a replay) is the
        # same honest null a corpus run carries; a run whose client reaches a
        # model is not a null, so no key is emitted.
        nulls["live_provider"] = (
            "replay: a deterministic client was injected for this run, so no "
            "live model produced these candidates"
        )
        nulls["extraction_quality"] = (
            "not_measured: the replay client reproduces the committed importer "
            "output, not a live model recording"
        )
    if not live:
        nulls["query_scoping"] = (
            "not_wired: the corpus backend replays the committed eight-paper "
            "corpus; the query does not select live papers"
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


def _acquisition_fields(
    source: str, papers: Sequence[Any], acquisition: AcquisitionResult | None
) -> dict[str, Any]:
    """The acquisition half of the summary, for corpus and live alike.

    A corpus run has no acquisition step, so every selected paper is both seen
    and new (the corpus is replayed whole). A live run reports what discovery
    actually produced; on an idempotent re-run ``papers_new`` is 0 and the
    duplicates are named.
    """
    if acquisition is not None:
        return {
            "source": acquisition.source,
            "query": acquisition.query,
            "limit": acquisition.limit,
            "papers_seen": acquisition.papers_seen,
            "papers_new": acquisition.papers_new,
            "papers_skipped_duplicate": acquisition.skipped_duplicate,
            "papers_skipped_no_text": acquisition.skipped_no_text,
            "papers_skipped_no_doi": acquisition.skipped_no_doi,
            "acquisition_errors": dict(acquisition.errors),
        }
    return {
        "source": source,
        "query": None,
        "limit": None,
        "papers_seen": len(papers),
        "papers_new": len(papers),
        "papers_skipped_duplicate": 0,
        "papers_skipped_no_text": 0,
        "papers_skipped_no_doi": 0,
        "acquisition_errors": {},
    }


def _empty_run_summary(
    *,
    run_id: str,
    namespace: str,
    store: Any,
    source: str,
    acquisition: AcquisitionResult | None,
    deterministic_client: bool,
    stores: ShadowStores,
) -> MigrateRunSummary:
    """A summary for a run that acquired no new papers (live dedupe only).

    Curation is skipped entirely rather than run over an empty candidate set:
    ``build_shadow_pipeline`` refuses no papers, and an "empty pipeline result"
    would be indistinguishable from a pipeline that ran and found nothing.
    """
    fields = _acquisition_fields(source, (), acquisition)
    return MigrateRunSummary(
        run_id=run_id,
        namespace=namespace,
        papers=(),
        dois=(),
        candidates_by_kind={},
        routes={},
        planned_operations={},
        committed_operations={},
        planned_candidates=0,
        committed_candidates=0,
        deferred_candidates=0,
        rejected_candidates=0,
        deferral_reasons={},
        epoch=int(store.current_epoch()),
        published_epoch_this_run=None,
        committed=False,
        execution_outcome=None,
        deterministic_client=deterministic_client,
        evidence_refs=0,
        **fields,
        honest_nulls={
            **_honest_nulls(
                source=source, deterministic_client=deterministic_client, stores=stores
            ),
            "new_papers": "none: every discovered paper was already in the ledger "
            "or already canonical, so no pipeline run was attempted",
        },
    )


def run_migration(
    *,
    config: MigrationConfig,
    papers: Sequence[SourceRecord],
    client: Any,
    stores: ShadowStores,
    store: Any,
    namespace: str,
    run_id: str = DEFAULT_RUN_ID,
    confidence_policy: Any = STRUCTURED_IDENTITY_POLICY,
    supported_operations: Any = None,
    source: str = SOURCE_CORPUS,
    acquisition: AcquisitionResult | None = None,
    source_locator: str = "ground_truth_chain",
) -> MigrateRunSummary:
    """Run ingestion and curation, returning a summary. No store is built here."""
    if not config.use_kgis_ingestion or not config.use_kgcs_resolution:
        raise MigrationRunDisabled(
            "the migration run requires both KGIS_INGESTION_ENABLED=1 and "
            "KGCS_RESOLUTION_ENABLED=1 (or an explicitly enabled MigrationConfig); "
            "no run was attempted."
        )

    if not papers:
        # Only a live run can legitimately arrive here with nothing new; a
        # corpus run with no papers is a programming error, and the pipeline's
        # own guard is the right place for it.
        if acquisition is None:
            raise ValueError(
                "run_migration requires at least one paper (a corpus run has no "
                "acquisition step that could legitimately select none)"
            )
        return _empty_run_summary(
            run_id=run_id,
            namespace=namespace,
            store=store,
            source=source,
            acquisition=acquisition,
            deterministic_client=is_deterministic(client),
            stores=stores,
        )

    shadow: ShadowRunResult = run_shadow_ingestion(
        papers,
        config=config,
        client=client,
        stores=stores,
        run_id=run_id,
        source_locator=source_locator,
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
        **_acquisition_fields(source, papers, acquisition),
        honest_nulls=_honest_nulls(
            source=source,
            deterministic_client=shadow.deterministic_client,
            stores=stores,
        ),
    )


def _ledger_paper_aliases(ledger: Any) -> set[str]:
    """Every ``namespace:key`` alias of a ``Paper`` already in the ledger.

    The durable ledger is the run-over-run memory of what KGIS has seen. A
    discovered paper whose DOI — or arXiv / Semantic Scholar / OpenAlex id — is
    in it is a duplicate, and is skipped before the network-bound full-text
    fetch. Reading *every* alias, not just the DOI, is what lets a paper
    discovered under a different identifier be recognized as the same paper.
    """
    keys: set[str] = set()
    for entry in ledger.ledger_entries():
        candidate = entry.candidate
        if getattr(candidate, "entity_type", None) != "Paper":
            continue
        for alias in getattr(candidate, "aliases", ()) or ():
            keys.add(f"{alias.namespace}:{alias.key}")
    return keys


def _canonical_lookup(store: Any) -> Callable[[Sequence[Any]], bool]:
    """A predicate: is any of these aliases already a canonical ``Paper``?"""

    def lookup(refs: Sequence[Any]) -> bool:
        for ref in refs:
            if store.find_entities(entity_type="Paper", alias=ref):
                return True
        return False

    return lookup


def _resolve_source(source: str | None) -> str:
    resolved = (
        source or os.getenv(ENV_MIGRATION_SOURCE) or SOURCE_CORPUS
    ).strip().lower()
    if resolved not in (SOURCE_CORPUS, SOURCE_LIVE):
        raise LiveAcquisitionError(
            f"MIGRATION_SOURCE must be {SOURCE_CORPUS!r} or {SOURCE_LIVE!r}; "
            f"got {resolved!r}"
        )
    return resolved


def execute_migration(
    *,
    config: MigrationConfig,
    slugs: Sequence[str] | None = None,
    dois: Sequence[str] | None = None,
    namespace: str | None = None,
    ledger_dir: str | None = None,
    run_id: str = DEFAULT_RUN_ID,
    source: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    sources: Sequence[str] | None = None,
    discovery: Any = None,
    text_fetcher: Any = None,
    client: Any = None,
    retrieved_at: datetime | None = None,
) -> MigrateRunSummary:
    """Build the isolated stores from the environment and run the pipeline.

    This is the CLI/Job entry point. It owns the canonical driver and closes it
    (and the ledger) on every exit path.

    ``source`` (or ``MIGRATION_SOURCE``) selects the paper source. ``corpus``
    (the default) replays the committed eight-paper corpus and is byte-identical
    to before this flag existed. ``live`` discovers papers for ``query`` up to
    ``limit`` through the repo's acquisition layer, dedupes them against the
    durable ledger and the canonical store, and runs the pipeline on only the
    new records.

    ``discovery``, ``text_fetcher`` and ``client`` are injection seams: tests
    pass fakes, and the staging Job leaves them to their real defaults. None of
    the defaults can be reached from a unit test accidentally, because live mode
    must be *asked* for and discovery always reaches the network otherwise.
    """
    from agentic_kg.config import get_config
    from agentic_kg.migration.neo4j import (
        SUPPORTED_OPERATIONS,
        canonical_store_from_config,
    )

    resolved_source = _resolve_source(source)
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
        if resolved_source == SOURCE_LIVE:
            from agentic_kg.migration.live import (
                LiveOpenAICompletionClient,
                acquire_live_papers,
                default_discovery,
                default_text_fetcher,
            )

            resolved_query = (query or os.getenv("INGEST_QUERY") or "").strip()
            if not resolved_query:
                raise LiveAcquisitionError(
                    "MIGRATION_SOURCE=live requires a query: set INGEST_QUERY or "
                    "pass query=..."
                )
            resolved_limit = (
                limit if limit is not None else int(os.getenv("INGEST_LIMIT", "20"))
            )
            acquisition = acquire_live_papers(
                query=resolved_query,
                limit=resolved_limit,
                discovery=discovery or default_discovery,
                text_fetcher=text_fetcher or default_text_fetcher(),
                ledger_aliases=_ledger_paper_aliases(stores.ledger),
                canonical_lookup=_canonical_lookup(store),
                retrieved_at=retrieved_at or datetime.now(UTC),
                sources=sources,
            )
            return run_migration(
                config=config,
                papers=acquisition.papers,
                client=client if client is not None else LiveOpenAICompletionClient(),
                stores=stores,
                store=store,
                namespace=resolved_namespace,
                run_id=run_id,
                supported_operations=SUPPORTED_OPERATIONS,
                source=SOURCE_LIVE,
                acquisition=acquisition,
                source_locator="live_discovery",
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
            source=SOURCE_CORPUS,
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
    "ENV_MIGRATION_SOURCE",
    "SOURCE_CORPUS",
    "SOURCE_LIVE",
    "MigrateRunSummary",
    "MigrationRunDisabled",
    "execute_migration",
    "run_migration",
    "select_papers",
]
