# AGENTS.md

## Cursor Cloud specific instructions

### Project Overview

Agentic Knowledge Graphs for Research Progression — a monorepo with three packages:
- **`packages/core`** — Python 3.12 core library (knowledge graph, extraction, agents)
- **`packages/api`** — FastAPI backend (port 8000), depends on core
- **`packages/ui`** — Next.js 14 frontend dashboard (port 3000)

### Services

| Service | How to run | Port | Required |
|---------|-----------|------|----------|
| Neo4j 5.15 | `docker compose -f docker/docker-compose.yml up neo4j -d` | 7474 (HTTP), 7687 (Bolt) | Yes |
| FastAPI API | `NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword123 uvicorn agentic_kg_api.main:app --reload --host 0.0.0.0 --port 8000` | 8000 | Yes |
| Next.js UI | `cd packages/ui && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev` | 3000 | Optional |

### Running services — caveats

- Docker must be started first: `sudo dockerd &>/tmp/dockerd.log &` then wait a few seconds.
- Neo4j container healthcheck reports `unhealthy` because curl is not available inside the `neo4j:5.15.0-community` image. The database itself runs fine; verify with `curl http://localhost:7474` from the host.
- The `.env` files (`/workspace/.env` and `/workspace/docker/.env`) are gitignored and must be created from `.env.example` / `docker/.env.example`. The Neo4j password used by docker-compose is set via `NEO4J_PASSWORD` in `docker/.env`.
- The API reads Neo4j config from environment variables (`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`), not from `.env` files automatically. Pass them when starting uvicorn.
- `~/.local/bin` must be on PATH to use pip user-installed binaries (ruff, pytest, uvicorn, etc.): `export PATH="$HOME/.local/bin:$PATH"`.

### Lint, test, build commands

See the `Makefile` at the repo root for canonical commands. Key commands:
- **Lint:** `make lint` (runs `ruff check` + `ruff format --check`)
- **Test (core):** `pytest packages/core/tests/ --ignore=packages/core/tests/e2e --ignore=packages/core/tests/integration -v`
- **Test (API):** `pytest packages/api/tests/ --ignore=packages/api/tests/e2e -v`
- **UI lint:** `cd packages/ui && npx next lint`
- **UI build:** `cd packages/ui && npm run build`

### Known pre-existing test failures

A few tests in `packages/core/tests/` fail due to Pydantic model schema changes (missing required fields in test fixtures). These are pre-existing and not caused by environment setup:
- `test_arbiter_no_retry_on_final_round` — reasoning string too short for validator
- `test_evaluate_approve_logs_trace_id` — logging capture issue
- `test_review_queue.py` — multiple tests fail/error due to `ProblemMention` schema changes (missing `section`, `quoted_text` fields)
