# Sprint 04: API Service + Production Web UI

**Status:** In Progress
**Started:** 2026-01-27
**Target Completion:** TBD

**Requirements:** N/A (ADR-driven)
**Related ADRs:** ADR-014 (Streamlit → Next.js Decision)

---

## Sprint Goal

Build a FastAPI backend and Next.js frontend to expose the extraction pipeline and knowledge graph as a production web application.

---

## User Stories

### US-01: Browse Research Problems
**As a** researcher
**I want to** browse and search extracted research problems
**So that** I can discover open questions in my field

### US-02: View Problem Details
**As a** researcher
**I want to** see full problem details including evidence and confidence
**So that** I can evaluate whether to pursue a problem

### US-03: Extract from Papers
**As a** researcher
**I want to** submit a paper URL or text for extraction
**So that** I can add new problems to the knowledge graph

### US-04: Visualize Knowledge Graph
**As a** researcher
**I want to** see a visual graph of problems and relationships
**So that** I can understand connections between research areas

---

## Architecture

```
Next.js Frontend (packages/ui/)     Port 3000
        ↓ REST
FastAPI Backend (packages/api/)     Port 8000
        ↓
Core Library (packages/core/)
        ↓
Neo4j Database                      Port 7687
```

**Key Decision:** Replaced Streamlit with Next.js + React for production-quality UI.

---

## Task Breakdown

### Phase 1: API Service (Tasks 1-6)

#### Task 1: FastAPI Scaffolding
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Create FastAPI application structure.

**Subtasks:**
- [x] Create `packages/api/src/agentic_kg_api/main.py` with FastAPI app
- [x] Health check endpoint (`GET /health`)
- [x] CORS middleware, error handlers, logging
- [x] Config loading from environment
- [x] Stats endpoint (`GET /api/stats`)

**Files created:**
- `packages/api/src/agentic_kg_api/main.py`
- `packages/api/src/agentic_kg_api/config.py`
- `packages/api/src/agentic_kg_api/__init__.py`

#### Task 2: Problem Endpoints
**Priority:** High | **Estimate:** 3 hours | **Status:** ✅ Complete

CRUD endpoints for problems.

**Subtasks:**
- [x] `GET /api/problems` — list/search problems (query, domain, status filters)
- [x] `GET /api/problems/{id}` — get problem by ID with full details
- [x] `PUT /api/problems/{id}` — update problem (status, review)
- [x] `DELETE /api/problems/{id}` — soft delete
- [x] Response models with Pydantic

**Files created:**
- `packages/api/src/agentic_kg_api/routers/problems.py`
- `packages/api/src/agentic_kg_api/schemas.py`
- `packages/api/src/agentic_kg_api/dependencies.py`

#### Task 3: Paper Endpoints
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Endpoints for paper management.

**Subtasks:**
- [x] `GET /api/papers` — list papers
- [x] `GET /api/papers/{doi}` — get paper by DOI

**Files created:**
- `packages/api/src/agentic_kg_api/routers/papers.py`

#### Task 4: Search Endpoint
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Hybrid search functionality.

**Subtasks:**
- [x] `POST /api/search` — hybrid search (semantic + structured)
- [x] Accept: query text, domain filter, status filter
- [x] Return: ranked results with scores

**Files created:**
- `packages/api/src/agentic_kg_api/routers/search.py`

#### Task 5: Extraction Trigger Endpoints
**Priority:** High | **Estimate:** 3 hours | **Status:** ✅ Complete

Endpoints to trigger extraction pipeline.

**Subtasks:**
- [x] `POST /api/extract` — extract from URL or raw text
- [x] Return extraction results with stages

**Files created:**
- `packages/api/src/agentic_kg_api/routers/extract.py`

#### Task 6: API Tests
**Priority:** Medium | **Estimate:** 3 hours | **Status:** Pending

Unit tests for API endpoints.

**Subtasks:**
- [ ] Unit tests for each router
- [ ] Test with mocked repository

**Files to create:**
- `packages/api/tests/`

---

### Phase 2: Next.js Frontend (Tasks 7-12)

#### Task 7: Next.js Project Setup
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Initialize Next.js application.

**Subtasks:**
- [x] Initialize Next.js 14+ with App Router, TypeScript
- [x] Tailwind CSS for styling
- [x] React Query for data fetching

**Files created:**
- `packages/ui/package.json`
- `packages/ui/tsconfig.json`
- `packages/ui/next.config.js`
- `packages/ui/tailwind.config.ts`
- `packages/ui/postcss.config.js`

#### Task 8: Layout & Navigation
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Application shell with navigation.

**Subtasks:**
- [x] App shell with sidebar navigation
- [x] Pages: Dashboard, Problems, Papers, Extract, Graph
- [x] API client utility

