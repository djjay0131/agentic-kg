# Technical Context: Agentic Knowledge Graphs with Denario

## Technologies and Frameworks

### Core Platform: Denario
- **Version**: 1.0+
- **Python**: 3.12+ required
- **Repository**: https://github.com/AstroPilot-AI/Denario (this is a fork)
- **Documentation**: https://denario.readthedocs.io/

### Agent Frameworks
- **AG2** (formerly AutoGen) - Multi-agent conversation framework
- **LangGraph** - Graph-based agent orchestration with state management
- **cmbagent** - Research analysis backend

### LLM Providers
- **OpenAI** - GPT models via API
- **Anthropic** - Claude models via API
- **Google Gemini** - Via Vertex AI (requires service account)
- **Perplexity** - For web-augmented responses

### Infrastructure
- **GCP Cloud Run** - Containerized deployment (recommended)
- **GCP GKE** - Kubernetes for larger deployments
- **Vertex AI** - Gemini model hosting and inference
- **Docker** - Container runtime (Python 3.12-slim base)

### Knowledge Graph Stack (To Be Selected)
- **Property Graph**: Neo4j, Amazon Neptune, or similar
- **RDF/SPARQL**: If interoperability with existing SKGs needed
- **Vector Index**: For semantic search (e.g., Pinecone, Weaviate, pgvector)

## Development Environment Setup

### Prerequisites
1. Python 3.12 or higher
2. Docker and Docker Compose
3. GCP account with billing enabled
4. API keys for LLM providers

### Local Development Setup

```bash
# Clone the repository
git clone <repo-url>
cd Denario

# Create virtual environment (choose one)
python3 -m venv Denario_env
source Denario_env/bin/activate

# Or with uv
uv sync

# Install Denario with GUI support
pip install -e ".[app]"

# Run the GUI locally
denario run
```

### Environment Variables

```bash
# LLM API Keys
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
export PERPLEXITY_API_KEY="pplx-..."

# For Vertex AI (Gemini via AG2)
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/gemini.json"
```

### GCP Deployment Setup

**1. Select or Create GCP Project**
```bash
# List existing projects you have access to
gcloud projects list

# Set an existing project (recommended for org users)
gcloud config set project <existing-project-id>

# OR create a new project (requires project creation permissions)
# Skip this if you don't have org-level create rights
gcloud projects create <new-project-id> 2>/dev/null || echo "Using existing project"
gcloud config set project <new-project-id>

# Verify which project is active
gcloud config get-value project
```

**2. Enable Required APIs**
```bash
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable aiplatform.googleapis.com
```

**3. Set Up Vertex AI Service Account**
- Create service account with "Vertex AI User" role
- Download JSON key as `gemini.json`
- See docs/llm_api_keys/vertex-ai-setup.md for details

**4. Build and Push Docker Image**
```bash
# Build from source
docker build -f docker/Dockerfile.prod -t denario .

# Tag for Artifact Registry
docker tag denario gcr.io/<project-id>/denario:latest

# Push
docker push gcr.io/<project-id>/denario:latest
```

**5. Deploy to Cloud Run**
```bash
gcloud run deploy denario \
  --image gcr.io/<project-id>/denario:latest \
  --platform managed \
  --region us-central1 \
  --port 8501 \
  --allow-unauthenticated \
  --set-env-vars "OPENAI_API_KEY=...,ANTHROPIC_API_KEY=..."
```

## Code Patterns and Conventions

### Denario API Usage
```python
from denario import Denario, Journal

# Initialize project
den = Denario(project_dir="project_dir")

# Set data description
den.set_data_description("Description of research data...")

# Generate research artifacts
den.get_idea()      # Research idea generation
den.get_method()    # Methodology development
den.get_results()   # Analysis execution
den.get_paper(journal=Journal.APS)  # Paper generation
```

### Agent Configuration (AG2)
```python
# For AG2 agents with Gemini via Vertex AI
den.get_results(
    engineer_model='gemini-2.5-pro',
    researcher_model='gemini-2.5-pro'
)
```

### Directory Structure
```
Denario/
├── src/denario/           # Core Denario library
├── docker/                # Dockerfiles
│   ├── Dockerfile.dev     # Development image
│   └── Dockerfile.prod    # Production image
├── docs/                  # Documentation
├── examples/              # Example projects
├── memory-bank/           # Project context (this folder)
├── construction/          # Development tracking
└── files/                 # Reference materials
```

## Common Issues and Debugging

### Issue: Vertex AI Authentication Fails
**Symptom:** "Could not authenticate" errors with Gemini
**Solution:**
- Verify `GOOGLE_APPLICATION_CREDENTIALS` points to valid JSON
- Check service account has "Vertex AI User" role
- Ensure billing is enabled on GCP project

### Issue: Docker Build Fails
**Symptom:** LaTeX package errors during build
**Solution:** Use the provided Dockerfile which includes all TeX dependencies

### Issue: Cloud Run Memory Limits
**Symptom:** Container crashes during LLM calls
**Solution:** Increase memory allocation: `--memory 2Gi` or higher

### Issue: API Rate Limits
**Symptom:** 429 errors from LLM providers
**Solution:** Implement retry logic, use multiple API keys, or batch requests

## Testing Strategy

### Local Testing
```bash
# Run Denario GUI
denario run

# Test with example project
python -c "from denario import Denario; d = Denario('examples/Project1')"
```

### Docker Testing
```bash
# Build and run locally
docker build -f docker/Dockerfile.dev -t denario_test .
docker run -p 8501:8501 --rm denario_test
```

### Integration Testing
- Test each agent type (idea, method, results, paper)
- Verify LLM provider connectivity
- Check knowledge graph operations (once implemented)

## Dependencies

### Python Libraries (from Denario)
- ag2 - Agent framework
- langgraph - Graph-based orchestration
- streamlit - GUI framework
- transformers - NLP models
- pandas, numpy - Data processing

### External Tools
- LaTeX (texlive) - For paper generation
- Docker - Container runtime
- gcloud CLI - GCP deployment

## Platform Notes

- **Primary OS**: Linux (Docker containers)
- **GUI Port**: 8501 (Streamlit)
- **API Keys**: Store securely, never commit to repository
- **Secrets Management**: Use GCP Secret Manager for production
