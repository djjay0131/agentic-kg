# System Architecture & Implementation Plan

**Created:** 2026-01-05
**Status:** Draft
**Purpose:** High-level system design identifying major components and implementation priorities

---

## 1. System Overview

The Agentic Knowledge Graph system enables research progression by treating **research problems as first-class entities**. The system ingests papers, extracts structured problems, stores them in a knowledge graph, and uses agents to prioritize and propose research continuations.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENTIC KG SYSTEM                                  │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Layer 3: AGENTIC ORCHESTRATION                    │    │
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │    │
│  │   │ Ranking  │  │Continua- │  │Evaluation│  │Synthesis │           │    │
│  │   │  Agent   │  │tion Agent│  │  Agent   │  │  Agent   │           │    │
│  │   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘           │    │
│  │        └─────────────┴─────────────┴─────────────┘                  │    │
│  │                          │ LangGraph/AG2                            │    │
│  └──────────────────────────┼──────────────────────────────────────────┘    │
│                             │                                                │
│  ┌──────────────────────────┼──────────────────────────────────────────┐    │
│  │                    Layer 2: KNOWLEDGE GRAPH                          │    │
│  │                          ▼                                           │    │
│  │   ┌─────────────────────────────────────────────────────────────┐   │    │
│  │   │                    Neo4j Graph Database                      │   │    │
│  │   │  ┌─────────────┐        ┌──────────────────────────────┐    │   │    │
│  │   │  │  Problems   │───────▶│ EXTENDS | CONTRADICTS |      │    │   │    │
│  │   │  │  (nodes)    │◀───────│ DEPENDS_ON | REFRAMES        │    │   │    │
│  │   │  └─────────────┘        └──────────────────────────────┘    │   │    │
│  │   │         │                                                    │   │    │
│  │   │         ▼                                                    │   │    │
│  │   │  ┌─────────────────────────────────────────┐                │   │    │
│  │   │  │  Vector Index (problem embeddings)      │                │   │    │
│  │   │  └─────────────────────────────────────────┘                │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  │                          ▲                                           │    │
│  │   ┌──────────────────────┴──────────────────────────────────────┐   │    │
│  │   │              Repository Layer (Python)                       │   │    │
│  │   │  • CRUD operations  • Hybrid queries  • Validation          │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  └──────────────────────────┬──────────────────────────────────────────┘    │
│                             │                                                │
│  ┌──────────────────────────┼──────────────────────────────────────────┐    │
│  │                    Layer 1: EXTRACTION PIPELINE                      │    │
│  │                          │                                           │    │
│  │   ┌──────────────────────▼──────────────────────────────────────┐   │    │
│  │   │                  LLM Extraction Engine                       │   │    │
│  │   │  • Problem extraction    • Relation inference                │   │    │
│  │   │  • Assumption/constraint extraction                          │   │    │
│  │   │  • Dataset/metric extraction                                 │   │    │
│  │   │  • Embedding generation                                      │   │    │
│  │   └──────────────────────┬──────────────────────────────────────┘   │    │
│  │                          │                                           │    │
│  │   ┌──────────────────────▼──────────────────────────────────────┐   │    │
│  │   │               Document Processing                            │   │    │
│  │   │  • PDF parsing         • Section segmentation                │   │    │
│  │   │  • Text extraction     • Schema validation                   │   │    │
│  │   └──────────────────────┬──────────────────────────────────────┘   │    │
│  └──────────────────────────┼──────────────────────────────────────────┘    │
│                             │                                                │
│  ┌──────────────────────────┼──────────────────────────────────────────┐    │
│  │                    Layer 0: DATA ACQUISITION                        │    │
│  │                          │                                           │    │
│  │   ┌──────────────────────▼──────────────────────────────────────┐   │    │
│  │   │              Paper Acquisition Layer                         │   │    │
│  │   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │    │
│  │   │  │   arXiv     │  │  OpenAlex   │  │  Paywall Access     │  │   │    │
│  │   │  │   (open)    │  │   (open)    │  │  (Denario integration│  │   │    │
│  │   │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  │                          │                                           │    │
│  │   ┌──────────────────────▼──────────────────────────────────────┐   │    │
│  │   │              Semantic Scholar API                            │   │    │
│  │   │  • Paper metadata    • Citations    • SPECTER2 embeddings   │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         INFRASTRUCTURE                                │   │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │   │
│  │   │ GCP Cloud   │  │ Artifact    │  │   Secret    │  │  Cloud    │  │   │
│  │   │    Run      │  │  Registry   │  │  Manager    │  │  Build    │  │   │
│  │   └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Major Components

### Component Registry

| ID | Component | Layer | Description | Status |
|----|-----------|-------|-------------|--------|
| **C1** | GCP Infrastructure | Infra | Cloud Run, CI/CD, secrets | Complete |
| **C2** | Neo4j Database | L2-KG | Graph database with vector index | Not Started |
| **C3** | Problem Schema | L2-KG | Pydantic models for Problem entity | Not Started |
| **C4** | Repository Layer | L2-KG | CRUD, queries, validation | Not Started |
| **C5** | Semantic Scholar Client | L0-Data | API client for paper metadata | Not Started |
| **C6** | Paper Acquisition Layer | L0-Data | Unified PDF retrieval (open + paywall) | Not Started |
| **C7** | Document Processor | L1-Extract | PDF parsing, section segmentation | Not Started |
| **C8** | LLM Extraction Engine | L1-Extract | Problem/relation extraction | Not Started |
| **C9** | Embedding Service | L1-Extract | Problem embedding generation | Not Started |
| **C10** | Ranking Agent | L3-Agent | Prioritize problems by tractability | Not Started |
| **C11** | Continuation Agent | L3-Agent | Propose next research steps | Not Started |
| **C12** | Evaluation Agent | L3-Agent | Execute reproducible workflows | Not Started |
| **C13** | Synthesis Agent | L3-Agent | Summarize and update graph | Not Started |
| **C14** | Agent Orchestrator | L3-Agent | LangGraph workflow coordination | Not Started |
| **C15** | Human Review UI | L3-Agent | Approval checkpoints in Streamlit | Not Started |
| **C16** | API Layer | Cross | FastAPI endpoints for KG access | Not Started |

---

## 3. Component Details

### 3.1 Layer 0: Data Acquisition

#### C5: Semantic Scholar Client
**Purpose:** Fetch paper metadata, citations, and embeddings from Semantic Scholar API

**Responsibilities:**
- Paper search by keyword, DOI, or arXiv ID
- Fetch paper details (title, abstract, authors, venue, year)
- Retrieve citation graph (references and citing papers)
- Get SPECTER2 embeddings for paper similarity
- Handle rate limiting (1 RPS authenticated)

**Interfaces:**
```python
class SemanticScholarClient:
    def search_papers(query: str, limit: int) -> List[PaperMetadata]
    def get_paper(paper_id: str) -> PaperMetadata
    def get_citations(paper_id: str) -> List[Citation]
    def get_embedding(paper_id: str) -> List[float]  # SPECTER2
```

**Dependencies:** None (external API)

---

#### C6: Paper Acquisition Layer
**Purpose:** Unified interface for retrieving paper PDFs from any source

**Responsibilities:**
- Download PDFs from arXiv (open access)
- Fetch papers from OpenAlex/PubMed Central
- Integrate Denario paywall access code
- Publisher API integration where available
- Caching to avoid redundant downloads
- Track paper source/provenance

**Interfaces:**
```python
class PaperAcquisitionLayer:
    def get_pdf(identifier: str) -> bytes  # DOI, arXiv ID, URL
    def get_pdf_path(identifier: str) -> Path
    def is_available(identifier: str) -> bool
    def get_source_type(identifier: str) -> SourceType  # ARXIV, OPENACCESS, PAYWALL
```

**Dependencies:**
- research-ai-paper repository (https://github.com/DJJayNet/research-ai-paper)
- Semantic Scholar Client (for identifier resolution)

**Integration Note:** Existing code in research-ai-paper repository for paywall access must be integrated.

---

### 3.2 Layer 1: Extraction Pipeline

#### C7: Document Processor
**Purpose:** Parse PDFs and segment into structured sections

**Responsibilities:**
- PDF text extraction (PyMuPDF/fitz)
- Layout-aware parsing (headers, paragraphs, figures)
- Section identification (Abstract, Introduction, Methods, Results, Discussion, Limitations, Future Work, References)
- Table/figure extraction (optional)
- Output structured document representation

**Interfaces:**
```python
class DocumentProcessor:
    def process(pdf_path: Path) -> StructuredDocument
    def get_section(doc: StructuredDocument, section: SectionType) -> str
    def get_text_span(doc: StructuredDocument, start: int, end: int) -> str
```

**Dependencies:** None

---

#### C8: LLM Extraction Engine
**Purpose:** Extract structured research problems from paper text using LLMs

**Responsibilities:**
- Problem statement extraction (from Limitations, Future Work sections)
- Assumption extraction (explicit and implicit)
- Constraint extraction (computational, data, methodological, theoretical)
- Dataset mention extraction
- Metric/baseline extraction
- Relation inference (EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES)
- Confidence scoring for all extractions
- Evidence span tracking (character offsets)

**Interfaces:**
```python
class LLMExtractionEngine:
    def extract_problems(doc: StructuredDocument) -> List[ExtractedProblem]
    def extract_relations(problems: List[Problem]) -> List[ExtractedRelation]
    def get_confidence(extraction: Any) -> float
```

**Dependencies:**
- Document Processor (C7)
- LLM providers (OpenAI, Anthropic, etc.)

---

#### C9: Embedding Service
**Purpose:** Generate embeddings for problems and other entities

**Responsibilities:**
- Generate problem statement embeddings
- Generate assumption/constraint embeddings (optional)
- Batch embedding generation for efficiency
- Model selection (OpenAI text-embedding-3-small default)

**Interfaces:**
```python
class EmbeddingService:
    def embed(text: str) -> List[float]  # 1536 dims
    def embed_batch(texts: List[str]) -> List[List[float]]
    def embed_problem(problem: Problem) -> List[float]
```

**Dependencies:**
- OpenAI API (or alternative embedding provider)

---

### 3.3 Layer 2: Knowledge Graph

#### C2: Neo4j Database
**Purpose:** Graph database for storing problems and relations

**Responsibilities:**
- Store Problem nodes with all attributes
- Store Paper, Author nodes
- Store relation edges (EXTENDS, CONTRADICTS, etc.)
- Maintain vector index for problem embeddings
- Support Cypher queries

**Setup:**
- Neo4j Community Edition (Docker) for development
- Neo4j Aura for production (or self-hosted)
- Vector index on problem embeddings

**Dependencies:** None (infrastructure)

---

#### C3: Problem Schema
**Purpose:** Pydantic models defining the Problem entity and related types

**Responsibilities:**
- Define Problem model with all attributes
- Define sub-models (Assumption, Constraint, Dataset, Metric, Baseline)
- Define Evidence and ExtractionMetadata models
- Define relation types
- Validation logic

**Interfaces:**
```python
class Problem(BaseModel):
    id: str
    statement: str
    domain: Optional[str]
    status: ProblemStatus
    assumptions: List[Assumption]
    constraints: List[Constraint]
    datasets: List[Dataset]
    metrics: List[Metric]
    baselines: List[Baseline]
    evidence: Evidence
    extraction_metadata: ExtractionMetadata
    embedding: Optional[List[float]]
    created_at: datetime
    updated_at: datetime
    version: int
```

**Dependencies:** None

---

#### C4: Repository Layer
**Purpose:** Data access layer for Neo4j operations

**Responsibilities:**
- CRUD operations for Problems
- CRUD operations for Papers, Authors
- Relation management (create, query, delete)
- Hybrid queries (graph + vector)
- Schema initialization
- Transaction management

**Interfaces:**
```python
class ProblemRepository:
    def create(problem: Problem) -> str
    def get(id: str) -> Optional[Problem]
    def update(id: str, problem: Problem) -> bool
    def delete(id: str) -> bool
    def search_semantic(query_embedding: List[float], limit: int) -> List[Problem]
    def search_hybrid(query_embedding: List[float], filters: dict) -> List[Problem]
    def get_related(id: str, relation_type: RelationType) -> List[Problem]
    def create_relation(from_id: str, to_id: str, relation: Relation) -> bool
```

**Dependencies:**
- Neo4j Database (C2)
- Problem Schema (C3)

---

### 3.4 Layer 3: Agentic Orchestration

#### C10: Ranking Agent
**Purpose:** Prioritize research problems by tractability and potential impact

**Responsibilities:**
- Score problems by dataset availability
- Score by baseline existence
- Score by constraint feasibility
- Consider domain relevance
- Produce ranked list with explanations

**Interfaces:**
```python
class RankingAgent:
    def rank(problems: List[Problem], criteria: RankingCriteria) -> RankedList
```

**Dependencies:**
- Repository Layer (C4)

---

#### C11: Continuation Agent
**Purpose:** Propose next research steps for a given problem

**Responsibilities:**
- Analyze problem context (assumptions, constraints)
- Consider related problems (EXTENDS, DEPENDS_ON)
- Propose concrete next experiments
- Propose methodology extensions
- Generate proposal with rationale

**Interfaces:**
```python
class ContinuationAgent:
    def propose(problem: Problem, context: GraphContext) -> Proposal
```

**Dependencies:**
- Repository Layer (C4)
- LLM provider

---

#### C12: Evaluation Agent
**Purpose:** Execute reproducible evaluation workflows

**Responsibilities:**
- Set up evaluation environment
- Run proposed experiments
- Collect and validate results
- Compare against baselines
- Report outcomes

**Interfaces:**
```python
class EvaluationAgent:
    def evaluate(proposal: Proposal, datasets: List[Dataset]) -> EvaluationResult
```

**Dependencies:**
- Paper Acquisition Layer (C6) - for datasets
- Compute resources

---

#### C13: Synthesis Agent
**Purpose:** Summarize results and update the knowledge graph

**Responsibilities:**
- Analyze evaluation results
- Generate summary report
- Propose graph updates (new problems, relations, status changes)
- Update problem status (open → in_progress → resolved)
- Create new EXTENDS relations for successful continuations

**Interfaces:**
```python
class SynthesisAgent:
    def synthesize(result: EvaluationResult) -> GraphUpdate
    def apply_update(update: GraphUpdate) -> bool
```

**Dependencies:**
- Repository Layer (C4)

---

#### C14: Agent Orchestrator
**Purpose:** Coordinate agent workflows using LangGraph

**Responsibilities:**
- Define agent workflow graph
- Manage state transitions
- Route to appropriate agents
- Handle human review checkpoints
- Maintain execution history

**Interfaces:**
```python
class AgentOrchestrator:
    def run_workflow(initial_state: WorkflowState) -> WorkflowResult
    def pause_for_review(state: WorkflowState) -> ReviewRequest
    def resume_after_review(decision: ReviewDecision) -> WorkflowResult
```

**Dependencies:**
- All agents (C10-C13)
- LangGraph

---

#### C15: Human Review UI
**Purpose:** Streamlit interface for human oversight

**Responsibilities:**
- Display pending review items
- Show agent proposals with context
- Enable approve/modify/reject decisions
- Capture feedback for system improvement
- Track review history

**Dependencies:**
- Agent Orchestrator (C14)
- Streamlit

---

### 3.5 Cross-Cutting

#### C16: API Layer
**Purpose:** REST API for external access to knowledge graph

**Responsibilities:**
- Paper ingestion endpoints
- Problem query endpoints
- Graph traversal endpoints
- Agent trigger endpoints
- Authentication/authorization

**Interfaces:**
```
POST /api/papers/ingest
GET  /api/problems/{id}
GET  /api/problems/search
GET  /api/problems/{id}/related
POST /api/agents/rank
POST /api/agents/continue
```

**Dependencies:**
- Repository Layer (C4)
- Agent Orchestrator (C14)
- FastAPI

---

## 4. Dependency Graph

```
                    ┌─────────────────────────────────────────────┐
                    │         C15: Human Review UI                │
                    └─────────────────────┬───────────────────────┘
                                          │
                    ┌─────────────────────▼───────────────────────┐
                    │         C14: Agent Orchestrator             │
                    └─────────────────────┬───────────────────────┘
                                          │
        ┌─────────────┬───────────────────┼───────────────────┬─────────────┐
        ▼             ▼                   ▼                   ▼             ▼
   ┌─────────┐   ┌─────────┐        ┌─────────┐        ┌─────────┐   ┌─────────┐
   │C10:Rank │   │C11:Cont │        │C12:Eval │        │C13:Synth│   │C16: API │
   └────┬────┘   └────┬────┘        └────┬────┘        └────┬────┘   └────┬────┘
        │             │                   │                  │             │
        └─────────────┴───────────────────┼──────────────────┴─────────────┘
                                          │
                    ┌─────────────────────▼───────────────────────┐
                    │         C4: Repository Layer                │
                    └─────────────────────┬───────────────────────┘
                                          │
                    ┌─────────────────────┼───────────────────────┐
                    ▼                     ▼                       ▼
              ┌─────────┐           ┌─────────┐            ┌─────────────┐
              │C2: Neo4j│           │C3:Schema│            │C9: Embedding│
              └─────────┘           └─────────┘            └──────┬──────┘
                                                                  │
                    ┌─────────────────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────────────────────────────────────────────────┐
        │                    C8: LLM Extraction Engine                       │
        └───────────────────────────────┬───────────────────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │         C7: Document Processor         │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │      C6: Paper Acquisition Layer       │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │      C5: Semantic Scholar Client       │
                    └───────────────────────────────────────┘
```

---

## 5. Implementation Phases

### Phase 0: Infrastructure ✅ COMPLETE
- GCP Cloud Run deployment
- CI/CD pipeline
- Secret management

### Phase 1: Knowledge Graph Foundation
**Components:** C2, C3, C4, C9
**Goal:** Store and query research problems

| Priority | Component | Effort | Description |
|----------|-----------|--------|-------------|
| P0 | C3: Problem Schema | S | Pydantic models for Problem entity |
| P0 | C2: Neo4j Database | S | Docker setup, schema initialization |
| P0 | C4: Repository Layer | M | CRUD operations, basic queries |
| P1 | C9: Embedding Service | S | OpenAI embedding integration |
| P1 | C4: Hybrid Search | M | Vector + structured queries |

**Deliverables:**
- Neo4j running in Docker
- Problem CRUD operations working
- Hybrid search functional
- Sample data loaded (10-20 problems)

---

### Phase 1.5: Data Acquisition Layer (NEW)
**Components:** C5, C6
**Goal:** Acquire papers from any source (open + paywall)

| Priority | Component | Effort | Description |
|----------|-----------|--------|-------------|
| P0 | C5: Semantic Scholar Client | S | Paper metadata and citations |
| P0 | C6: Paper Acquisition (Open) | M | arXiv, OpenAlex integration |
| P1 | C6: Paper Acquisition (Paywall) | M | Denario integration |

**Deliverables:**
- Semantic Scholar API client working
- Can download PDFs from arXiv
- Can download paywalled papers via Denario
- Unified interface regardless of source

**Dependencies:** Denario repository code review needed

---

### Phase 2: Extraction Pipeline
**Components:** C7, C8
**Goal:** Extract problems from papers automatically

| Priority | Component | Effort | Description |
|----------|-----------|--------|-------------|
| P0 | C7: Document Processor | M | PDF parsing, section segmentation |
| P0 | C8: Problem Extraction | L | LLM prompts, schema validation |
| P1 | C8: Relation Inference | M | EXTENDS, CONTRADICTS detection |
| P1 | C8: Provenance Tracking | S | Character offset linking |

**Deliverables:**
- Can parse any research PDF
- Extract problems from Limitations/Future Work
- Extract assumptions, constraints, datasets, metrics
- Link all extractions to source text
- Infer relations between problems

**Dependencies:** Phase 1, Phase 1.5

---

### Phase 3: Agentic Orchestration
**Components:** C10, C11, C12, C13, C14, C15
**Goal:** Agents that operate on the knowledge graph

| Priority | Component | Effort | Description |
|----------|-----------|--------|-------------|
| P0 | C10: Ranking Agent | M | Problem prioritization |
| P0 | C14: Orchestrator (basic) | M | LangGraph workflow |
| P1 | C11: Continuation Agent | L | Proposal generation |
| P1 | C15: Human Review UI | M | Streamlit approval interface |
| P2 | C12: Evaluation Agent | L | Experiment execution |
| P2 | C13: Synthesis Agent | M | Graph updates from results |

**Deliverables:**
- Rank problems by tractability
- Generate research continuation proposals
- Human review workflow
- Graph updates from agent actions

**Dependencies:** Phases 1, 2

---

### Phase 4: API & Integration
**Components:** C16
**Goal:** External access and production readiness

| Priority | Component | Effort | Description |
|----------|-----------|--------|-------------|
| P0 | C16: Core API | M | CRUD endpoints, search |
| P1 | C16: Agent API | M | Trigger workflows via API |
| P2 | C16: Auth | S | API key authentication |

**Deliverables:**
- REST API for all KG operations
- Can trigger agent workflows via API
- Production-ready deployment

**Dependencies:** All previous phases

---

## 6. Prioritized Implementation Plan

### Recommended Order

```
Phase 0 (Complete)
     │
     ▼
Phase 1: Knowledge Graph Foundation ─────────────────┐
     │                                                │
     ▼                                                │
Phase 1.5: Data Acquisition Layer ◄──────────────────┘
     │                               (can run in parallel
     ▼                                after C3 complete)
Phase 2: Extraction Pipeline
     │
     ▼
Phase 3: Agentic Orchestration
     │
     ▼
Phase 4: API & Integration
```

### Sprint Breakdown

| Sprint | Focus | Components | Duration Est. |
|--------|-------|------------|---------------|
| **Sprint 1** | KG Foundation | C2, C3, C4 (basic) | 1-2 weeks |
| **Sprint 2** | Embeddings + Search | C9, C4 (hybrid) | 1 week |
| **Sprint 3** | Data Acquisition | C5, C6 | 1-2 weeks |
| **Sprint 4** | Document Processing | C7 | 1 week |
| **Sprint 5** | Problem Extraction | C8 (extraction) | 2 weeks |
| **Sprint 6** | Relation Inference | C8 (relations) | 1 week |
| **Sprint 7** | Ranking Agent | C10, C14 (basic) | 1-2 weeks |
| **Sprint 8** | Continuation + Review | C11, C15 | 2 weeks |
| **Sprint 9** | Evaluation + Synthesis | C12, C13 | 2-3 weeks |
| **Sprint 10** | API Layer | C16 | 1 week |

**Total Estimated Duration:** 13-18 weeks

---

## 7. Critical Path

The critical path runs through:

1. **C3: Problem Schema** - Everything depends on the data model
2. **C4: Repository Layer** - All components need to read/write to KG
3. **C6: Paper Acquisition** - Extraction needs papers
4. **C8: LLM Extraction** - Agents need problems to work with
5. **C14: Agent Orchestrator** - Coordinates all agent activity

Parallelization opportunities:
- C5 (Semantic Scholar) and C6 (Paper Acquisition) can run in parallel with C2-C4
- C9 (Embeddings) can be developed alongside C4
- C15 (Human Review UI) can be developed alongside C10-C11

---

## 8. Risk Areas

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM extraction quality | High | Schema validation, confidence thresholds, human review |
| Paywall integration complexity | Medium | Early investigation of Denario code |
| Neo4j performance at scale | Medium | Start with indexing strategy, profile early |
| Agent coordination bugs | Medium | Extensive testing, gradual autonomy |
| Rate limits (Semantic Scholar, LLMs) | Low | Caching, batching, retry logic |

---

## 9. Open Questions

1. **research-ai-paper Integration:** Review repository capabilities and interface design
2. **Evaluation Agent Scope:** How much actual experiment execution vs. proposal only?
3. **Multi-tenancy:** Do we need user/project isolation in the graph?
4. **Embedding Model:** Stick with OpenAI or consider local models?
5. **Deduplication Threshold:** What similarity score = duplicate problem?

---

## 10. Next Steps

1. **Immediate:** Finalize this design document
2. **Sprint 1 Kickoff:** Set up Neo4j, implement Problem schema
3. **Parallel Investigation:** Review Denario paywall access code
4. **Design Review:** Validate component interfaces before implementation

---

## References

- [Phase 1 Knowledge Graph Design](./phase-1-knowledge-graph.md)
- [KG Options Comparison](./kg-options-comparison.md)
- [System Patterns](../../memory-bank/systemPatterns.md)
- [Phases Coordination](../../memory-bank/phases.md)
