# Sprint 05: Agent Implementation

**Branch:** `claude/sprint-05-agent-implementation`
**Status:** In Progress
**Started:** 2026-01-29

## Overview

Implements four specialized research agents (Ranking, Continuation, Evaluation, Synthesis) forming a closed-loop research workflow. Uses LangGraph for orchestration, WebSocket for real-time HITL checkpoints, and Docker sandbox for code evaluation.

## Architecture

```
UI (Next.js)   <-- WebSocket -->  FastAPI Backend
                                       |
                                  LangGraph Workflow
                                  +-------------+
                                  |   Ranking   | <- SearchService
                                  |   v HITL    |
                                  | Continuation| <- Problem context
                                  |   v HITL    |
                                  |  Evaluation | <- Docker sandbox
                                  |   v HITL    |
                                  |  Synthesis  | -> Write back to KG
                                  +-------------+
```

## Tasks

### Phase 1: Agent Foundation (Tasks 1-4) - COMPLETE

- [x] Task 1: Agent schemas (`agents/schemas.py`)
- [x] Task 2: ResearchState (`agents/state.py`)
- [x] Task 3: Base agent + prompts (`agents/base.py`, `agents/prompts.py`)
- [x] Task 4: Agent config (`agents/config.py`)

### Phase 2: Individual Agents (Tasks 5-8) - COMPLETE

- [x] Task 5: Ranking Agent (`agents/ranking.py`)
- [x] Task 6: Continuation Agent (`agents/continuation.py`)
- [x] Task 7: Evaluation Agent + Sandbox (`agents/evaluation.py`, `agents/sandbox.py`)
- [x] Task 8: Synthesis Agent (`agents/synthesis.py`)

### Phase 3: Orchestration (Tasks 9-11) - COMPLETE

- [x] Task 9: LangGraph workflow (`agents/workflow.py`)
- [x] Task 10: HITL checkpoints (`agents/checkpoints.py`)
- [x] Task 11: Workflow runner (`agents/runner.py`)

### Phase 4: API + UI Integration (Tasks 12-14) - COMPLETE

- [x] Task 12: WebSocket infrastructure (`api/websocket.py`)
- [x] Task 13: Agent API router (`api/routers/agents.py`)
- [x] Task 14: UI workflow pages (workflows list, detail, stepper, checkpoint forms)

### Phase 5: Testing + Documentation (Tasks 15-17) - IN PROGRESS

- [ ] Task 15: Agent unit tests
- [ ] Task 16: Workflow and integration tests
- [ ] Task 17: Documentation and sprint docs

## New Dependencies

```toml
# packages/core/pyproject.toml
"langgraph>=0.2.0"
"langchain-core>=0.3.0"
"docker>=7.0.0"
```

## Files Created

### Core Agent Module (`packages/core/src/agentic_kg/agents/`)
- `__init__.py` - Package exports
- `schemas.py` - Pydantic output models
- `state.py` - LangGraph ResearchState TypedDict
- `base.py` - BaseAgent ABC
- `prompts.py` - Prompt templates
- `config.py` - Agent configuration
- `ranking.py` - Ranking Agent
- `continuation.py` - Continuation Agent
- `evaluation.py` - Evaluation Agent
- `sandbox.py` - Docker sandbox execution
- `synthesis.py` - Synthesis Agent
- `workflow.py` - LangGraph StateGraph
- `checkpoints.py` - HITL checkpoint manager
- `runner.py` - Workflow session runner

### API (`packages/api/src/agentic_kg_api/`)
- `websocket.py` - WebSocket connection manager
- `routers/agents.py` - Workflow REST + WS endpoints

### UI (`packages/ui/src/`)
- `lib/websocket.ts` - WebSocket client hook
- `components/WorkflowStepper.tsx` - Step progress visualization
- `components/CheckpointForm.tsx` - Human decision form
- `app/workflows/page.tsx` - Workflow list/dashboard
- `app/workflows/[id]/page.tsx` - Workflow detail page
