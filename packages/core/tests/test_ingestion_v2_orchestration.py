"""entity-pipeline-orchestration — per-paper loop tests (AC-25).

Mocked unit tests of ``ingest_papers``'s new V2 entity pipeline wiring:
flag combos, skip check, error injection, helpers. No testcontainers;
no real LLM calls; uses the existing test_ingestion.py mocking style.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.data_acquisition.normalizer import NormalizedPaper
from agentic_kg.extraction.pipeline import PaperExtractionResult
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)
from agentic_kg.ingestion import (
    IngestionResult,
    _build_extractor_section_text,
    _can_skip_entity_extraction,
    ingest_papers,
)

# =============================================================================
# Fixtures + helpers
# =============================================================================


def _normalized_paper(
    doi: str = "10.1/a",
    title: str = "Paper",
    pdf_url: str = "https://example/a.pdf",
    abstract: str | None = None,
) -> NormalizedPaper:
    return NormalizedPaper(
        doi=doi, title=title, authors=[], venue=None, year=2024,
        abstract=abstract, external_ids={}, pdf_url=pdf_url,
        source="openalex",
    )


def _search_result(papers):
    sr = MagicMock()
    sr.papers = papers
    sr.errors = {}
    sr.total_by_source = {"openalex": len(papers)}
    return sr


def _batch_import_result(created: int = 1, updated: int = 0):
    r = MagicMock()
    r.total = created + updated
    r.created = created
    r.updated = updated
    r.skipped = 0
    r.failed = 0
    r.errors = {}
    r.results = []
    return r


def _processing_result(success: bool = True, problem_count: int = 0, section_chars: int = 400):
    """Mock PaperProcessingResult.

    SM-1: a successful result must carry >= MIN_USABLE_CHARS of section text
    for full-text acquisition to accept it (else it is treated as failed_thin).
    """
    r = MagicMock()
    r.success = success
    r.problem_count = problem_count
    seg = MagicMock()
    if success and section_chars:
        section = MagicMock()
        section.section_type = MagicMock()
        section.section_type.value = "abstract"
        section.content = "full paper text " * (section_chars // 16 + 1)
        seg.sections = [section]
    else:
        seg.sections = []
    r.segmented_document = seg
    r.stages = [MagicMock(success=success, error="fetch failed" if not success else None)]
    r.get_high_confidence_problems = MagicMock(
        return_value=[MagicMock() for _ in range(problem_count)],
    )
    return r


def _v1_integration_result(
    mentions_created: int = 0,
    new_concepts: int = 0,
    linked: int = 0,
):
    r = MagicMock()
    r.mentions_created = mentions_created
    r.mentions_new_concepts = new_concepts
    r.mentions_linked = linked
    r.mentions = [MagicMock(concept_id=f"c-{i}") for i in range(mentions_created)]
    return r


def _v2_integration_result(
    topics: int = 0,
    concepts: int = 0,
    models: int = 0,
    methods: int = 0,
    incomplete: bool = False,
):
    r = MagicMock()
    r.topics_assigned = topics
    r.concepts_linked = concepts
    r.models_linked = models
    r.methods_linked = methods
    r.problem_concept_edges_drawn = 0
    r.paper_marked_incomplete = incomplete
    r.skipped_topic_names = []
    return r


def _extraction_result_with_entities() -> PaperExtractionResult:
    """A PaperExtractionResult carrying a Concept, Model, and Method
    (and zero problems / topics) — used to assert V2 integration fires
    on any-entity-extracted contracts."""
    return PaperExtractionResult(
        problems=[],
        topics=[],
        concepts=[
            ExtractedResearchConcept(
                name="attention", quoted_text="self-attention is used",
                confidence=0.95,
            )
        ],
        models=[
            ExtractedModel(
                name="BERT", quoted_text="we use BERT-base",
                confidence=0.95,
            )
        ],
        methods=[
            ExtractedMethod(
                name="fine-tuning", quoted_text="we fine-tune the model",
                confidence=0.9,
            )
        ],
        failures=[],
    )


@pytest.fixture
def common_mocks():
    """Patch every direct collaborator of ingest_papers's V2 path."""
    with (
        patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
        patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
        patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
        patch("agentic_kg.ingestion.get_repository") as mock_repo_factory,
        patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg_cls,
        patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        patch("agentic_kg.ingestion._paper_has_footprint", return_value=False),
        patch(
            "agentic_kg.ingestion._can_skip_entity_extraction",
            return_value=False,
        ),
        patch("agentic_kg.ingestion.extract_all_entities") as mock_extract,
        patch("agentic_kg.ingestion.normalize_cross_entity") as mock_norm,
        patch(
            "agentic_kg.ingestion.integrate_paper_entities",
        ) as mock_v2_int,
        # V2 extractor + embedder construction.
        patch("agentic_kg.extraction.topic_extractor.TopicExtractor") as mock_topic_cls,
        patch(
            "agentic_kg.extraction.concept_extractor.ConceptExtractor",
        ) as mock_concept_cls,
        patch(
            "agentic_kg.extraction.model_extractor.ModelExtractor",
        ) as mock_model_cls,
        patch(
            "agentic_kg.extraction.method_extractor.MethodExtractor",
        ) as mock_method_cls,
        patch(
            "agentic_kg.extraction.llm_client.get_openai_client",
        ) as mock_llm_factory,
        patch(
            "agentic_kg.knowledge_graph.embeddings.EmbeddingService",
        ) as mock_embedder_cls,
        patch(
            "agentic_kg.extraction.taxonomy_hash.canonical_taxonomy_hash",
            return_value="taxhash-abc",
        ),
        patch(
            "agentic_kg.knowledge_graph.taxonomy.parse_taxonomy",
            return_value=[],
        ),
    ):
        topic_x = MagicMock()
        topic_x.taxonomy_path = "/fake/path"
        topic_x.extract = AsyncMock(return_value=[])
        mock_topic_cls.return_value = topic_x
        concept_x = MagicMock()
        concept_x.extract = AsyncMock(return_value=[])
        mock_concept_cls.return_value = concept_x
        model_x = MagicMock()
        model_x.extract = AsyncMock(return_value=[])
        mock_model_cls.return_value = model_x
        method_x = MagicMock()
        method_x.extract = AsyncMock(return_value=[])
        mock_method_cls.return_value = method_x

        mock_llm_factory.return_value = MagicMock(name="OpenAIClient")
        mock_embedder_cls.return_value = MagicMock(name="EmbeddingService")

        mock_v2_int.return_value = _v2_integration_result()
        mock_extract.return_value = PaperExtractionResult(failures=[])
        mock_norm.return_value = MagicMock(
            is_clean=True, pairs_detected=0,
            pairs_resolved=0, pairs_rejected=0,
        )

        # Default V1 integrator return.
        mock_intg_cls.return_value.integrate_extracted_problems.return_value = (
            _v1_integration_result()
        )

        yield {
            "agg": mock_agg,
            "imp": mock_imp,
            "pipe": mock_pipe,
            "repo_factory": mock_repo_factory,
            "v1_intg_cls": mock_intg_cls,
            "extract": mock_extract,
            "normalize": mock_norm,
            "v2_int": mock_v2_int,
            "topic": topic_x,
            "concept": concept_x,
            "model": model_x,
            "method": method_x,
            "llm_factory": mock_llm_factory,
            "embedder_cls": mock_embedder_cls,
        }


