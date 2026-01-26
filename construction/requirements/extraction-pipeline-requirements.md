# Extraction Pipeline - Requirements Specification

**Version:** 1.0
**Date:** 2026-01-26
**Sprint:** 03
**Status:** Draft

**Related Documents:**
- [Knowledge Graph Design](../design/phase-1-knowledge-graph.md)
- [Data Acquisition Requirements](data-acquisition-requirements.md)
- [Product Context](../../memory-bank/productContext.md)
- [System Patterns](../../memory-bank/systemPatterns.md)

---

## 1. Overview

This document specifies the requirements for the Information Extraction Pipeline, which extracts structured research problems from scientific papers and populates the Knowledge Graph.

### 1.1 Purpose

Enable automated extraction of research problems from papers with:
- PDF text extraction and section segmentation
- LLM-based structured extraction with schema validation
- Research problem identification from limitations/future work sections
- Relationship extraction between problems
- Provenance tracking for all extracted entities
- Human-in-the-loop review workflow

### 1.2 Scope

**In Scope:**
- PDF text extraction using open-source tools
- Section segmentation (abstract, intro, methods, results, limitations, future work)
- LLM-based entity extraction with structured output
- Problem entity creation with full schema population
- Relationship inference between problems
- Confidence scoring and provenance tracking
- Batch extraction pipeline

**Out of Scope:**
- Figure/table extraction (future enhancement)
- Mathematical formula parsing (future enhancement)
- Multi-language support (English only initially)
- Real-time extraction UI (Phase 4)

---

## 2. Functional Requirements

### 2.1 PDF Text Extraction

#### FR-2.1.1: Text Extraction Engine
**Priority:** High

The system shall extract text content from PDF files.

| Capability | Description |
|------------|-------------|
| PDF Parsing | Extract text from PDF documents |
| Layout Handling | Handle single/multi-column layouts |
| Page Assembly | Reconstruct text flow across pages |
| Encoding Support | Handle various character encodings |

**Technical Approach:**
- Primary: PyMuPDF (fitz) for fast, accurate extraction
- Fallback: pdfplumber for complex layouts
- Error handling for corrupted/scanned PDFs

**Requirements:**
- Accept PDF files from URL or local path
- Return structured text with page numbers
- Detect and flag scanned/image-based PDFs
- Handle large PDFs (100+ pages) efficiently

#### FR-2.1.2: Text Preprocessing
**Priority:** High

The system shall preprocess extracted text for downstream processing.

| Operation | Description |
|-----------|-------------|
| Cleanup | Remove headers, footers, page numbers |
| Dehyphenation | Rejoin hyphenated words at line breaks |
| Unicode Normalization | Normalize to NFC form |
| Whitespace | Normalize whitespace and line breaks |

---

### 2.2 Section Segmentation

#### FR-2.2.1: Section Identification
**Priority:** High

The system shall identify and segment paper sections.

| Section Type | Description | Priority |
|--------------|-------------|----------|
| Abstract | Paper summary | High |
| Introduction | Background and motivation | High |
| Related Work | Prior art discussion | Medium |
| Methods/Approach | Methodology description | Medium |
| Results/Experiments | Experimental findings | Medium |
| Discussion | Analysis of results | Medium |
| Limitations | Study limitations | **Critical** |
| Future Work | Proposed extensions | **Critical** |
| Conclusion | Summary and takeaways | High |
| References | Bibliography | Low |

**Requirements:**
- Use heuristic pattern matching for common headings
- Fall back to LLM classification for ambiguous sections
- Handle variations (e.g., "Experiments" vs "Evaluation")
- Support papers without explicit sections
- Return section boundaries with text content

#### FR-2.2.2: Section Prioritization
**Priority:** High

The system shall prioritize sections for problem extraction.

**Extraction Priority:**
1. **Limitations** - Primary source of acknowledged problems
2. **Future Work** - Direct statements of open problems
3. **Discussion** - Implicit problems from analysis
4. **Conclusion** - Summarized future directions
5. **Introduction** - Problem framing and motivation

---

### 2.3 Problem Extraction

#### FR-2.3.1: LLM-Based Extraction
**Priority:** High

