# Technical Context

Last updated: 2026-07-02

## Languages and Frameworks

- **Python 3.12+** — primary language
- **Denario >=1.0** — core agent/document processing framework (fork of AstroPilot-AI/Denario)
- **FastAPI** — REST API backend (`packages/api/`)
- **Next.js 14** (App Router) — frontend UI (`packages/ui/`)
- **LangGraph** — stateful agent workflow orchestration
- **AG2** (formerly AutoGen) — multi-agent conversation framework
- **Pydantic >=2.0** — data validation and models
- **instructor** — structured LLM output extraction (declared in `pyproject.toml` since commit `8fb6756`)

## Knowledge Graph Stack

- **Neo4j 5.x+** — property graph database with native vector indexes
- **Embeddings**: OpenAI `text-embedding-3-small` (1536 dimensions)
- **Vector indexes**: Neo4j VECTOR indexes on ProblemMention, ProblemConcept, ResearchConcept, Model, Method nodes
- **Hybrid retrieval**: Graph traversal + vector similarity combined
- **APOC**: enabled for the smoke-test testcontainers Neo4j (workflow env: `NEO4J_PLUGINS: '["apoc"]'`)

## LLM Providers

- **OpenAI** — GPT models (primary extraction, description generation, cross-entity routing, agent LLM)
- **Anthropic** — Claude models
- **Google Gemini** — via Vertex AI (requires service account)
- **Perplexity** — web-augmented responses
- **L-1 (future)** — Local / low-cost SLM client for narrow tasks (description-gen, normalization router, extractors). Documented across E-6 / E-7 / E-8 V2 / entity-pipeline-orchestration ACs as the per-extractor `client: BaseLLMClient` injection swap point.

## Development Setup

```bash
# Clone and set up
git clone <repo-url>
cd agentic-kg

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with uv (or pip)
uv sync

# Run unit tests (excludes E2E)
pytest packages/core/tests/ --ignore=packages/core/tests/e2e -q

# Run smoke test against staging (legacy)
make smoke-test

# Run the entity-pipeline-orchestration smoke locally (Docker Neo4j + real ingest)
export OPENAI_API_KEY="sk-..."
make smoke-local

# Local development with Docker Compose
docker compose up
```

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `neo4j>=5.0.0` | Graph database driver |
| `pydantic>=2.0.0` | Data models and validation |
| `openai>=1.0.0` | LLM API client |
| `denario>=1.0.0` | Core framework |
| `langgraph` | Agent workflow graphs |
| `instructor` | Structured LLM output (declared in `pyproject.toml`) |
| `fitz` (PyMuPDF) | PDF text extraction |
| `cachetools` | TTL response caching |
| `httpx` | HTTP client for data acquisition |
| `testcontainers[neo4j]` | Ephemeral Neo4j in unit/integration tests |
| `PyYAML` | Workflow structure tests (`test_smoke_workflow_structure.py`) |

## Infrastructure and Deployment

- **GCP Project**: `vt-gcp-00042`
- **Region**: `us-central1`
- **API (staging)**: Cloud Run Service at `https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app`
- **Ingestion Job (staging)**: Cloud Run Job `agentic-kg-ingest-staging` (Terraform-managed)
- **Neo4j (staging)**: Compute Engine at `bolt://34.173.74.125:7687` (Browser: `http://34.173.74.125:7474`)
- **Neo4j Schema**: Initialized via `SchemaManager` — 6 constraints, 25 indexes (3 vector), version 2
- **Terraform IaC**: `infra/` directory — API service, ingest job, IAM, env vars
- **CI/CD**: Cloud Build (`cloudbuild.yaml`) with `_SERVICE=api|job` substitution
- **GitHub Actions workflows**:
  - `smoke-ingest.yml` — ci-smoke-test-ingestion (PR + daily cron + workflow_dispatch)
  - `integration-tests.yml` — integration suite against staging Neo4j
  - `test.yml` — unit tests
  - `deploy-branch.yml` / `deploy-master.yml` / `deploy-tag.yml` — Cloud Build triggers
  - `build-images.yml`, `code-review.yml`, `preview-docs.yml`, `update-docs.yml`
