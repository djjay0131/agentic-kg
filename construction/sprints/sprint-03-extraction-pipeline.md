# Sprint 03: Information Extraction Pipeline

**Status:** Planning
**Started:** 2026-01-26
**Target Completion:** TBD

**Requirements:** [extraction-pipeline-requirements.md](../requirements/extraction-pipeline-requirements.md)
**Related ADRs:** ADR-013 (LLM Extraction Approach)

---

## Sprint Goal

Implement the information extraction pipeline to identify and extract structured research problems from scientific papers, with full provenance tracking.

---

## User Stories

### US-01: Extract Problems from Paper
**As a** researcher
**I want to** extract research problems from a paper PDF
**So that** I can identify open questions for continuation

### US-02: Batch Process Paper Collection
**As a** system administrator
**I want to** extract from multiple papers in batch
**So that** I can build the knowledge graph efficiently

### US-03: Review Extracted Problems
**As a** domain expert
**I want to** review low-confidence extractions
**So that** I can improve extraction quality

---

## Task Breakdown

### Phase 1: PDF Processing Foundation

#### Task 1: PDF Text Extraction Module
**Priority:** High | **Estimate:** 4 hours

Create a PDF extraction module using PyMuPDF.

**Subtasks:**
- [ ] Create `extraction/pdf_extractor.py` with text extraction
- [ ] Handle page-by-page extraction with metadata
- [ ] Implement text cleanup (headers, footers, page numbers)
- [ ] Add dehyphenation and unicode normalization
- [ ] Handle extraction errors gracefully
- [ ] Unit tests for extractor

**Files to create:**
- `packages/core/src/agentic_kg/extraction/__init__.py`
- `packages/core/src/agentic_kg/extraction/pdf_extractor.py`
- `packages/core/tests/extraction/test_pdf_extractor.py`

#### Task 2: Section Segmentation
**Priority:** High | **Estimate:** 4 hours

Implement section detection and segmentation.

**Subtasks:**
- [ ] Create `extraction/section_segmenter.py`
- [ ] Heuristic-based section heading detection
- [ ] Handle common heading variations
- [ ] Section type classification
- [ ] Return structured section objects
- [ ] Unit tests for segmenter

**Files to create:**
- `packages/core/src/agentic_kg/extraction/section_segmenter.py`
- `packages/core/tests/extraction/test_section_segmenter.py`

---

### Phase 2: LLM Integration

#### Task 3: LLM Client Wrapper
**Priority:** High | **Estimate:** 3 hours

Create an abstraction layer for LLM providers.

**Subtasks:**
- [ ] Create `extraction/llm_client.py` with provider abstraction
- [ ] Implement OpenAI client with structured output (instructor)
- [ ] Add retry logic with exponential backoff
- [ ] Token counting and usage tracking
- [ ] Configuration via environment variables
- [ ] Unit tests with mocked responses

**Files to create:**
- `packages/core/src/agentic_kg/extraction/llm_client.py`
- `packages/core/tests/extraction/test_llm_client.py`

#### Task 4: Prompt Templates
**Priority:** High | **Estimate:** 3 hours

Create versioned prompt templates for extraction.

**Subtasks:**
- [ ] Create `extraction/prompts/` directory structure
- [ ] System prompt template for problem extraction
- [ ] Section-specific prompt variants
- [ ] Few-shot examples for each section type
- [ ] Prompt loading and formatting utilities
- [ ] Unit tests for prompt formatting

**Files to create:**
- `packages/core/src/agentic_kg/extraction/prompts/__init__.py`
- `packages/core/src/agentic_kg/extraction/prompts/templates.py`
- `packages/core/src/agentic_kg/extraction/prompts/examples.py`

---

### Phase 3: Problem Extraction

#### Task 5: Extraction Schema Models
**Priority:** High | **Estimate:** 2 hours

Create Pydantic models for extraction output.

**Subtasks:**
- [ ] Create `extraction/schemas.py` with extraction-specific models
- [ ] ExtractedProblem with all attributes
- [ ] ExtractionResult container
- [ ] Validation rules matching Problem schema
- [ ] Conversion to Problem model
- [ ] Unit tests for schemas

**Files to create:**
- `packages/core/src/agentic_kg/extraction/schemas.py`
- `packages/core/tests/extraction/test_schemas.py`

#### Task 6: Problem Extractor Core
**Priority:** High | **Estimate:** 5 hours

Implement the main problem extraction logic.

**Subtasks:**
- [ ] Create `extraction/problem_extractor.py`
- [ ] Single section extraction method
- [ ] Multi-section extraction with deduplication
- [ ] Confidence score calculation
- [ ] Error handling and partial results
- [ ] Unit tests with mocked LLM

**Files to create:**
- `packages/core/src/agentic_kg/extraction/problem_extractor.py`
- `packages/core/tests/extraction/test_problem_extractor.py`

#### Task 7: Relationship Extractor
**Priority:** Medium | **Estimate:** 4 hours

Extract relationships between problems.

**Subtasks:**
- [ ] Create `extraction/relation_extractor.py`
- [ ] Textual cue detection for relations
- [ ] Semantic similarity-based inference
- [ ] Confidence scoring for relations
- [ ] Evidence preservation
- [ ] Unit tests for relation extraction

**Files to create:**
- `packages/core/src/agentic_kg/extraction/relation_extractor.py`
- `packages/core/tests/extraction/test_relation_extractor.py`

