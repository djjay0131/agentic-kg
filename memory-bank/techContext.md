# Technical Context: Agentic Knowledge Graphs

## Repository Structure

This is a **monorepo** containing all components of the Agentic KG system:

```
agentic-kg/
├── packages/
│   ├── core/           # Knowledge graph logic, extended agents
│   │   └── src/agentic_kg/
│   ├── api/            # FastAPI/GraphQL service
│   │   └── src/
│   └── ui/             # Streamlit UI
│       └── src/
├── deploy/
│   ├── docker/         # Dockerfiles for each service
│   └── terraform/      # Infrastructure as code
├── memory-bank/        # Project documentation
├── construction/       # Sprint tracking
└── files/              # Reference materials
```

## Technologies and Frameworks

### Core Platform: Denario (Dependency)
- **Version**: 1.0+
- **Python**: 3.12+ required
- **Repository**: https://github.com/AstroPilot-AI/Denario
- **Usage**: Imported as `pip install denario`

### Agent Frameworks
- **AG2** (formerly AutoGen) - Multi-agent conversation framework
- **LangGraph** - Graph-based agent orchestration with state management
- **cmbagent** - Research analysis backend

### LLM Providers
- **OpenAI** - GPT models via API
- **Anthropic** - Claude models via API
- **Google Gemini** - Via AI Studio API
- **Perplexity** - For web-augmented responses

### Knowledge Graph Stack
- **Property Graph**: Neo4j (primary choice)
- **Vector Index**: For semantic search (TBD: Pinecone, Weaviate, or pgvector)
- **Hybrid Retrieval**: Combining graph queries with vector similarity

### Infrastructure
- **GCP Cloud Run** - Containerized deployment
- **GCP Secret Manager** - API key storage
- **GCP Artifact Registry** - Docker images
- **GCP Cloud Build** - CI/CD with GitHub triggers

## GCP Deployment (Current Setup)

| Component | Value |
|-----------|-------|
| GCP Project | `vt-gcp-00042` |
| Region | `us-central1` |
| Artifact Registry | `us-central1-docker.pkg.dev/vt-gcp-00042/denario` |
| Cloud Run Service | `denario` (Denario core) |
| Secrets | OPENAI, GOOGLE, ANTHROPIC, PERPLEXITY API keys |

### CI/CD Triggers (on djjay0131/Denario)
- `denario-prod-deploy`: Builds on `master` push
- `denario-dev-deploy`: Builds on `dev/*` push

**Note**: CI/CD for agentic-kg repo needs to be configured separately.

## Development Environment Setup

### Prerequisites
1. Python 3.12 or higher
2. GCP account with billing enabled
3. API keys for LLM providers
4. gcloud CLI installed and authenticated

### Local Development

```bash
# Clone the repository
git clone https://github.com/djjay0131/agentic-kg.git
cd agentic-kg

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install all packages in development mode
pip install -e packages/core
pip install -e packages/api
pip install -e packages/ui

# Or install with extras
pip install -e ".[dev]"
```

### Environment Variables

```bash
# LLM API Keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_API_KEY="AIza..."
export PERPLEXITY_API_KEY="pplx-..."

# For Neo4j (when configured)
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="..."
```

## Code Patterns

### Knowledge Graph Entity (Core)
```python
from agentic_kg.entities import Problem, EvidenceSpan

# Problems as first-class entities
problem = Problem(
    id="p-001",
    title="Optimal hyperparameter selection",
    assumptions=["Smooth loss landscape"],
    constraints=["Limited compute budget"],
    datasets=["ImageNet", "CIFAR-10"],
    metrics=["Accuracy", "F1"],
    evidence=[EvidenceSpan(paper_id="arxiv:2301.00001", text="...")],
)
```

### Using Denario Agents
```python
from denario import Denario

# Initialize with Denario's agent infrastructure
den = Denario(project_dir="project_dir")
den.set_data_description("Research context...")
den.get_idea()  # Uses Denario's idea generation agents
```

## Testing Strategy

### Unit Tests
```bash
pytest packages/core/tests/
pytest packages/api/tests/
```

### Integration Tests
```bash
# Test with local Neo4j
docker compose up neo4j
pytest tests/integration/
```

## Common Issues

### Issue: Denario Import Fails
**Solution**: Ensure denario is installed: `pip install denario>=1.0.0`

### Issue: Neo4j Connection Refused
**Solution**: Start Neo4j container: `docker compose up neo4j`

### Issue: API Key Not Found
**Solution**: Check environment variables or GCP Secret Manager configuration

## Related Repositories

| Repository | Purpose |
|------------|---------|
| [djjay0131/Denario](https://github.com/djjay0131/Denario) | Fork of core Denario library |
| [AstroPilot-AI/Denario](https://github.com/AstroPilot-AI/Denario) | Upstream Denario |
| [AstroPilot-AI/DenarioApp](https://github.com/AstroPilot-AI/DenarioApp) | Denario's Streamlit UI |
