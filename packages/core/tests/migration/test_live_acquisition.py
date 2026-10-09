"""Live, query-driven acquisition for the KGIS -> KGCS path.

Fast tier only: discovery and full-text fetch are injected fakes, the completion
client returns an empty extraction, and the canonical store is the reference
``MemoryGraphStore``. No network, no Docker. The real discovery clients
(aggregator / S2 / arXiv) and the real ``PDFExtractor`` are *defaults* that live
mode must be asked for, and are never reached here.

The one test that runs the committed corpus through the shadow pipeline is the
byte-identity guard: the whole corpus candidate set hashes to a digest measured
against ``master`` before this change (see the PR body), so any accidental edit
to the corpus path turns it red.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

pytest.importorskip(
    "kgis",
    reason=(
        "the opt-in 'migration' extra is not installed; "
        "pip install './packages/core[migration]'"
    ),
)

from agentic_kg.data_acquisition.normalizer import NormalizedPaper  # noqa: E402
from agentic_kg.migration.config import MigrationConfig  # noqa: E402
from agentic_kg.migration.ingestion import (  # noqa: E402
    ShadowStores,
    importer_replay_client,
    load_corpus,
    run_shadow_ingestion,
)
from agentic_kg.migration.ingestion.papers import paper_evidence_id  # noqa: E402
from agentic_kg.migration.live import (  # noqa: E402
    SOURCE_LIVE,
    AcquiredPaper,
    LiveAcquisitionError,
    acquire_live_papers,
)
from agentic_kg.migration.neo4j import SUPPORTED_OPERATIONS  # noqa: E402
from agentic_kg.migration.run import (  # noqa: E402
    _canonical_lookup,
    _ledger_paper_aliases,
    _resolve_source,
    run_migration,
)
from kg_contracts.testing.memory import MemoryGraphStore  # noqa: E402

RETRIEVED_AT = datetime(2026, 9, 18, tzinfo=UTC)

#: Long enough to clear the default minimum-text threshold.
LONG_TEXT = (
    "Abstract\n"
    + "We study things. " * 40
    + "\n\nIntroduction\n"
    + "This paper is about things and other things. " * 40
    + "\n"
)

#: The committed-corpus candidate digest, measured on `master` before this
#: change (252 candidates, `report.candidates_submitted == 228`).
CORPUS_CANDIDATE_DIGEST = (
    "bbee0cbab217f055de32625abf391577790b397b187a2b429e8c2a78874064ec"
)


class EmptyClient:
    """A deterministic `CompletionClient` whose extractors find nothing.

    Returns an empty JSON array, which KGIS parses as zero items, so the
    extraction arm contributes no candidates and the tests observe the
    structured paper arm, which is what acquisition feeds.
    """

    deterministic = True

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return "[]"


def _normalized(
    doi: str | None,
    *,
    s2: str | None = None,
    arxiv: str | None = None,
    title: str | None = None,
) -> NormalizedPaper:
    ids: dict[str, str] = {}
    if s2:
        ids["semantic_scholar"] = s2
    if arxiv:
        ids["arxiv"] = arxiv
    return NormalizedPaper(
        title=title or f"Paper {doi}",
        source="semantic_scholar",
        doi=doi,
        year=2025,
        external_ids=ids,
        pdf_url="https://example.invalid/paper.pdf",
    )


def _discovery(papers, errors=None):
    return lambda query, limit, sources: (list(papers)[:limit], errors or {})


def _fetch(text: str = LONG_TEXT):
    return lambda paper: text


# ---------------------------------------------------------------------------
# acquire_live_papers: dedupe, cap and skips
# ---------------------------------------------------------------------------


def test_acquire_dedupes_against_the_ledger_by_doi() -> None:
    papers = [_normalized("10.1234/a", s2="a" * 40), _normalized("10.1234/b", s2="b" * 40)]
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases={"doi:10.1234/a"},
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_seen == 2
    assert result.papers_new == 1
    assert result.skipped_duplicate == 1
    assert [p.doi for p in result.papers] == ["10.1234/b"]


def test_acquire_dedupes_on_a_secondary_identifier() -> None:
    """A paper recognized only by its arXiv id is still a duplicate."""
    papers = [_normalized("10.1234/a", arxiv="2501.00001")]
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases={"arxiv:2501.00001"},
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_new == 0
    assert result.skipped_duplicate == 1


def test_acquire_dedupes_against_the_canonical_store() -> None:
    papers = [_normalized("10.1234/a", s2="a" * 40)]
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases=set(),
        canonical_lookup=lambda refs: True,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_new == 0
    assert result.skipped_duplicate == 1


def test_acquire_caps_at_the_limit() -> None:
    papers = [_normalized(f"10.1234/{i}", s2=str(i) * 8) for i in range(5)]
    result = acquire_live_papers(
        query="q",
        limit=2,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases=set(),
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_seen == 2
    assert result.papers_new == 2


def test_acquire_skips_a_paper_without_a_doi() -> None:
    papers = [_normalized(None), _normalized("10.1234/a", s2="a" * 40)]
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases=set(),
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_seen == 1
    assert result.skipped_no_doi == 1
    assert result.papers_new == 1


def test_acquire_skips_when_full_text_is_too_thin() -> None:
    papers = [_normalized("10.1234/a", s2="a" * 40)]
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch("abstract only"),
        ledger_aliases=set(),
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.papers_new == 0
    assert result.skipped_no_text == 1


def test_acquire_reports_discovery_errors() -> None:
    result = acquire_live_papers(
        query="q",
        limit=10,
        discovery=_discovery([], errors={"semantic_scholar": "rate limited"}),
        text_fetcher=_fetch(),
        ledger_aliases=set(),
        canonical_lookup=lambda refs: False,
        retrieved_at=RETRIEVED_AT,
    )
    assert result.errors == {"semantic_scholar": 1}
    assert result.papers_new == 0


def test_limit_must_be_positive() -> None:
    with pytest.raises(LiveAcquisitionError, match="limit must be"):
        acquire_live_papers(
            query="q",
            limit=0,
            discovery=_discovery([]),
            text_fetcher=_fetch(),
            ledger_aliases=set(),
            canonical_lookup=lambda refs: False,
            retrieved_at=RETRIEVED_AT,
        )


# ---------------------------------------------------------------------------
# run_migration in live mode: commit, provenance, idempotency
# ---------------------------------------------------------------------------


def _live_run(
    papers, ledger_aliases, canonical_lookup, *, run_id="run_1", stores=None, store=None
):
    config = MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True)
    stores = stores if stores is not None else ShadowStores.in_memory()
    store = store if store is not None else MemoryGraphStore()
    acquisition = acquire_live_papers(
        query="live things",
        limit=10,
        discovery=_discovery(papers),
        text_fetcher=_fetch(),
        ledger_aliases=ledger_aliases,
        canonical_lookup=canonical_lookup,
        retrieved_at=RETRIEVED_AT,
    )
    summary = run_migration(
        config=config,
        papers=acquisition.papers,
        client=EmptyClient(),
        stores=stores,
        store=store,
        namespace="test-ns",
        run_id=run_id,
        supported_operations=SUPPORTED_OPERATIONS,
        source=SOURCE_LIVE,
        acquisition=acquisition,
        source_locator="live_discovery",
    )
    return summary, acquisition, stores, store


def test_live_run_commits_new_papers_and_reruns_idempotently() -> None:
    papers = [_normalized("10.1234/a", s2="a" * 40), _normalized("10.1234/b", s2="b" * 40)]
    summary, _acq, stores, store = _live_run(papers, set(), lambda refs: False)
    try:
        assert summary.source == SOURCE_LIVE
        assert summary.committed is True
        assert summary.committed_candidates == 2
        assert summary.papers_seen == 2
        assert summary.papers_new == 2
        assert summary.epoch == 1
        assert "query_scoping" not in summary.honest_nulls

        second, second_acq, _s, _st = _live_run(
            papers,
            _ledger_paper_aliases(stores.ledger),
            _canonical_lookup(store),
            run_id="run_2",
            stores=stores,
            store=store,
        )
        assert second_acq.papers_new == 0
        assert second_acq.skipped_duplicate == 2
        assert second.committed is False
        assert second.committed_candidates == 0
        assert second.epoch == summary.epoch
        assert "new_papers" in second.honest_nulls
    finally:
        stores.close()


def test_live_evidence_row_carries_full_provenance() -> None:
    paper = _normalized("10.1234/a", s2="a" * 40, arxiv="2501.00001", title="Live Title")
    summary, acquisition, stores, _store = _live_run([paper], set(), lambda refs: False)
    try:
        acquired: AcquiredPaper = acquisition.papers[0]
        evidence = stores.evidence.get(paper_evidence_id(acquired))
        assert evidence is not None
        assert evidence.source_type == "semantic_scholar"
        assert evidence.observed_at == RETRIEVED_AT
        assert "query:live things" in (evidence.content or "")
        assert "arxiv=2501.00001" in (evidence.content or "")
        assert "source:semantic_scholar" in (evidence.content or "")
        assert evidence.provenance.actor == "kgis.structured"
        assert summary.evidence_refs >= 1
    finally:
        stores.close()


def test_live_run_with_no_new_papers_attempts_no_pipeline() -> None:
    """A fully-duplicate acquisition skips the pipeline and says why."""
    papers = [_normalized("10.1234/a", s2="a" * 40)]
    summary, acquisition, stores, _store = _live_run(
        papers, {"doi:10.1234/a"}, lambda refs: False
    )
    try:
        assert acquisition.papers_new == 0
        assert summary.committed is False
        assert summary.papers_new == 0
        assert summary.planned_candidates == 0
        assert "new_papers" in summary.honest_nulls
    finally:
        stores.close()


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------


def test_unknown_source_refuses() -> None:
    with pytest.raises(LiveAcquisitionError, match="MIGRATION_SOURCE"):
        _resolve_source("sideways")


def test_corpus_is_the_default_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATION_SOURCE", raising=False)
    assert _resolve_source(None) == "corpus"


def test_live_source_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_SOURCE", "live")
    assert _resolve_source(None) == "live"


def test_execute_migration_live_without_a_query_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Live mode must be told what to look for; it never invents a query."""
    from types import SimpleNamespace

    from agentic_kg.migration import run as run_mod

    monkeypatch.delenv("INGEST_QUERY", raising=False)
    monkeypatch.setattr(
        "agentic_kg.config.get_config",
        lambda: SimpleNamespace(
            neo4j=SimpleNamespace(uri="bolt://unused", username="u", password="p", database=None)
        ),
    )
    monkeypatch.setattr(
        "agentic_kg.migration.neo4j.canonical_store_from_config",
        lambda *args, **kwargs: SimpleNamespace(
            close=lambda: None, current_epoch=lambda: 0, find_entities=lambda **kw: []
        ),
    )
    with pytest.raises(LiveAcquisitionError, match="requires a query"):
        run_mod.execute_migration(
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True),
            source="live",
            ledger_dir=str(tmp_path),
        )


# ---------------------------------------------------------------------------
# Corpus byte-identity
# ---------------------------------------------------------------------------


def test_corpus_candidate_set_is_byte_identical() -> None:
    """The corpus shadow run hashes to the digest measured on `master`.

    This is the whole contract of ``MIGRATION_SOURCE=corpus`` (the default):
    nothing about the frozen path moved. The digest covers every candidate's
    full JSON dump — ids, hashes, aliases, coordinates, evidence refs — sorted.
    """
    papers = load_corpus()
    stores = ShadowStores.in_memory()
    try:
        run = run_shadow_ingestion(
            papers,
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
            client=importer_replay_client(papers),
            stores=stores,
        )
        rows = sorted(
            json.dumps(cand.model_dump(mode="json"), sort_keys=True, default=str)
            for cand in (*run.paper_candidates, *run.candidates)
        )
        digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    finally:
        stores.close()

    assert len(rows) == 252
    assert run.report.candidates_submitted == 228
    assert digest == CORPUS_CANDIDATE_DIGEST