def _wire_basic_search(common_mocks, papers):
    common_mocks["agg"].return_value.search_papers = AsyncMock(
        return_value=_search_result(papers),
    )
    common_mocks["imp"].return_value.batch_import = AsyncMock(
        return_value=_batch_import_result(created=len(papers)),
    )


# =============================================================================
# AC-4 / AC-5 / AC-6: text source resolution helper
# =============================================================================


class TestBuildExtractorSectionText:
    def test_returns_empty_when_seg_none(self):
        assert _build_extractor_section_text(None) == ""

    def test_returns_empty_when_no_sections(self):
        seg = MagicMock()
        seg.sections = []
        assert _build_extractor_section_text(seg) == ""

    def test_concatenates_wanted_sections_in_order(self):
        from agentic_kg.extraction.section_segmenter import (
            Section,
            SectionType,
        )

        seg = MagicMock()
        seg.sections = [
            Section(SectionType.ABSTRACT, "title", "abstract content"),
            Section(SectionType.INTRODUCTION, "title", "intro content"),
            Section(SectionType.RESULTS, "title", "results content"),  # filtered
            Section(SectionType.METHODS, "title", "methods content"),
            Section(SectionType.EXPERIMENTS, "title", "experiments content"),
        ]
        text = _build_extractor_section_text(seg)
        # Order matches section order in the document (we don't re-sort).
        assert "abstract content" in text
        assert "intro content" in text
        assert "methods content" in text
        assert "experiments content" in text
        assert "results content" not in text  # not in the wanted set

    def test_skips_empty_content(self):
        from agentic_kg.extraction.section_segmenter import (
            Section,
            SectionType,
        )

        seg = MagicMock()
        seg.sections = [
            Section(SectionType.ABSTRACT, "title", "   "),  # whitespace only
            Section(SectionType.INTRODUCTION, "title", "real content"),
        ]
        text = _build_extractor_section_text(seg)
        assert text.strip() == "real content"


