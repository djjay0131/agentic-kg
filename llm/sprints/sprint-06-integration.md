# Sprint 06: Full-Stack Integration

## Overview

Wire the agent workflow end-to-end: connect WorkflowRunner to API startup, add event bus for decoupled WebSocket broadcasting, create integration tests, and add dev tooling (Makefile, Docker updates).

**Branch:** `claude/sprint-06-integration`
**Status:** Complete

## Components

### New Files

| File | Purpose |
|------|---------|
| `packages/core/src/agentic_kg/agents/events.py` | Async event bus for workflow step transitions |
| `packages/api/src/agentic_kg_api/tasks.py` | Background task execution + event-to-WebSocket bridge |
| `packages/api/tests/test_agent_integration.py` | 10 integration tests for agent API endpoints |
| `packages/api/tests/test_websocket_integration.py` | 12 tests for WebSocket + event bus |
| `Makefile` | Monorepo dev targets (install, test, lint, build, docker) |

### Modified Files

| File | Change |
|------|--------|
| `packages/api/src/agentic_kg_api/main.py` | WorkflowRunner init in lifespan hook |
| `docker/docker-compose.yml` | Docker socket mount for sandbox, ANTHROPIC_API_KEY |
| `memory-bank/activeContext.md` | Sprint 06 status |
| `memory-bank/phases.md` | Phase 5 complete, Phase 6 in progress |

## Architecture

```
API Startup (lifespan)
  ├── create_llm_client()         ← existing factory
  ├── get_repo/search/relations() ← existing singletons
  ├── WorkflowRunner(deps)        ← NEW wiring
  ├── set_workflow_runner(runner)  ← connects to router
  └── setup_event_bridge()        ← subscribes WS bridge

WorkflowRunner ──emit──→ EventBus ──bridge──→ WebSocket Manager ──broadcast──→ Clients
```

## Test Results

- 126 agent tests passing
- 111 API tests passing (89 existing + 22 new)
- 237 total tests