**Files created:**
- `packages/ui/src/app/layout.tsx`
- `packages/ui/src/app/providers.tsx`
- `packages/ui/src/app/globals.css`
- `packages/ui/src/lib/api.ts`
- `packages/ui/src/components/Sidebar.tsx`

#### Task 9: Dashboard Page
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Main dashboard with summary stats.

**Subtasks:**
- [x] Summary stats (total problems, papers)
- [x] Recent problems feed
- [x] Quick search bar

**Files created:**
- `packages/ui/src/app/page.tsx`

#### Task 10: Problem Browser
**Priority:** High | **Estimate:** 3 hours | **Status:** ✅ Complete

Searchable/filterable problem list and detail view.

**Subtasks:**
- [x] Searchable/filterable table of problems
- [x] Problem detail view (statement, evidence, confidence, metadata)
- [x] Status management (open → in_progress → resolved)

**Files created:**
- `packages/ui/src/app/problems/page.tsx`
- `packages/ui/src/app/problems/[id]/page.tsx`

#### Task 11: Paper Browser & Extraction
**Priority:** High | **Estimate:** 3 hours | **Status:** ✅ Complete

Paper list and extraction form.

**Subtasks:**
- [x] Paper list with search
- [x] Extraction form: URL or text input
- [x] Extraction progress indicator

**Files created:**
- `packages/ui/src/app/papers/page.tsx`
- `packages/ui/src/app/extract/page.tsx`

#### Task 12: Knowledge Graph Visualization
**Priority:** Medium | **Estimate:** 4 hours | **Status:** ✅ Complete

Interactive graph visualization.

**Subtasks:**
- [x] Interactive graph view using react-force-graph-2d
- [x] Show problem nodes, relation edges, paper connections
- [x] Click nodes to navigate to detail views
- [x] Graph API endpoint for data

**Files created:**
- `packages/ui/src/app/graph/page.tsx`
- `packages/ui/src/components/GraphView.tsx`
- `packages/api/src/agentic_kg_api/routers/graph.py`

---

### Phase 3: Integration (Tasks 13-14)

#### Task 13: Docker & Deployment Updates
**Priority:** High | **Estimate:** 2 hours | **Status:** ✅ Complete

Update Docker configuration for Next.js.

**Subtasks:**
- [x] Update `docker/Dockerfile.ui` for Next.js (Node 20 base)
- [x] Update `docker/docker-compose.yml` UI service

**Files modified:**
- `docker/Dockerfile.ui`
- `docker/docker-compose.yml`

#### Task 14: Documentation & Sprint Docs
**Priority:** Medium | **Estimate:** 2 hours | **Status:** ✅ Complete

Create sprint documentation.

**Subtasks:**
- [x] Create `construction/sprints/sprint-04-api-and-ui.md`
- [x] Update `memory-bank/activeContext.md`
- [x] Update `memory-bank/phases.md`

**Files created:**
- `construction/sprints/sprint-04-api-and-ui.md`

---

## Task Summary

| Task | Description | Priority | Status |
|------|-------------|----------|--------|
| 1 | FastAPI Scaffolding | High | ✅ Complete |
| 2 | Problem Endpoints | High | ✅ Complete |
| 3 | Paper Endpoints | High | ✅ Complete |
| 4 | Search Endpoint | High | ✅ Complete |
| 5 | Extraction Trigger Endpoints | High | ✅ Complete |
| 6 | API Tests | Medium | Pending |
| 7 | Next.js Project Setup | High | ✅ Complete |
| 8 | Layout & Navigation | High | ✅ Complete |
| 9 | Dashboard Page | High | ✅ Complete |
| 10 | Problem Browser | High | ✅ Complete |
| 11 | Paper Browser & Extraction | High | ✅ Complete |
| 12 | Knowledge Graph Visualization | Medium | ✅ Complete |
| 13 | Docker & Deployment Updates | High | ✅ Complete |
| 14 | Documentation & Sprint Docs | Medium | ✅ Complete |

**High Priority Tasks:** 10 ✅ (10/10 complete)
**Medium Priority Tasks:** 4 (3/4 complete)

**Progress:** 13/14 tasks complete (93%)

---

## Dependencies

### External
- Node.js 20+ for Next.js development
- Neo4j running for API to work (docker-compose handles this)
- OpenAI API key for extraction endpoints and embeddings

### Internal
- Phase 1 Knowledge Graph (complete) - for storage
- Phase 2 Data Acquisition (complete) - for paper fetching
- Phase 3 Extraction Pipeline (complete) - for extraction

---

## Verification

1. `docker-compose up neo4j api ui` — all three services start
2. `curl localhost:8000/health` — API responds
3. Open `localhost:3000` — UI loads with dashboard
4. Search for problems, view details, trigger extraction from UI
5. Graph visualization renders with sample data

---

## Revision History

| Date | Changes |
|------|---------|
| 2026-01-27 | Initial sprint, Tasks 1-14 (except API tests) |