# =============================================================================
# AC-21 / AC-22 / AC-23: skip check
# =============================================================================


class TestCanSkipEntityExtraction:
    def test_returns_false_when_no_doi(self):
        repo = MagicMock()
        assert _can_skip_entity_extraction(repo, None, "hash-1") is False

    def test_returns_false_when_no_taxonomy_hash(self):
        repo = MagicMock()
        assert _can_skip_entity_extraction(repo, "10.1/a", "") is False

    def test_returns_false_when_paper_not_in_graph(self):
        repo = MagicMock()
        session = MagicMock()
        session.__enter__ = lambda self: session
        session.__exit__ = lambda self, *a: None
        session.run.return_value.single.return_value = None
        repo.session.return_value = session
        assert _can_skip_entity_extraction(repo, "10.1/a", "h1") is False

    def test_returns_false_on_query_failure(self):
        repo = MagicMock()
        repo.session.side_effect = RuntimeError("Neo4j unreachable")
        assert _can_skip_entity_extraction(repo, "10.1/a", "h1") is False

    def test_returns_true_when_hash_matches_and_complete(self):
        repo = MagicMock()
        session = MagicMock()
        session.__enter__ = lambda self: session
        session.__exit__ = lambda self, *a: None
        row = {"taxonomy_hash": "h1", "extraction_incomplete": False}
        session.run.return_value.single.return_value = row
        repo.session.return_value = session
        assert _can_skip_entity_extraction(repo, "10.1/a", "h1") is True

    def test_returns_false_when_hash_mismatches(self):
        repo = MagicMock()
        session = MagicMock()
        session.__enter__ = lambda self: session
        session.__exit__ = lambda self, *a: None
        row = {"taxonomy_hash": "h1-stale", "extraction_incomplete": False}
        session.run.return_value.single.return_value = row
        repo.session.return_value = session
        assert (
            _can_skip_entity_extraction(repo, "10.1/a", "h1-current") is False
        )

    def test_returns_false_when_extraction_incomplete(self):
        repo = MagicMock()
        session = MagicMock()
        session.__enter__ = lambda self: session
        session.__exit__ = lambda self, *a: None
        row = {"taxonomy_hash": "h1", "extraction_incomplete": True}
        session.run.return_value.single.return_value = row
        repo.session.return_value = session
        assert _can_skip_entity_extraction(repo, "10.1/a", "h1") is False


# =============================================================================
# AC-3 / AC-4: per-batch shared dep construction
# =============================================================================


class TestSharedDependencyConstruction:
    @pytest.mark.asyncio
    async def test_extractors_constructed_once_per_batch(self, common_mocks):
        papers = [_normalized_paper(doi=f"10.1/{c}") for c in "abc"]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=0),
        )

        await ingest_papers("q", limit=10)

        # AC-3 / AC-20: extractors constructed once across N papers; the
        # OpenAI client factory ran exactly once.
        from agentic_kg.extraction.concept_extractor import ConceptExtractor
        from agentic_kg.extraction.method_extractor import MethodExtractor
        from agentic_kg.extraction.model_extractor import ModelExtractor
        from agentic_kg.extraction.topic_extractor import TopicExtractor

        # Each class was called once (via the per-batch construction).
        assert TopicExtractor.call_count == 1  # type: ignore[attr-defined]
        assert ConceptExtractor.call_count == 1  # type: ignore[attr-defined]
        assert ModelExtractor.call_count == 1  # type: ignore[attr-defined]
        assert MethodExtractor.call_count == 1  # type: ignore[attr-defined]
        assert common_mocks["llm_factory"].call_count == 1

    @pytest.mark.asyncio
    async def test_embedder_not_constructed_when_normalize_off(
        self, common_mocks,
    ):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=0),
        )

        await ingest_papers(
            "q", limit=10, normalize_cross_entity_collisions=False,
        )

        # embedder class was NOT instantiated because normalize is off.
        common_mocks["embedder_cls"].assert_not_called()


