"""Issue #58 — citation-population failures must not be absorbed silently.

Before this change ``ImportResult.citation_population`` was written by
``PaperImporter.import_paper`` and read by *nobody*: ``batch_import``
did not aggregate it, ``ingest_papers`` never consulted it, and
``status`` was set to ``"completed"`` unconditionally. A run in which
100% of papers failed to reach Semantic Scholar was byte-for-byte
indistinguishable from a run whose papers genuinely cite nothing.

These tests pin the propagation path (importer → batch → ingestion
result → status → summary log) and the two-case distinction an operator
needs: infrastructure failure vs genuine regression.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.data_acquisition.importer import (
    BatchImportResult,
    ImportResult,
    PaperImporter,
)
from agentic_kg.ingestion import (
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_ERRORS,
    IngestionResult,
    citations_unmeasured,
    ingest_papers,
)
from agentic_kg.job_runner import _determine_exit_code
from agentic_kg.knowledge_graph.citation_graph import (
    CITATION_OUTCOME_FETCH_FAILED,
    CITATION_OUTCOME_LOOKUP_FAILED,
    CITATION_OUTCOME_NO_S2_ID,
    CITATION_OUTCOME_NOT_ATTEMPTED,
    CITATION_OUTCOME_SUCCEEDED,
    CitationPopulationResult,
    classify_citation_population,
    populate_citations,
)

# =============================================================================
# Helpers
# =============================================================================


def _paper(doi: str) -> MagicMock:
    p = MagicMock()
    p.doi = doi
    return p


def _ok(edges: int = 3, stubs: int = 2) -> CitationPopulationResult:
    return CitationPopulationResult(edges_created=edges, stubs_created=stubs)


def _lookup_failed() -> CitationPopulationResult:
    return CitationPopulationResult(
        skipped_no_s2_id=True,
        lookup_failed=True,
        errors=["s2_id_lookup_failed: 429 Too Many Requests"],
    )


def _fetch_failed() -> CitationPopulationResult:
    return CitationPopulationResult(
        fetch_failed=True, errors=["fetch_failed: circuit breaker OPEN"],
    )


def _no_s2_id() -> CitationPopulationResult:
    return CitationPopulationResult(skipped_no_s2_id=True)


def _importer() -> PaperImporter:
    return PaperImporter(aggregator=MagicMock(), repository=MagicMock())


async def _run_batch(
    outcomes: list[CitationPopulationResult | None],
) -> BatchImportResult:
    """Run ``batch_import`` over N identifiers with canned citation outcomes."""
    importer = _importer()
    results = [
        ImportResult(
            paper=_paper(f"10.1/{i}"), created=True, citation_population=cp,
        )
        for i, cp in enumerate(outcomes)
    ]
    importer.import_paper = AsyncMock(side_effect=results)
    return await importer.batch_import(
        [f"10.1/{i}" for i in range(len(outcomes))]
    )


# =============================================================================
# classify_citation_population — the outcome vocabulary
# =============================================================================


class TestClassify:
    def test_none_is_not_attempted_not_success(self):
        # The honest-null rule: "never ran" is not "ran and found zero".
        assert classify_citation_population(None) == CITATION_OUTCOME_NOT_ATTEMPTED

    def test_clean_result_is_succeeded(self):
        assert classify_citation_population(_ok()) == CITATION_OUTCOME_SUCCEEDED

    def test_zero_edges_still_succeeded(self):
        """S2 answered with an empty reference list — real evidence of absence."""
        empty = CitationPopulationResult(edges_created=0, stubs_created=0)
        assert classify_citation_population(empty) == CITATION_OUTCOME_SUCCEEDED

    def test_lookup_failure_is_infrastructure(self):
        assert classify_citation_population(_lookup_failed()) == (
            CITATION_OUTCOME_LOOKUP_FAILED
        )

    def test_fetch_failure_is_infrastructure(self):
        assert classify_citation_population(_fetch_failed()) == (
            CITATION_OUTCOME_FETCH_FAILED
        )

    def test_no_s2_id_is_distinct_from_unreachable(self):
        """S2 was reachable and simply has no record — a data gap, not an outage."""
        assert classify_citation_population(_no_s2_id()) == CITATION_OUTCOME_NO_S2_ID


class TestLookupFailedFlag:
    @pytest.mark.asyncio
    async def test_raising_lookup_sets_lookup_failed(self):
        s2 = MagicMock()
        s2.get_paper_by_doi = AsyncMock(side_effect=RuntimeError("429"))
        res = await populate_citations(
            repo=MagicMock(), s2_client=s2, paper_doi="10.1/a",
        )
        assert res.skipped_no_s2_id is True
        assert res.lookup_failed is True

    @pytest.mark.asyncio
    async def test_empty_lookup_response_does_not_set_lookup_failed(self):
        s2 = MagicMock()
        s2.get_paper_by_doi = AsyncMock(return_value={})
        res = await populate_citations(
            repo=MagicMock(), s2_client=s2, paper_doi="10.1/a",
        )
        assert res.skipped_no_s2_id is True
        assert res.lookup_failed is False


# =============================================================================
# batch_import aggregation
# =============================================================================


class TestBatchAggregation:
    @pytest.mark.asyncio
    async def test_all_succeed(self):
        batch = await _run_batch([_ok(edges=3, stubs=2)] * 3)
        assert batch.citation_attempted == 3
        assert batch.citation_succeeded == 3
        assert batch.citation_failed == 0
        assert batch.citation_edges_created == 9
        assert batch.citation_stubs_created == 6
        assert batch.citation_failures == {}

    @pytest.mark.asyncio
    async def test_all_fail(self):
        batch = await _run_batch(
            [_lookup_failed(), _lookup_failed(), _fetch_failed()]
        )
        assert batch.citation_attempted == 3
        assert batch.citation_succeeded == 0
        assert batch.citation_failed == 3
        assert batch.citation_edges_created == 0
        assert batch.citation_failures == {
            "s2_lookup_failed": 2, "s2_fetch_failed": 1,
        }
        # The reason string survives per-DOI for diagnosis.
        assert "429" in batch.citation_failure_details["10.1/0"]

    @pytest.mark.asyncio
    async def test_partial_fail(self):
        batch = await _run_batch([_ok(edges=4), _lookup_failed(), _no_s2_id()])
        assert batch.citation_attempted == 3
        assert batch.citation_succeeded == 1
        assert batch.citation_failed == 2
        assert batch.citation_edges_created == 4
        assert batch.citation_failures == {
            "s2_lookup_failed": 1, "no_s2_id": 1,
        }

    @pytest.mark.asyncio
    async def test_never_attempted_is_not_counted_as_attempt(self):
        """populate_citations=False / skipped import must not inflate counters."""
        batch = await _run_batch([None, None])
        assert batch.citation_attempted == 0
        assert batch.citation_succeeded == 0
        assert batch.citation_failed == 0

    @pytest.mark.asyncio
    async def test_aggregate_serialized_in_to_dict(self):
        batch = await _run_batch([_lookup_failed()])
        d = batch.to_dict()
        assert d["citation_attempted"] == 1
        assert d["citation_succeeded"] == 0
        assert d["citation_failures"] == {"s2_lookup_failed": 1}


# =============================================================================
# ingest_papers — propagation + status semantics
# =============================================================================


def _batch(**kw) -> BatchImportResult:
    b = BatchImportResult(total=kw.pop("total", 3))
    b.created = kw.pop("created", 3)
    for k, v in kw.items():
        setattr(b, k, v)
    return b


async def _ingest_with(batch: BatchImportResult) -> IngestionResult:
    search = MagicMock()
    search.papers = []
    search.errors = {}
    with (
        patch("agentic_kg.ingestion.get_paper_aggregator") as agg,
        patch("agentic_kg.ingestion.get_paper_importer") as imp,
        patch("agentic_kg.ingestion.get_pipeline"),
        patch("agentic_kg.ingestion.KGIntegratorV2"),
        patch("agentic_kg.ingestion.get_repository"),
        patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
    ):
        agg.return_value.search_papers = AsyncMock(return_value=search)
        imp.return_value.batch_import = AsyncMock(return_value=batch)
        return await ingest_papers("q", limit=3, extract_entities=False)


class TestIngestionPropagation:
    @pytest.mark.asyncio
    async def test_counts_reach_the_result(self):
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=2, citation_failed=1,
            citation_edges_created=19, citation_stubs_created=12,
            citation_failures={"s2_lookup_failed": 1},
            citation_failure_details={"10.1/c": "s2_lookup_failed: 429"},
        ))
        assert result.citation_population_attempted == 3
        assert result.citation_population_succeeded == 2
        assert result.citation_population_failed == 1
        assert result.citation_edges_created == 19
        assert result.citation_stubs_created == 12
        assert result.citation_failures == {"s2_lookup_failed": 1}
        assert result.citation_failure_details == {
            "10.1/c": "s2_lookup_failed: 429"
        }

    @pytest.mark.asyncio
    async def test_counts_are_serialized_for_operators(self):
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=0, citation_failed=3,
            citation_failures={"s2_fetch_failed": 3},
        ))
        payload = result.model_dump()
        assert payload["citation_population_attempted"] == 3
        assert payload["citation_failures"] == {"s2_fetch_failed": 3}


class TestStatusSemantics:
    @pytest.mark.asyncio
    async def test_all_citation_failures_downgrade_status(self):
        """THE defect: this run used to report status='completed'."""
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=0, citation_failed=3,
            citation_failures={"s2_lookup_failed": 3},
        ))
        assert result.status == STATUS_COMPLETED_WITH_ERRORS
        assert result.error is None  # not a fatal run; the import succeeded

    @pytest.mark.asyncio
    async def test_partial_success_stays_completed(self):
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=1, citation_failed=2,
            citation_edges_created=5,
            citation_failures={"s2_lookup_failed": 2},
        ))
        assert result.status == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_all_succeed_is_completed(self):
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=3,
            citation_edges_created=19,
        ))
        assert result.status == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_s2_reachable_but_zero_citations_is_completed(self):
        """Measured and genuinely empty is a healthy run, not a degraded one.

        The smoke gate still fails on cites=0 — but as a REGRESSION, which
        is a different report than an unreachable-S2 run.
        """
        result = await _ingest_with(_batch(
            citation_attempted=3, citation_succeeded=3, citation_edges_created=0,
        ))
        assert result.status == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_no_attempts_is_completed(self):
        result = await _ingest_with(_batch())
        assert result.status == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_summary_log_names_the_citation_outcome(self, caplog):
        with caplog.at_level(logging.INFO, logger="agentic_kg.ingestion"):
            await _ingest_with(_batch(
                citation_attempted=3, citation_succeeded=0, citation_failed=3,
                citation_failures={"s2_lookup_failed": 3},
            ))
        text = caplog.text
        assert "Citation summary: attempted=3 succeeded=0 failed=3" in text
        assert "measured NOTHING about citations" in text


class TestCitationsUnmeasured:
    def test_true_only_when_attempted_and_none_succeeded(self):
        base = dict(trace_id="t", query="q")
        assert citations_unmeasured(IngestionResult(
            **base, citation_population_attempted=2,
            citation_population_succeeded=0,
        ))
        assert not citations_unmeasured(IngestionResult(
            **base, citation_population_attempted=2,
            citation_population_succeeded=1,
        ))
        # Never attempted: nothing was claimed, so nothing is degraded.
        assert not citations_unmeasured(IngestionResult(**base))


class TestJobRunnerExitCode:
    def test_degraded_status_exits_partial(self):
        result = IngestionResult(
            trace_id="t", query="q", status=STATUS_COMPLETED_WITH_ERRORS,
        )
        assert _determine_exit_code(result) == 1

    def test_fatal_still_wins(self):
        result = IngestionResult(trace_id="t", query="q", status="failed")
        assert _determine_exit_code(result) == 2