The system shall use LLM to extract structured problems from text.

**Input:** Section text + paper metadata
**Output:** List of Problem entities conforming to schema

**Extraction Schema:**
```python
class ExtractedProblem:
    statement: str          # Clear problem statement
    domain: str             # Research domain
    assumptions: list[Assumption]
    constraints: list[Constraint]
    datasets: list[Dataset]
    metrics: list[Metric]
    baselines: list[Baseline]
    confidence: float       # Extraction confidence
```

**Requirements:**
- Use OpenAI GPT-4 or Claude 3.5 Sonnet
- Structured output via function calling / tool use
- Schema validation for all extracted entities
- Retry with prompt refinement on validation failure
- Configurable model selection

#### FR-2.3.2: Prompt Engineering
**Priority:** High

The system shall use carefully designed prompts for extraction.

**Prompt Components:**
1. **System Context**: Role definition and output format
2. **Schema Definition**: Expected output structure with examples
3. **Extraction Guidelines**: What constitutes a valid problem
4. **Section Context**: Paper title, authors, section type
5. **Quality Criteria**: Clarity, specificity, actionability

**Requirements:**
- Store prompts as version-controlled templates
- Support prompt variants for different section types
- Include few-shot examples for each section type
- Log prompts with extraction results for debugging

#### FR-2.3.3: Multi-Pass Extraction
**Priority:** Medium

The system shall support iterative extraction refinement.

**Pass 1: Candidate Identification**
- Identify potential problem statements
- Assign initial confidence scores

**Pass 2: Attribute Extraction**
- Extract assumptions, constraints, datasets
- Link to baseline methods mentioned

**Pass 3: Validation & Deduplication**
- Validate against schema
- Merge overlapping problems
- Assign final confidence scores

---

### 2.4 Relationship Extraction

#### FR-2.4.1: Problem-to-Problem Relations
**Priority:** Medium

The system shall identify relationships between extracted problems.

| Relation | Indicators |
|----------|------------|
| EXTENDS | "builds on", "extends", "further explores" |
| CONTRADICTS | "conflicts with", "challenges", "contrary to" |
| DEPENDS_ON | "requires", "prerequisite", "depends on" |
| REFRAMES | "redefines", "alternative view", "new perspective" |

**Requirements:**
- Extract relations from explicit textual cues
- Infer relations from semantic similarity
- Assign confidence scores to relations
- Store evidence (quoted text) for each relation

#### FR-2.4.2: Problem-to-Paper Links
**Priority:** High

The system shall create EXTRACTED_FROM relations.

**Requirements:**
- Link each problem to source paper via DOI
- Store extraction section and character offsets
- Preserve quoted evidence text
- Track extraction timestamp and model

---

### 2.5 Provenance Tracking

#### FR-2.5.1: Extraction Metadata
**Priority:** High

The system shall capture comprehensive extraction provenance.

| Field | Description |
|-------|-------------|
| extracted_at | Timestamp of extraction |
| extractor_version | Pipeline version |
| extraction_model | LLM model used |
| confidence_score | Overall confidence |
| prompt_version | Prompt template version |
| section_source | Source section(s) |
| quoted_text | Original supporting text |

**Requirements:**
- All metadata fields required (no nulls for core fields)
- Queryable extraction history
- Support for audit trails

#### FR-2.5.2: Confidence Scoring
**Priority:** Medium

The system shall assign meaningful confidence scores.

**Factors:**
- LLM reported confidence
- Schema completeness (% of fields populated)
- Section quality (limitations > discussion)
- Text clarity (explicit vs. implied)

**Calibration:**
- Score range: 0.0 - 1.0
- Target: scores correlate with human agreement rates
- Threshold for auto-acceptance: 0.85+

---

### 2.6 Batch Processing

#### FR-2.6.1: Batch Extraction Pipeline
**Priority:** High

The system shall support batch extraction from multiple papers.

**Requirements:**
- Accept list of paper DOIs or PDF URLs
- Process papers in parallel (configurable concurrency)
- Report progress and failures
- Store intermediate results for resume
- Configurable batch size (default: 10)

**Input Modes:**
1. DOI list → fetch paper → extract PDF → process
2. PDF URL list → download → process
3. Local PDF paths → process