# =============================================================================
# AC-13: extract_entities=False short-circuits the V2 path
# =============================================================================


class TestExtractEntitiesOff:
    @pytest.mark.asyncio
    async def test_no_extractors_constructed(self, common_mocks):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )

        await ingest_papers("q", limit=10, extract_entities=False)

        from agentic_kg.extraction.concept_extractor import ConceptExtractor
        from agentic_kg.extraction.topic_extractor import TopicExtractor

        TopicExtractor.assert_not_called()  # type: ignore[attr-defined]
        ConceptExtractor.assert_not_called()  # type: ignore[attr-defined]
        common_mocks["llm_factory"].assert_not_called()
        common_mocks["extract"].assert_not_called()
        common_mocks["normalize"].assert_not_called()

    @pytest.mark.asyncio
    async def test_v1_still_runs(self, common_mocks):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=2),
        )

        await ingest_papers("q", limit=10, extract_entities=False)

        v1_call = (
            common_mocks["v1_intg_cls"]
            .return_value
            .integrate_extracted_problems
        )
        v1_call.assert_called_once()


# =============================================================================
# AC-11 / AC-12: normalize flag wiring
# =============================================================================


class TestNormalizeFlag:
    @pytest.mark.asyncio
    async def test_normalize_called_when_on_with_collisions(
        self, common_mocks,
    ):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        common_mocks["extract"].return_value = _extraction_result_with_entities()
        common_mocks["normalize"].return_value = MagicMock(
            is_clean=False, pairs_detected=1,
            pairs_resolved=1, pairs_rejected=0,
        )

        result = await ingest_papers("q", limit=10)

        common_mocks["normalize"].assert_awaited_once()
        assert result.papers_with_normalization_audit == 1
        # AC-11: normalization_result threads through to integrator.
        v2_kwargs = common_mocks["v2_int"].call_args.kwargs
        assert v2_kwargs["normalization_result"] is not None

    @pytest.mark.asyncio
    async def test_normalize_skipped_when_flag_off(self, common_mocks):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        common_mocks["extract"].return_value = _extraction_result_with_entities()

        await ingest_papers(
            "q", limit=10, normalize_cross_entity_collisions=False,
        )

        common_mocks["normalize"].assert_not_called()
        v2_kwargs = common_mocks["v2_int"].call_args.kwargs
        assert v2_kwargs["normalization_result"] is None


# =============================================================================
# AC-21 / AC-22: skip check end-to-end
# =============================================================================


class TestSkipCheck:
    @pytest.mark.asyncio
    async def test_skipped_paper_increments_counter(self, common_mocks):
        papers = [_normalized_paper(doi="10.1/already-extracted")]
        _wire_basic_search(common_mocks, papers)
        # Force skip-check to return True for any paper.
        with patch(
            "agentic_kg.ingestion._can_skip_entity_extraction",
            return_value=True,
        ):
            result = await ingest_papers("q", limit=10)

        assert result.papers_skipped_complete == 1
        # Nothing else ran for this paper.
        common_mocks["pipe"].return_value.process_pdf_url.assert_not_called()
        common_mocks["extract"].assert_not_called()
        common_mocks["normalize"].assert_not_called()
        common_mocks["v2_int"].assert_not_called()

    @pytest.mark.asyncio
    async def test_force_reextract_bypasses_skip(self, common_mocks):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        # Skip check would say True, but force_reextract=True bypasses.
        with patch(
            "agentic_kg.ingestion._can_skip_entity_extraction",
            return_value=True,
        ):
            result = await ingest_papers(
                "q", limit=10, force_reextract=True,
            )

        assert result.papers_skipped_complete == 0
        common_mocks["pipe"].return_value.process_pdf_url.assert_called_once()


# =============================================================================
# AC-5 / AC-6: PDF-less fallback + empty-input clean short-circuit
# =============================================================================