- **Secrets**:
  - **GCP Secret Manager** (for staging + production deploys): `OPENAI_API_KEY`, `NEO4J_PASSWORD`
  - **GitHub Actions Secrets** (for smoke-ingest workflow): `OPENAI_API_KEY` (added 2026-07-02)
- **Docker**: `Dockerfile` (API, full image), `docker/Dockerfile.job` (core-only, job image)

## Cloud Run Job env vars (post entity-pipeline-orchestration)

The Cloud Run Job's `job_runner._parse_env` reads these — all default-on except the last:

| Env var | Default | Effect when `false` (case-insensitive) |
|---|---|---|
| `INGEST_QUERY` | (required; exits 2 if missing) | — |
| `INGEST_LIMIT` | `20` | — |
| `INGEST_SOURCES` | `""` (all) | — |
| `INGEST_AGENT_WORKFLOW` | `true` | Disables Medium/Low confidence agent routing |
| `INGEST_MIN_CONFIDENCE` | `0.5` | — |
| `POPULATE_CITATIONS` | `true` | Skips `populate_citations` in PaperImporter (E-8 V2) |
| `EXTRACT_ENTITIES` | `true` | Skips the 4 entity extractors + normalizer (entity-pipeline-orchestration) |
| `NORMALIZE_CROSS_ENTITY` | `true` | Keeps extractors on, skips E-7 routing LLM |
| `FORCE_REEXTRACT` | `false` (opt-in) | Bypasses the AC-21 per-paper skip check |

Pattern: default-on flags use `.lower() != "false"`; opt-in flags use `.lower() == "true"`.

## CLI flags mirror env vars

`agentic-kg ingest`:
- `--query` / `--limit` / `--sources` / `--min-confidence`
- `--dry-run` — search only, no writes
- `--no-agent-workflow` — mirror of `INGEST_AGENT_WORKFLOW=false`
- `--sanity-check-only`
- `--no-populate-citations` — E-8 V2
- `--no-extract-entities` — entity-pipeline-orchestration
- `--no-normalize-cross-entity` — entity-pipeline-orchestration
- `--force-rewrite` — override AC-13 purge guardrail
- `--force-reextract` — bypass AC-21 skip check
- `--json` — machine-readable output; used by ci-smoke-test workflow

## Build and Test

- **Build system**: Hatchling (`pyproject.toml`)
- **Linter**: Ruff (line-length 100, target py312)
- **Test framework**: pytest with pytest-asyncio
- **Test markers**: `e2e`, `slow`, `costly`, `integration`
- **Test paths**: `packages/core/tests/`, `packages/api/tests/`
- **Test count**: 1994 core unit tests passing, 234 skipped (e2e + Docker-gated integration)
- **CI smoke**: 76 dedicated smoke-suite tests (scripts/smoke_assert.py + workflow structure + Makefile parity)

## Technical Constraints

- Neo4j vector indexes require 5.x+ (no Aura Free tier support for VECTOR)
- Staging Neo4j runs on a single Compute Engine VM (not HA)
- E2E tests require staging environment variables and live services
- LLM API calls incur costs — tests marked `costly` are skipped by default
- Fork PRs cannot access `OPENAI_API_KEY` secret → ci-smoke-test-ingestion fails with a cryptic OpenAI auth error on fork PRs (accepted limitation)
- The Cloud Run Job's default behavior changed on 2026-06-23 (entity-pipeline-orchestration merge): `EXTRACT_ENTITIES=true` default adds ~5-6 LLM calls per paper. Operators re-running historic pipelines should explicitly set `EXTRACT_ENTITIES=false` if they want V1 cost parity.