#### FR-2.6.2: Resume Capability
**Priority:** Medium

The system shall support resuming failed batch jobs.

**Requirements:**
- Track batch job state (pending, in_progress, failed, complete)
- Skip already-processed papers on resume
- Aggregate statistics across resume attempts
- Clean up partial extractions on failure

---

### 2.7 Knowledge Graph Integration

#### FR-2.7.1: Problem Creation
**Priority:** High

The system shall create Problem entities in the Knowledge Graph.

**Requirements:**
- Map extracted data to Problem Pydantic model
- Validate all fields against schema
- Generate embeddings for problem statements
- Check for duplicate problems before creating
- Return created Problem entity with ID

#### FR-2.7.2: Deduplication
**Priority:** Medium

The system shall detect and merge duplicate problems.

**Deduplication Strategies:**
1. **Exact match**: Same quoted_text from same paper
2. **Semantic similarity**: Embedding cosine > 0.95
3. **Fuzzy statement match**: Normalized edit distance < 0.1

**Merge Behavior:**
- Keep problem with highest confidence
- Merge supplementary attributes
- Link both sources in evidence

#### FR-2.7.3: Relation Creation
**Priority:** Medium

The system shall create relations between entities.

**Requirements:**
- Create EXTRACTED_FROM for each problem
- Create problem-to-problem relations
- Validate both endpoints exist
- Handle relation updates (new evidence for existing relation)

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### NFR-3.1.1: Throughput
**Priority:** High

| Operation | Target |
|-----------|--------|
| PDF extraction | < 5 seconds per paper |
| Section segmentation | < 2 seconds |
| LLM extraction (per section) | < 30 seconds |
| Full paper processing | < 3 minutes |
| Batch (10 papers) | < 20 minutes |

#### NFR-3.1.2: Concurrency
**Priority:** Medium

| Metric | Target |
|--------|--------|
| Concurrent PDF extractions | 5 |
| Concurrent LLM requests | 3 (API rate limited) |
| Background batch jobs | 2 |

### 3.2 Quality

#### NFR-3.2.1: Extraction Accuracy
**Priority:** High

| Metric | Target |
|--------|--------|
| Problem identification F1 | > 0.70 |
| Attribute extraction accuracy | > 0.80 |
| Relation precision | > 0.75 |
| Schema compliance | 100% |

#### NFR-3.2.2: Human Alignment
**Priority:** Medium

| Metric | Target |
|--------|--------|
| Inter-annotator agreement | > 0.60 kappa |
| System-human agreement | Within 10% of inter-annotator |

### 3.3 Reliability

#### NFR-3.3.1: Error Handling
**Priority:** High

- Graceful degradation on PDF extraction failure
- Retry logic for LLM API failures
- Partial results preserved on batch failure
- Detailed error logging with context

#### NFR-3.3.2: Idempotency
**Priority:** High

- Re-extraction of same paper produces same results
- Duplicate detection prevents double-insertion
- Update timestamps reflect actual changes

### 3.4 Observability

#### NFR-3.4.1: Logging
**Priority:** Medium

- Log all extraction attempts with paper ID
- Log LLM requests/responses (sanitized)
- Log confidence scores and thresholds
- Log validation failures with context

#### NFR-3.4.2: Metrics
**Priority:** Low

- Track extraction success rate
- Track problems per paper distribution
- Track confidence score distribution
- Track processing time per stage

### 3.5 Configuration

#### NFR-3.5.1: Environment Variables
**Priority:** High

| Variable | Purpose | Default |
|----------|---------|---------|
| OPENAI_API_KEY | LLM API authentication | Required |
| EXTRACTION_MODEL | Model for extraction | gpt-4-turbo |
| EXTRACTION_TEMPERATURE | LLM temperature | 0.1 |
| CONFIDENCE_THRESHOLD | Auto-accept threshold | 0.85 |
| MAX_RETRIES | LLM retry attempts | 3 |
| BATCH_SIZE | Papers per batch | 10 |
| CONCURRENCY | Parallel extractions | 3 |

---

## 4. User Stories