class TestTextSourceResolution:
    @pytest.mark.asyncio
    async def test_no_pdf_fails_loud_no_abstract_fallback(self, common_mocks):
        """SM-1: a paper with no candidate PDF source is skipped (full text or
        fail) — the abstract is NOT used as a fallback and extractors do NOT run.
        """
        papers = [
            _normalized_paper(
                pdf_url=None, abstract="this is the abstract text",
            ),
        ]
        _wire_basic_search(common_mocks, papers)

        result = await ingest_papers("q", limit=10)

        # No PDF source → pipeline not called, extractors not run, counted skipped.
        common_mocks["pipe"].return_value.process_pdf_url.assert_not_called()
        common_mocks["extract"].assert_not_awaited()
        assert result.papers_skipped_no_pdf == 1
        assert result.pdf_ok == 0

    @pytest.mark.asyncio
    async def test_no_pdf_no_abstract_clean_short_circuit(self, common_mocks):
        papers = [_normalized_paper(pdf_url=None, abstract=None)]
        _wire_basic_search(common_mocks, papers)

        result = await ingest_papers("q", limit=10)

        # Q3: V2 integration only runs when something was extracted or
        # V1 ran. With empty input AND PaperExtractionResult having
        # empty lists, V2 should NOT fire.
        common_mocks["v2_int"].assert_not_called()
        # Paper persists from Phase 1 — no integration error recorded.
        assert "10.1/a" not in result.extraction_errors


# =============================================================================
# AC-14: per-paper error isolation
# =============================================================================


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_v1_failure_skips_v2_for_paper(self, common_mocks):
        papers = [
            _normalized_paper(doi="10.1/a"),
            _normalized_paper(doi="10.1/b"),
        ]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        # V1 raises for the first paper, succeeds for the second.
        v1_call = (
            common_mocks["v1_intg_cls"]
            .return_value
            .integrate_extracted_problems
        )
        v1_call.side_effect = [
            RuntimeError("Neo4j hiccup"),
            _v1_integration_result(mentions_created=1),
        ]

        result = await ingest_papers("q", limit=10)

        assert "10.1/a" in result.extraction_errors
        assert "V1 integration" in result.extraction_errors["10.1/a"]
        # Per TL Q1: V1 failure → V2 skipped for paper A. V2 still
        # ran for paper B.
        assert common_mocks["v2_int"].call_count == 1
        assert (
            common_mocks["v2_int"].call_args.kwargs["paper_doi"] == "10.1/b"
        )

    @pytest.mark.asyncio
    async def test_pdf_processing_failure_records_error(self, common_mocks):
        """SM-1: a raising PDF fetch fails the paper loudly (categorized),
        records an error, and does NOT run the entity extractors/integrator."""
        papers = [_normalized_paper(doi="10.1/a")]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            side_effect=RuntimeError("PDF malformed"),
        )

        result = await ingest_papers("q", limit=10)

        assert "10.1/a" in result.extraction_errors
        assert "No usable full text" in result.extraction_errors["10.1/a"]
        assert result.acquisition_failures.get("failed_blocked") == 1
        # No usable text → extractors + V2 integrator never ran for this paper.
        common_mocks["extract"].assert_not_awaited()
        common_mocks["v2_int"].assert_not_called()

    @pytest.mark.asyncio
    async def test_per_paper_error_continues_batch(self, common_mocks):
        papers = [
            _normalized_paper(doi="10.1/a"),
            _normalized_paper(doi="10.1/b"),
        ]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        # extract_all_entities raises for first paper only.
        common_mocks["extract"].side_effect = [
            RuntimeError("transient"),
            PaperExtractionResult(failures=[]),
        ]

        result = await ingest_papers("q", limit=10)

        # First paper errored; second succeeded.
        assert "10.1/a" in result.extraction_errors
        assert result.status == "completed"


# =============================================================================
# AC-15: extraction_incomplete propagates to Paper node via counter
# =============================================================================


class TestExtractionIncompleteCounter:
    @pytest.mark.asyncio
    async def test_v2_integrator_marks_incomplete_increments_counter(
        self, common_mocks,
    ):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        common_mocks["extract"].return_value = _extraction_result_with_entities()
        common_mocks["v2_int"].return_value = _v2_integration_result(
            topics=0, concepts=1, models=1, methods=1, incomplete=True,
        )

        result = await ingest_papers("q", limit=10)

        assert result.papers_marked_incomplete == 1
        assert result.concepts_v2_linked == 1
        assert result.models_linked == 1
        assert result.methods_linked == 1


# =============================================================================
# Q3 / AC-9 / AC-10: V2 integration trigger
# =============================================================================


