"""The shadow ingestion run: source -> KGIS -> candidates + evidence -> ledger.

Shadow only. Every candidate this module produces lands in an isolated
:class:`~agentic_kg.migration.ingestion.stores.ShadowStores` pair. Nothing here
writes a canonical fact, mutates the production graph, or holds a
`GraphMutationStore` — per ADR-0003 decision 1, `CandidateSink.submit()` is the
only application-facing write in the target architecture, and it is the only
write this module performs.

**Config is injected (ADR-0004 decision 4, AC-15c).** Nothing here calls
`get_migration_config()`. The flag reaches this module as a `MigrationConfig`
argument, which is what lets a test drive an enabled run without mutating
process-global state and what stops a caller from accidentally depending on
env-var ordering. `test_config_injection.py` enforces it by AST walk.

**Disabled means disabled.** With `use_kgis_ingestion=False` — the default, and
therefore the state of every existing deployment —
:func:`run_shadow_ingestion` raises :class:`ShadowIngestionDisabled` rather than
returning an empty result. An empty result is indistinguishable from "the
pipeline ran and found nothing", and those are opposite facts.

**No live provider in the test path.** The client is a required argument with
no default. There is no fallback that constructs an `OpenAIClient` when one is
not supplied, because a default like that is how a test suite ends up making
paid network calls on someone else's branch. :mod:`replay` builds the
deterministic client CI uses; a real provider adapter satisfies
`CompletionClient` structurally and can be passed in by a caller who wants one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.ingestion._contracts import (
    BuildContext,
    Candidate,
    CompletionClient,
    CoverageCounter,
    DeterministicIdStrategy,
    ExtractionPipeline,
    ExtractorConfig,
    FixedClock,
    IngestionReport,
    IterableDocumentSource,
    SubmissionStatus,
    is_deterministic,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.documents import PAPER_SOURCE_TYPE, SectionChunker
from agentic_kg.migration.ingestion.extractors import (
    STRUCTURED_SCORING,
    research_extractors,
)
from agentic_kg.migration.ingestion.ontology import (
    GRAPH_ID,
    ONTOLOGY_VERSION,
    RESEARCH_ONTOLOGY,
    research_candidate_validator,
)
from agentic_kg.migration.ingestion.papers import (
    STRUCTURED_PRODUCER,
    build_paper_candidates,
    build_paper_evidence,
)
from agentic_kg.migration.ingestion.stores import ShadowStores

#: A fixed clock for the whole run. Determinism is the requirement that makes
#: a replayed run comparable to the one before it; `created_at` is on every
#: candidate, so a wall clock would make two identical runs differ in every row.
#: The value is arbitrary and only has to be stable.
RUN_INSTANT = datetime(2026, 9, 18, tzinfo=UTC)

#: Fixed run id, for the same reason. `new_run_id()` is a random ULID and would
#: change `producer_run_id` and every derived trace id on each run.
DEFAULT_RUN_ID = "run_kgis_shadow_0001"
DEFAULT_JOB_ID = "job_kgis_shadow_0001"


class ShadowIngestionDisabled(RuntimeError):
    """The KGIS ingestion flag is off, so no shadow run was attempted.

    Raised rather than returning an empty :class:`ShadowRunResult`, because a
    result carrying zero candidates would be read as a measurement of the
    pipeline instead of a statement about the flag.
    """


@dataclass(frozen=True)
class ShadowRunResult:
    """Everything one shadow run produced, plus how to tell it apart from nothing.

    `report` is KGIS's own `IngestionReport` — not a re-derived summary, so the
    counts here are the pipeline's counts rather than this module's opinion of
    them.
    """

    report: IngestionReport
    candidates: tuple[Candidate, ...]
    paper_candidates: tuple[Candidate, ...]
    stores: ShadowStores
    deterministic_client: bool

    @property
    def submitted(self) -> int:
        return self.report.candidates_submitted

    def entity_counts(self) -> dict[str, int]:
        """Submitted `EntityCandidate`s per `entity_type`, sorted.

        Derived from the candidates actually submitted, not from the extractor
        configuration: a count read off the config would report what was
        *asked for* and stay identical if every extractor returned nothing.
        """
        from agentic_kg.migration.ingestion._contracts import EntityCandidate

        counts: dict[str, int] = {}
        for candidate in (*self.paper_candidates, *self.candidates):
            if isinstance(candidate, EntityCandidate):
                counts[candidate.entity_type] = counts.get(candidate.entity_type, 0) + 1
        return dict(sorted(counts.items()))

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in (*self.paper_candidates, *self.candidates):
            counts[candidate.candidate_kind] = counts.get(candidate.candidate_kind, 0) + 1
        return dict(sorted(counts.items()))


def build_shadow_pipeline(
    papers: Sequence[CorpusPaper],
    *,
    config: MigrationConfig,
    client: CompletionClient,
    stores: ShadowStores,
    extractors: Sequence[ExtractorConfig] | None = None,
    run_id: str = DEFAULT_RUN_ID,
    job_id: str = DEFAULT_JOB_ID,
    emit_document_artifacts: bool = True,
) -> ExtractionPipeline:
    """Wire a KGIS `ExtractionPipeline` for the shadow path.

    Raises :class:`ShadowIngestionDisabled` when the injected config has KGIS
    ingestion switched off.

    Note `candidate_validator=` is passed explicitly and unconditionally.
    `ExtractionPipeline` takes no `ontology=` argument and defaults to
    `OntologyCandidateValidator(None)`, which admits *every* term — so a run
    built without this line would validate nothing while looking exactly like a
    run that validated everything (AC-14).
    """
    if not config.use_kgis_ingestion:
        raise ShadowIngestionDisabled(
            "KGIS shadow ingestion is disabled: the injected MigrationConfig has "
            "use_kgis_ingestion=False (set KGIS_INGESTION_ENABLED=1, or pass an "
            "explicitly enabled MigrationConfig). No run was attempted."
        )
    if not papers:
        raise ValueError("build_shadow_pipeline requires at least one paper")

    documents = [paper.to_document().to_kgis_document() for paper in papers]
    return ExtractionPipeline(
        graph_id=GRAPH_ID,
        document_source=IterableDocumentSource(
            documents, source_type=PAPER_SOURCE_TYPE, locator="ground_truth_chain"
        ),
        chunker=SectionChunker(),
        extractors=tuple(extractors) if extractors is not None else research_extractors(),
        client=client,
        sink=stores.ledger,
        evidence_registry=stores.evidence,
        ledger_reader=stores.ledger,
        ontology_version=ONTOLOGY_VERSION,
        candidate_validator=research_candidate_validator(strict=True),
        clock=FixedClock(RUN_INSTANT),
        ids=DeterministicIdStrategy(),
        run_id=run_id,
        job_id=job_id,
        # The PDF text itself, as an `ArtifactCandidate`. Per KGCS ADR candidate
        # 0001 an artifact contributes no graph operation, so it stays in the
        # ledger and evidence and never reaches a graph — which is also true of
        # the legacy graph, so this is not a regression.
        emit_document_artifacts=emit_document_artifacts,
        # Single-threaded. KGIS's reduce phase is already serialised and its
        # ordering is deterministic either way, but a replay client raising
        # ReplayMiss inside a thread pool reports a much worse traceback, and
        # this path's whole value is that a miss is legible.
        concurrency=1,
    )


def _structured_context(run_id: str) -> BuildContext:
    return BuildContext(
        graph_id=GRAPH_ID,
        producer=STRUCTURED_PRODUCER,
        producer_run_id=run_id,
        ontology_version=ONTOLOGY_VERSION,
        scoring=STRUCTURED_SCORING,
        clock=FixedClock(RUN_INSTANT),
        ids=DeterministicIdStrategy(),
    )


def submit_paper_candidates(
    papers: Sequence[CorpusPaper], *, stores: ShadowStores, run_id: str = DEFAULT_RUN_ID
) -> tuple[Candidate, ...]:
    """The structured arm: one Paper entity + its attributes, per paper.

    Runs through the same `CandidateSink` and the same evidence registry as the
    extraction arm, because "both modes submit candidates through
    `CandidateSink`" is the property that keeps one ledger the single record of
    what was proposed. Evidence is written *before* the candidates that cite it,
    so a reader that resolves refs never sees a dangling one.
    """
    context = _structured_context(run_id)
    submitted: list[Candidate] = []
    for paper in papers:
        stores.evidence.put(build_paper_evidence(paper, observed_at=RUN_INSTANT))
        candidates = build_paper_candidates(paper, context)
        for candidate in candidates:
            stores.evidence.add_refs(candidate.candidate_id, list(candidate.evidence_refs))
        result = stores.ledger.submit(candidates)
        accepted = {
            outcome.candidate_id
            for outcome in result.outcomes
            if outcome.status is SubmissionStatus.RECEIVED
        }
        submitted.extend(c for c in candidates if c.candidate_id in accepted)
    return tuple(submitted)


def run_shadow_ingestion(
    papers: Sequence[CorpusPaper],
    *,
    config: MigrationConfig,
    client: CompletionClient,
    stores: ShadowStores,
    extractors: Sequence[ExtractorConfig] | None = None,
    run_id: str = DEFAULT_RUN_ID,
    include_papers: bool = True,
) -> ShadowRunResult:
    """Run both arms and return what reached the ledger.

    The structured arm runs first so a Paper identity is in the ledger before
    anything that would refer to it.
    """
    pipeline = build_shadow_pipeline(
        papers,
        config=config,
        client=client,
        stores=stores,
        extractors=extractors,
        run_id=run_id,
    )
    paper_candidates = (
        submit_paper_candidates(papers, stores=stores, run_id=run_id)
        if include_papers
        else ()
    )
    report = pipeline.run()
    # Scoped to *this* run, not to everything the ledger holds. Against an
    # in-memory store the two are the same; against a persistent one reused
    # across runs they are not, and an unscoped read would fold a previous
    # run's candidates into this run's result — inflating every count in
    # `entity_counts()` and every arm record, with nothing looking wrong.
    submitted = tuple(
        entry.candidate
        for entry in stores.ledger.ledger_entries()
        if entry.candidate.producer != STRUCTURED_PRODUCER
        and entry.candidate.producer_run_id == run_id
    )
    _populate_coverage(report, (*paper_candidates, *submitted))
    return ShadowRunResult(
        report=report,
        candidates=submitted,
        paper_candidates=paper_candidates,
        stores=stores,
        deterministic_client=is_deterministic(client),
    )


def _populate_coverage(report: IngestionReport, candidates: Sequence[Candidate]) -> None:
    """Fill in `report.coverage`, which `ExtractionPipeline` leaves undeclared.

    `IngestPipeline` (the structured front end) builds an `OntologyCoverage`
    from its ontology; `ExtractionPipeline` takes no `ontology=` argument at
    all, so its report always comes back `declared=False` with empty term
    tallies. That is an honest null for KGIS — it genuinely does not know the
    vocabulary — but it is *wrong* for this path, which validates every
    candidate against `RESEARCH_ONTOLOGY` and therefore does know it. A report
    saying "no ontology was declared" next to a run that rejected undeclared
    terms contradicts itself, and a reader would reasonably conclude AC-14 was
    not enforced.

    Reuses `kgis.ontology.CoverageCounter` verbatim — the same accumulator
    `IngestPipeline` uses — rather than computing the tallies here. A local
    re-derivation would be a second implementation of a summary this repo does
    not own, and it would drift.

    Note the `unused_*` axes are as informative as the `unknown_*` ones: a
    `Topic` term declared and never used is exactly the signal §9.0 wants
    visible, and it is how the zero-Topic outcome shows up in the report rather
    than only in a test.
    """
    counter = CoverageCounter()
    for candidate in candidates:
        counter.observe(candidate)
    report.coverage = counter.summarize(RESEARCH_ONTOLOGY)


__all__ = [
    "DEFAULT_JOB_ID",
    "DEFAULT_RUN_ID",
    "RUN_INSTANT",
    "ShadowIngestionDisabled",
    "ShadowRunResult",
    "build_shadow_pipeline",
    "run_shadow_ingestion",
    "submit_paper_candidates",
]
