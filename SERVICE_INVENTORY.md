# Service Inventory

**Last Updated:** 2026-02-10

This document provides a comprehensive inventory of all deployed services across the agentic-kg and Denario projects.

## Quick Reference

| Service | Type | Status | Primary URL |
|---------|------|--------|-------------|
| agentic-kg-api-staging | FastAPI | ✅ Healthy | <https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app> |
| agentic-kg-ui-staging | Next.js | ⚠️ Fixing | <https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app> |
| denario | Streamlit | ⚠️ Legacy | <https://denario-tqpsba7pza-uc.a.run.app> |
| denario-app | Streamlit | ✅ Active | <https://denario-app-tqpsba7pza-uc.a.run.app> |

## Agentic-KG Services

### agentic-kg-api-staging

**Type:** FastAPI Backend
**Repository:** [djjay0131/agentic-kg](https://github.com/djjay0131/agentic-kg)
**Status:** ✅ Healthy

**URLs:**

- Primary: <https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app>
- Legacy: <https://agentic-kg-api-staging-542888988741.us-central1.run.app>

**Purpose:** REST API backend for the research problem knowledge graph system.

**Features:**

- Problem CRUD operations
- Paper management
- Hybrid semantic search
- LLM-based extraction pipeline
- Agent workflow orchestration

**Health Check:**

```bash
curl https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/health
# {"status":"ok","version":"0.1.0","neo4j_connected":true}
```

**API Documentation:**

- OpenAPI: `/docs`
- ReDoc: `/redoc`

**Environment Variables:**

- `NEO4J_URI` - Neo4j connection string (from Secret Manager)
- `NEO4J_PASSWORD` - Neo4j password (from Secret Manager)
- `OPENAI_API_KEY` - OpenAI API key (from Secret Manager)
- `ANTHROPIC_API_KEY` - Anthropic API key (from Secret Manager)

**Dependencies:**

- Neo4j: bolt://34.173.74.125:7687
- GCP Secret Manager

---

### agentic-kg-ui-staging

**Type:** Next.js Frontend
**Repository:** [djjay0131/agentic-kg](https://github.com/djjay0131/agentic-kg)
**Status:** ⚠️ API URL configuration fix in progress

**URLs:**

- Primary: <https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app>
- Legacy: <https://agentic-kg-ui-staging-542888988741.us-central1.run.app>

**Purpose:** Web dashboard for exploring the knowledge graph and managing research problems.

**Features:**

- Dashboard with system stats
- Problem browser and detail views
- Paper listing
- Semantic search interface
- Graph visualization (react-force-graph)
- Extraction interface
- Agent workflow UI

**Pages:**

- `/` - Dashboard
- `/problems` - Problem list
- `/problems/[id]` - Problem details
- `/papers` - Paper list
- `/graph` - Graph visualization
- `/extract` - Extraction interface
- `/workflows` - Agent workflows
- `/workflows/[id]` - Workflow detail

**Build-Time Environment:**

- `NEXT_PUBLIC_API_URL` - API endpoint (required at build time)

**Dependencies:**

- agentic-kg-api-staging

---

## Denario Services

### denario (Legacy)

**Type:** Streamlit App
**Repository:** [djjay0131/Denario](https://github.com/djjay0131/Denario)
**Status:** ⚠️ Legacy - Consider deprecating

**URLs:**

- Primary: <https://denario-tqpsba7pza-uc.a.run.app>
- Legacy: <https://denario-542888988741.us-central1.run.app>

**Purpose:** Original Denario Streamlit interface (older version).

**Version:** Commit 5d5713e
**Dockerfile:** `docker/Dockerfile.prod`
**Install:** `pip install "denario[app]"`

**Environment Variables:**

- `OPENAI_API_KEY` - OpenAI API key (from Secret Manager)
- `GOOGLE_API_KEY` - Google/Gemini API key (from Secret Manager)
- `ANTHROPIC_API_KEY` - Anthropic API key (from Secret Manager)
- `PERPLEXITY_API_KEY` - Perplexity API key (from Secret Manager)

**Issues:**

- Older version without recent bug fixes
- No file upload restrictions
- Missing Vertex AI improvements

---

### denario-app (Active)

**Type:** Streamlit App
**Repository:** [djjay0131/DenarioApp](https://github.com/djjay0131/DenarioApp)
**Status:** ✅ Active with improvements

**URLs:**

- Primary: <https://denario-app-tqpsba7pza-uc.a.run.app>
- Legacy: <https://denario-app-542888988741.us-central1.run.app>

**Purpose:** Enhanced Denario Streamlit interface with production improvements.

**Version:** Commit b5e1270
**Dockerfile:** `Dockerfile` (root)
**Install:** `pip install denario_app`

**Improvements over legacy denario:**

1. File upload restrictions (.md and .txt only)
2. Better error handling (UnicodeDecodeError)
3. Vertex AI integration (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`)
4. Docker permission fixes
5. Production warnings

**Environment Variables:**

- `OPENAI_API_KEY` - OpenAI API key (from Secret Manager)
- `GOOGLE_API_KEY` - Google/Gemini API key (from Secret Manager)
- `ANTHROPIC_API_KEY` - Anthropic API key (from Secret Manager)
- `PERPLEXITY_API_KEY` - Perplexity API key (from Secret Manager)
- `GOOGLE_CLOUD_PROJECT` - GCP project ID (vt-gcp-00042)
- `GOOGLE_CLOUD_LOCATION` - GCP region (us-central1)

**Recent Commits:**

- `b5e1270` - Add GOOGLE_CLOUD_PROJECT env vars for Vertex AI
- `b9f6b4b` - Restrict file uploads to markdown/text files only
- `289b1ef` - Add PDF text extraction support using PyMuPDF
- `06d6def` - Fix Docker build permission issue
- `a6e2c70` - Add Cloud Build configuration for GCP deployment

---

## Infrastructure

### Neo4j Database

**Type:** Neo4j Graph Database
**Status:** ✅ Running

**Access:**

- Bolt: bolt://34.173.74.125:7687
- Browser: <http://34.173.74.125:7474>

**Purpose:** Graph database for storing research problems, papers, and relationships.

**Credentials:**

- Username: `neo4j`
- Password: Stored in GCP Secret Manager (`NEO4J_PASSWORD`)

**Terraform:**

- Configuration: `infra/terraform/`
- Get password: `cd infra && terraform output -raw neo4j_password`

---

### GCP Secret Manager

**Secrets:**

- `OPENAI_API_KEY` - 164 bytes
- `ANTHROPIC_API_KEY` - 108 bytes
- `GOOGLE_API_KEY` - 39 bytes
- `PERPLEXITY_API_KEY` - 53 bytes
- `NEO4J_URI` - Connection string
- `NEO4J_PASSWORD` - Database password

**Access:**

```bash
# List secrets
gcloud secrets list

# View secret value
gcloud secrets versions access latest --secret="OPENAI_API_KEY"
```

---

## URL Formats

Cloud Run provides two URL formats for each service:

1. **Legacy Format (Project Number):**
   - Pattern: `https://{service}-{project-number}.{region}.run.app`
   - Example: `https://denario-542888988741.us-central1.run.app`
   - Shown by: `gcloud run services list`

2. **New Format (Random String):**
   - Pattern: `https://{service}-{random}.{region-code}.a.run.app`
   - Example: `https://denario-tqpsba7pza-uc.a.run.app`
   - Shown by: `gcloud run services describe`

Both URLs point to the same service and are fully functional.

---

## Repository Links

- **agentic-kg:** <https://github.com/djjay0131/agentic-kg>
- **Denario:** <https://github.com/djjay0131/Denario> (fork)
- **DenarioApp:** <https://github.com/djjay0131/DenarioApp> (fork)
- **Upstream Denario:** <https://github.com/AstroPilot-AI/Denario>
- **Upstream DenarioApp:** <https://github.com/AstroPilot-AI/DenarioApp>

---

## Recommendations

### Immediate Actions

1. **Fix agentic-kg-ui deployment** - Rebuild with NEXT_PUBLIC_API_URL (in progress)
2. **Document denario vs denario-app** - Clarify which to use
3. **Consider deprecating legacy denario service** - Consolidate to denario-app

### Future Improvements

1. Set up custom domains for cleaner URLs
2. Implement monitoring and alerting
3. Create automated health check dashboard
4. Document API endpoints with OpenAPI
5. Set up automated deployment pipelines

---

## Deployment Commands

### List All Services

```bash
gcloud run services list --region=us-central1
```

### Get Service Details

```bash
gcloud run services describe {service-name} --region=us-central1
```

### Check Service Health

```bash
# agentic-kg API
curl https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/health

# UI (check HTTP status)
curl -I https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app
```

### View Logs

```bash
gcloud run services logs read {service-name} --region=us-central1 --limit=50
```

---

## Support

For issues or questions:

- Check service logs: `gcloud run services logs read`
- Review deployment docs: `construction/sprints/`
- Contact: djjay0131@gmail.com