class TestV2IntegrationGate:
    @pytest.mark.asyncio
    async def test_v2_runs_when_only_entities_extracted_no_problems(
        self, common_mocks,
    ):
        """Q3: V2 fires when ANY entity extraction landed, even if V1
        produced zero problems."""
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        # PDF succeeds but yields zero problems.
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=0),
        )
        # Entity extractors yield 1 concept.
        common_mocks["extract"].return_value = PaperExtractionResult(
            problems=[],
            concepts=[
                ExtractedResearchConcept(
                    name="attention",
                    quoted_text="self-attention is used",
                    confidence=0.95,
                )
            ],
            failures=[],
        )

        await ingest_papers("q", limit=10)

        # V1 NOT called (no problems); V2 IS called.
        v1_call = (
            common_mocks["v1_intg_cls"]
            .return_value
            .integrate_extracted_problems
        )
        v1_call.assert_not_called()
        common_mocks["v2_int"].assert_called_once()
        # mentions list is empty per Q3 contract.
        kwargs = common_mocks["v2_int"].call_args.kwargs
        assert kwargs["mentions"] == []

    @pytest.mark.asyncio
    async def test_v2_skipped_when_zero_extractions_and_no_v1(
        self, common_mocks,
    ):
        papers = [_normalized_paper(pdf_url=None, abstract=None)]
        _wire_basic_search(common_mocks, papers)
        common_mocks["extract"].return_value = PaperExtractionResult(
            failures=[],
        )

        await ingest_papers("q", limit=10)

        common_mocks["v2_int"].assert_not_called()


# =============================================================================
# AC-1: CLI signature parity
# =============================================================================


class TestIngestionResultV2Counters:
    def test_default_counters_zero(self):
        r = IngestionResult(trace_id="x", query="q")
        assert r.topics_linked == 0
        assert r.concepts_v2_linked == 0
        assert r.models_linked == 0
        assert r.methods_linked == 0
        assert r.papers_marked_incomplete == 0
        assert r.papers_with_normalization_audit == 0
        assert r.papers_skipped_complete == 0


# =============================================================================
# AC-18: progress callback phases
# =============================================================================


class TestProgressCallbackPhases:
    @pytest.mark.asyncio
    async def test_normalized_and_entity_integrated_phases_emit(
        self, common_mocks,
    ):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        common_mocks["extract"].return_value = _extraction_result_with_entities()
        common_mocks["normalize"].return_value = MagicMock(
            is_clean=False, pairs_detected=1,
            pairs_resolved=1, pairs_rejected=0,
        )
        common_mocks["v2_int"].return_value = _v2_integration_result(
            topics=1, concepts=1, models=1, methods=1,
        )

        emitted: list[tuple[str, str, dict]] = []

        def cb(phase, doi, detail):
            emitted.append((phase, doi, detail))

        await ingest_papers("q", limit=10, on_progress=cb)

        phases = {e[0] for e in emitted}
        assert "normalized" in phases
        assert "entity_integrated" in phases

        ei = next(e for e in emitted if e[0] == "entity_integrated")
        # AC-18: entity_integrated payload carries the per-kind counts.
        assert ei[2] == {
            "topics": 1, "concepts_v2": 1, "models": 1, "methods": 1,
        }

    @pytest.mark.asyncio
    async def test_normalized_phase_not_emitted_when_flag_off(
        self, common_mocks,
    ):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)
        common_mocks["pipe"].return_value.process_pdf_url = AsyncMock(
            return_value=_processing_result(success=True, problem_count=1),
        )
        common_mocks["extract"].return_value = _extraction_result_with_entities()

        emitted: list[tuple[str, str, dict]] = []

        def cb(phase, doi, detail):
            emitted.append((phase, doi, detail))

        await ingest_papers(
            "q", limit=10, on_progress=cb,
            normalize_cross_entity_collisions=False,
        )

        phases = {e[0] for e in emitted}
        assert "normalized" not in phases
        # entity_integrated still fires.
        assert "entity_integrated" in phases

    @pytest.mark.asyncio
    async def test_skipped_complete_phase_emitted(self, common_mocks):
        papers = [_normalized_paper()]
        _wire_basic_search(common_mocks, papers)

        emitted: list[tuple[str, str, dict]] = []

        def cb(phase, doi, detail):
            emitted.append((phase, doi, detail))

        with patch(
            "agentic_kg.ingestion._can_skip_entity_extraction",
            return_value=True,
        ):
            await ingest_papers("q", limit=10, on_progress=cb)

        phases = {e[0] for e in emitted}
        assert "skipped_complete" in phases
