# Technical Context

## Languages and Frameworks

- **Python 3.12+** — primary language
- **Denario >=1.0** — core agent/document processing framework (fork of AstroPilot-AI/Denario)
- **FastAPI** — REST API backend (`packages/api/`)
- **Next.js 14** (App Router) — frontend UI (`packages/ui/`)
- **LangGraph** — stateful agent workflow orchestration
- **AG2** (formerly AutoGen) — multi-agent conversation framework
- **Pydantic >=2.0** — data validation and models
- **instructor** — structured LLM output extraction

## Knowledge Graph Stack

- **Neo4j 5.x+** — property graph database with native vector indexes
- **Embeddings**: OpenAI `text-embedding-3-small` (1536 dimensions)
- **Vector indexes**: Neo4j VECTOR indexes on ProblemMention and ProblemConcept nodes
- **Hybrid retrieval**: Graph traversal + vector similarity combined

## LLM Providers

- **OpenAI** — GPT models (primary extraction and agent LLM)
- **Anthropic** — Claude models
- **Google Gemini** — via Vertex AI (requires service account)
- **Perplexity** — web-augmented responses

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

# Run smoke test against staging
make smoke-test

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
| `instructor` | Structured LLM output |
| `fitz` (PyMuPDF) | PDF text extraction |
| `cachetools` | TTL response caching |

## Infrastructure and Deployment

- **GCP Project**: `vt-gcp-00042`
- **Region**: `us-central1`
- **API (staging)**: Cloud Run at `https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app`
- **Neo4j (staging)**: Compute Engine at `bolt://34.173.74.125:7687`
- **Terraform IaC**: `infra/` directory
- **CI/CD**: Cloud Build (`cloudbuild.yaml`), GitHub Actions for docs
- **Secrets**: GCP Secret Manager for API keys
- **Docker**: Python 3.12-slim base image

## Build and Test

- **Build system**: Hatchling (`pyproject.toml`)
- **Linter**: Ruff (line-length 100, target py312)
- **Test framework**: pytest with pytest-asyncio
- **Test markers**: `e2e`, `slow`, `costly`, `integration`
- **Test paths**: `packages/core/tests/`, `packages/api/tests/`

## Technical Constraints

- Neo4j vector indexes require 5.x+ (no Aura Free tier support for VECTOR)
- Staging Neo4j runs on a single Compute Engine VM (not HA)
- E2E tests require staging environment variables and live services
- LLM API calls incur costs — tests marked `costly` are skipped by default