### US-01: Extract Problems from Paper
**As a** researcher
**I want to** extract research problems from a paper PDF
**So that** I can identify open questions for continuation

**Acceptance Criteria:**
1. Given a paper PDF, When I run extraction, Then problems are identified
2. Given extraction results, When I review, Then each problem has statement and evidence
3. Given low-confidence problems, When flagged, Then they are marked for human review
4. Given extraction complete, When stored, Then problems appear in knowledge graph

---

### US-02: Batch Process Paper Collection
**As a** system administrator
**I want to** extract from multiple papers in batch
**So that** I can build the knowledge graph efficiently

**Acceptance Criteria:**
1. Given 50 paper DOIs, When I run batch, Then all are processed
2. Given batch progress, When running, Then I see completion percentage
3. Given failures in batch, When complete, Then report shows successes/failures
4. Given interrupted batch, When resumed, Then processing continues from checkpoint

---

### US-03: Review Extracted Problems
**As a** domain expert
**I want to** review low-confidence extractions
**So that** I can improve extraction quality

**Acceptance Criteria:**
1. Given problems < 0.85 confidence, When I query, Then I get review queue
2. Given a problem, When I review, Then I see evidence and context
3. Given I approve, When saved, Then human_reviewed is true
4. Given I reject, When saved, Then problem is marked deprecated

---

### US-04: Find Related Problems
**As a** researcher
**I want to** see problems that extend or depend on other problems
**So that** I can understand research progressions

**Acceptance Criteria:**
1. Given Problem A, When I query relations, Then I see EXTENDS/DEPENDS_ON links
2. Given relations, When displayed, Then confidence and evidence shown
3. Given no relations, When queried, Then empty result (not error)

---

## 5. Acceptance Criteria Matrix

| Requirement | Acceptance Test | Priority |
|-------------|-----------------|----------|
| FR-2.1.1 | PDF text extracted correctly | High |
| FR-2.2.1 | Sections identified with >80% accuracy | High |
| FR-2.3.1 | Problems extracted with valid schema | High |
| FR-2.3.2 | Prompts produce consistent results | High |
| FR-2.5.1 | All extractions have full provenance | High |
| FR-2.6.1 | Batch of 10 papers completes in <20 min | Medium |
| FR-2.7.1 | Problems stored in Knowledge Graph | High |
| NFR-3.2.1 | Extraction F1 > 0.70 on test set | High |

---

## 6. Dependencies

### 6.1 External Services
| Service | Purpose | Required |
|---------|---------|----------|
| OpenAI API | LLM extraction | Yes |
| Anthropic API | Alternative LLM | No |

### 6.2 Internal Dependencies
| Component | Purpose |
|-----------|---------|
| Data Acquisition Layer | Fetch papers/PDFs |
| Knowledge Graph Repository | Store problems |
| Embedding Service | Generate embeddings |

### 6.3 Python Packages
| Package | Version | Purpose |
|---------|---------|---------|
| PyMuPDF | >=1.23.0 | PDF extraction |
| pdfplumber | >=0.10.0 | Fallback PDF parsing |
| openai | >=1.0.0 | GPT API |
| anthropic | >=0.18.0 | Claude API (optional) |
| instructor | >=0.5.0 | Structured LLM output |
| tenacity | >=8.0.0 | Retry logic |

---

## 7. Constraints

1. **LLM Rate Limits**: Must stay within API rate limits (10K TPM for GPT-4)

2. **Cost Management**: LLM calls are expensive; optimize token usage

3. **Context Window**: GPT-4 Turbo: 128K tokens; section chunking needed for long papers

4. **PDF Quality**: Scanned PDFs require OCR (out of scope for v1)

5. **Language**: English only for v1; multi-language requires different prompts

---

## 8. Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| PDF Library | PyMuPDF primary | Fast, accurate, well-maintained |
| LLM Provider | OpenAI (default) | Best structured output support |
| Output Format | Pydantic via instructor | Type-safe, validated |
| Confidence Source | LLM self-reported + heuristics | Combined is more reliable |
| Section Detection | Heuristic + LLM fallback | Fast for common cases |
| Batch Persistence | SQLite job queue | Simple, file-based |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-26 | Claude | Initial requirements specification |