---

### Phase 4: Pipeline Integration

#### Task 8: Paper Processing Pipeline
**Priority:** High | **Estimate:** 4 hours

Create the end-to-end paper processing pipeline.

**Subtasks:**
- [ ] Create `extraction/pipeline.py`
- [ ] Orchestrate PDF → sections → extraction → storage
- [ ] Handle both PDF URLs and local files
- [ ] Integration with data acquisition layer
- [ ] Progress tracking and logging
- [ ] Unit tests for pipeline

**Files to create:**
- `packages/core/src/agentic_kg/extraction/pipeline.py`
- `packages/core/tests/extraction/test_pipeline.py`

#### Task 9: Knowledge Graph Integration
**Priority:** High | **Estimate:** 3 hours

Integrate extracted problems with the Knowledge Graph.

**Subtasks:**
- [ ] Create `extraction/kg_integration.py`
- [ ] Convert ExtractedProblem to Problem model
- [ ] Generate embeddings for problem statements
- [ ] Check for existing problems (deduplication)
- [ ] Create EXTRACTED_FROM relations
- [ ] Unit tests for integration

**Files to create:**
- `packages/core/src/agentic_kg/extraction/kg_integration.py`
- `packages/core/tests/extraction/test_kg_integration.py`

#### Task 10: Batch Processing
**Priority:** Medium | **Estimate:** 3 hours

Implement batch extraction with job tracking.

**Subtasks:**
- [ ] Create `extraction/batch.py`
- [ ] SQLite job queue for batch state
- [ ] Parallel processing with rate limiting
- [ ] Resume capability for failed jobs
- [ ] Progress reporting
- [ ] Unit tests for batch processing

**Files to create:**
- `packages/core/src/agentic_kg/extraction/batch.py`
- `packages/core/tests/extraction/test_batch.py`

---

### Phase 5: CLI & Testing

#### Task 11: CLI Commands
**Priority:** Medium | **Estimate:** 2 hours

Add CLI commands for extraction operations.

**Subtasks:**
- [ ] Add `extract` command to CLI
- [ ] Single paper extraction
- [ ] Batch extraction from file
- [ ] Progress output and summary
- [ ] Integration tests

**Files to modify:**
- `packages/core/src/agentic_kg/cli.py` (if exists) or create new

#### Task 12: Fixtures and Conftest
**Priority:** Medium | **Estimate:** 2 hours

Create test fixtures for extraction tests.

**Subtasks:**
- [ ] Create `tests/extraction/conftest.py`
- [ ] Sample PDF fixture
- [ ] Section text fixtures
- [ ] Mock LLM response fixtures
- [ ] Expected extraction results

**Files to create:**
- `packages/core/tests/extraction/conftest.py`

#### Task 13: Integration Tests
**Priority:** Low | **Estimate:** 3 hours

End-to-end integration tests for extraction.

**Subtasks:**
- [ ] Create integration test suite
- [ ] Test with real (open access) papers
- [ ] Validate extracted problem quality
- [ ] Performance benchmarks

**Files to create:**
- `packages/core/tests/extraction/test_integration.py`

---

## Task Summary

| Task | Description | Priority | Status |
|------|-------------|----------|--------|
| 1 | PDF Text Extraction Module | High | Pending |
| 2 | Section Segmentation | High | Pending |
| 3 | LLM Client Wrapper | High | Pending |
| 4 | Prompt Templates | High | Pending |
| 5 | Extraction Schema Models | High | Pending |
| 6 | Problem Extractor Core | High | Pending |
| 7 | Relationship Extractor | Medium | Pending |
| 8 | Paper Processing Pipeline | High | Pending |
| 9 | Knowledge Graph Integration | High | Pending |
| 10 | Batch Processing | Medium | Pending |
| 11 | CLI Commands | Medium | Pending |
| 12 | Fixtures and Conftest | Medium | Pending |
| 13 | Integration Tests | Low | Deferred |

**High Priority Tasks:** 8
**Medium Priority Tasks:** 4
**Low Priority/Deferred:** 1

**Estimated Total:** ~42 hours

---

## Dependencies

### External
- OpenAI API key for LLM extraction
- Sample PDFs for testing (open access papers)

### Internal
- Phase 2 Data Acquisition (complete) - for paper fetching
- Phase 1 Knowledge Graph (complete) - for storage

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM API rate limits | Medium | Implement rate limiting, caching |
| PDF extraction quality | Medium | Multiple fallback parsers |
| Extraction accuracy | High | Iterative prompt refinement |
| Cost overrun | Medium | Token budgets, batch optimization |

---

## Definition of Done

- [ ] All high-priority tasks complete
- [ ] Unit tests passing with >80% coverage
- [ ] Manual verification with 10 sample papers
- [ ] Documentation updated
- [ ] PR approved and merged

---

## Notes

### Prompt Engineering Strategy
Start with simple prompts and iterate based on extraction quality:
1. V1: Direct extraction with schema
2. V2: Chain-of-thought reasoning
3. V3: Self-reflection and validation

### Quality Checkpoints
- Task 6 complete: Manual review of 5 extractions
- Task 9 complete: Verify KG population
- Sprint complete: Benchmark against manual extraction

---

## Revision History

| Date | Changes |
|------|---------|
| 2026-01-26 | Initial sprint planning |
