# Deployment Infrastructure Design

**Created:** 2026-01-07
**Status:** Draft
**Purpose:** Define deployment infrastructure as code for agentic-kg services

---

## 1. Overview

This document describes the Infrastructure as Code (IaC) for deploying agentic-kg services to Google Cloud Platform (GCP). The infrastructure supports:

- **Branch deployment on demand** - Manual trigger from any branch
- **Master deployment after merge** - Automatic deployment on PR merge
- **Service versioning** - Mix and match versions of different services
- **Multi-environment support** - Dev, staging, production

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DEPLOYMENT ARCHITECTURE                              │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    GitHub Actions Workflows                          │    │
│  │   ┌──────────────────┐         ┌──────────────────┐                 │    │
│  │   │ Branch Deploy    │         │ Master Deploy    │                 │    │
│  │   │ (manual trigger) │         │ (auto on merge)  │                 │    │
│  │   └────────┬─────────┘         └────────┬─────────┘                 │    │
│  └────────────┼────────────────────────────┼───────────────────────────┘    │
│               │                            │                                 │
│               ▼                            ▼                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    GCP Artifact Registry                             │    │
│  │   ┌─────────────────────────────────────────────────────────────┐   │    │
│  │   │  us-central1-docker.pkg.dev/PROJECT_ID/agentic-kg/          │   │    │
│  │   │                                                              │   │    │
│  │   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │    │
│  │   │  │ core:v1.0.0 │  │ api:v1.0.0  │  │ ui:v1.0.0           │  │   │    │
│  │   │  │ core:latest │  │ api:latest  │  │ ui:latest           │  │   │    │
│  │   │  │ core:<sha>  │  │ api:<sha>   │  │ ui:<sha>            │  │   │    │
│  │   │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│               │                            │                                 │
│               ▼                            ▼                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    GCP Cloud Run Services                            │    │
│  │                                                                      │    │
│  │   ┌─────────────────────────────────────────────────────────────┐   │    │
│  │   │  PRODUCTION (us-central1)                                   │   │    │
│  │   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │    │
│  │   │  │ agentic-kg   │  │ agentic-kg   │  │ agentic-kg       │  │   │    │
│  │   │  │ -api         │  │ -ui          │  │ -worker          │  │   │    │
│  │   │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  │                                                                      │    │
│  │   ┌─────────────────────────────────────────────────────────────┐   │    │
│  │   │  STAGING (us-central1)                                      │   │    │
│  │   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │    │
│  │   │  │ agentic-kg   │  │ agentic-kg   │  │ agentic-kg       │  │   │    │
│  │   │  │ -api-staging │  │ -ui-staging  │  │ -worker-staging  │  │   │    │
│  │   │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    External Services                                 │    │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │    │
│  │   │ Neo4j Aura      │  │ GCP Secret Mgr  │  │ OpenAI API          │ │    │
│  │   │ (managed)       │  │ (credentials)   │  │ (embeddings)        │ │    │
│  │   └─────────────────┘  └─────────────────┘  └─────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Service Components

### 3.1 Services Matrix

| Service | Package | Port | Purpose | Sprint |
|---------|---------|------|---------|--------|
| `agentic-kg-api` | `packages/api` | 8000 | FastAPI REST/GraphQL | Future |
| `agentic-kg-ui` | `packages/ui` | 8501 | Streamlit UI | Future |
| `agentic-kg-worker` | `packages/core` | N/A | Background jobs | Future |

### 3.2 Service Dependencies

```
agentic-kg-api ──► agentic-kg-core (library)
                   │
                   ├──► Neo4j Aura
                   ├──► OpenAI API
                   └──► GCP Secret Manager

agentic-kg-ui ──► agentic-kg-api (HTTP)
              └──► agentic-kg-core (library)

agentic-kg-worker ──► agentic-kg-core (library)
                      │
                      ├──► Neo4j Aura
                      └──► OpenAI API
```

---

## 4. Versioning Strategy

### 4.1 Image Tagging Convention

Each service image is tagged with multiple identifiers:

```
IMAGE_REPOSITORY/SERVICE:TAG

Tags:
- SHA tag:     service:abc1234      (git commit short SHA)
- Version tag: service:v1.2.3       (semantic version from git tag)
- Latest tag:  service:latest       (most recent build from master)
- Branch tag:  service:branch-name  (most recent build from branch)
```

### 4.2 Mix-and-Match Deployments

Environments can specify different versions per service:

```yaml
# production.yaml
services:
  api:
    image: agentic-kg-api:v2.0.0   # Stable API
  ui:
    image: agentic-kg-ui:v1.5.0    # Older UI (compatibility)
  worker:
    image: agentic-kg-worker:v2.1.0  # Latest worker

# staging.yaml
services:
  api:
    image: agentic-kg-api:abc1234  # Testing specific commit
  ui:
    image: agentic-kg-ui:feature-branch  # Testing branch
  worker:
    image: agentic-kg-worker:latest
```

### 4.3 Version Manifest

A `deployment-manifest.yaml` tracks deployed versions:

```yaml
environment: production
deployed_at: 2026-01-07T10:00:00Z
deployed_by: github-actions
services:
  api:
    image: us-central1-docker.pkg.dev/PROJECT/agentic-kg/api:v2.0.0
    sha: abc123def456
    deployed_at: 2026-01-07T10:00:00Z
  ui:
    image: us-central1-docker.pkg.dev/PROJECT/agentic-kg/ui:v1.5.0
    sha: def456abc123
    deployed_at: 2026-01-05T14:30:00Z
```

---

## 5. Deployment Workflows

### 5.1 Branch Deploy (Manual)

**Trigger:** `workflow_dispatch` with inputs
**Use case:** Testing feature branches, PR validation

```
Developer ─► GitHub Actions UI ─► Select branch ─► Select service(s)
                                        │
                                        ▼
                             Build & Deploy to staging
                                        │
                                        ▼
                              Return staging URL
```

**Inputs:**
- `branch`: Branch to deploy (default: current)
- `services`: Which services to deploy (all, api, ui, worker)
- `environment`: Target environment (staging, dev)

### 5.2 Master Deploy (Automatic)

**Trigger:** Push to `master` (after PR merge)
**Use case:** Production releases

```
PR Merge to master ─► Tests pass ─► Build all changed services
                                              │
                                              ▼
                                    Push to Artifact Registry
                                              │
                                              ▼
                                    Deploy to staging
                                              │
                                              ▼
                                    Run integration tests
                                              │
                                              ▼
                                    (Optional) Deploy to production
```

### 5.3 Tag Deploy (Release)

**Trigger:** Git tag push (`v*.*.*`)
**Use case:** Versioned releases

```
Create tag v1.2.3 ─► Build with version tag ─► Push images
                                                    │
                                                    ▼
                                          Tag as v1.2.3 + latest
                                                    │
                                                    ▼
                                          Create GitHub Release
```

---

## 6. Infrastructure Components

### 6.1 Dockerfiles

```
docker/
├── Dockerfile.api        # FastAPI service
├── Dockerfile.ui         # Streamlit service
├── Dockerfile.worker     # Background worker
└── Dockerfile.base       # Shared base image with dependencies
```

### 6.2 GitHub Actions Workflows

```
.github/workflows/
├── deploy-branch.yml     # Manual branch deployment
├── deploy-master.yml     # Auto-deploy on master merge
├── deploy-tag.yml        # Version release deployment
└── build-images.yml      # Reusable image building workflow
```

### 6.3 Deployment Configurations

```
deploy/
├── environments/
│   ├── dev.yaml          # Development environment config
│   ├── staging.yaml      # Staging environment config
│   └── production.yaml   # Production environment config
├── services/
│   ├── api.yaml          # API service Cloud Run config
│   ├── ui.yaml           # UI service Cloud Run config
│   └── worker.yaml       # Worker service Cloud Run config
└── terraform/            # (Future) Terraform for infra provisioning
    ├── main.tf
    ├── variables.tf
    └── outputs.tf
```

---

## 7. GCP Resources

### 7.1 Required GCP Services

| Service | Purpose | Setup |
|---------|---------|-------|
| Cloud Run | Container hosting | Per-service |
| Artifact Registry | Docker image storage | Single registry |
| Secret Manager | API keys, credentials | Project-level |
| Cloud Build | CI/CD (optional) | Triggered by GH Actions |
| IAM | Service accounts | Per-environment |

### 7.2 Service Accounts

```
Service Account: agentic-kg-deployer@PROJECT.iam.gserviceaccount.com
Roles:
- roles/run.admin
- roles/artifactregistry.writer
- roles/secretmanager.secretAccessor
- roles/iam.serviceAccountUser

Service Account: agentic-kg-runtime@PROJECT.iam.gserviceaccount.com
Roles:
- roles/secretmanager.secretAccessor
- roles/logging.logWriter
- roles/monitoring.metricWriter
```

### 7.3 Secret Manager Secrets

| Secret Name | Description | Used By |
|-------------|-------------|---------|
| `NEO4J_URI` | Neo4j Aura connection URI | api, worker |
| `NEO4J_PASSWORD` | Neo4j authentication | api, worker |
| `OPENAI_API_KEY` | OpenAI embeddings API | api, worker |
| `ANTHROPIC_API_KEY` | Claude API (agents) | api, worker |

---

## 8. Security Considerations

### 8.1 Image Scanning

All images are scanned for vulnerabilities before deployment:

```yaml
- name: Scan image for vulnerabilities
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: ${{ env.IMAGE }}
    exit-code: '1'
    severity: 'CRITICAL,HIGH'
```

### 8.2 Least Privilege

- Runtime service accounts have minimal permissions
- Deployer accounts are separate from runtime accounts
- Secrets are accessed at runtime, not build time

### 8.3 Network Security

- Cloud Run services are not publicly accessible by default
- Inter-service communication via internal URLs
- External access through Cloud Load Balancer (future)

---

## 9. Monitoring & Observability

### 9.1 Deployment Tracking

Each deployment records:
- Git SHA and branch/tag
- Timestamp
- Deployer (user or automation)
- Environment
- Success/failure status

### 9.2 Health Checks

```yaml
# Cloud Run health check configuration
healthCheck:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 30
  failureThreshold: 3
```

### 9.3 Rollback Strategy

```bash
# Quick rollback to previous version
gcloud run services update-traffic SERVICE \
  --to-revisions=PREVIOUS_REVISION=100 \
  --region=us-central1

# Or deploy specific version
gh workflow run deploy-branch.yml \
  -f services=api \
  -f image_tag=v1.1.0 \
  -f environment=production
```

---

## 10. Local Development

### 10.1 Docker Compose

For local development with all services:

```bash
# Start all services
docker compose -f docker/docker-compose.yml up -d

# Start specific services
docker compose -f docker/docker-compose.yml up neo4j api
```

### 10.2 Environment Variables

```bash
# .env.local (not committed)
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=dev-password
OPENAI_API_KEY=sk-...
```

---

## 11. Implementation Checklist

### Phase 1: Infrastructure Setup (Current)
- [ ] Create Dockerfile templates for each service
- [ ] Create GitHub Actions workflow for branch deployment
- [ ] Create GitHub Actions workflow for master deployment
- [ ] Set up Artifact Registry repository
- [ ] Configure service accounts and IAM

### Phase 2: Service Deployment (When API is Ready)
- [ ] Implement API service Dockerfile
- [ ] Deploy API to staging
- [ ] Set up health checks
- [ ] Configure secrets

### Phase 3: Full Stack (When UI is Ready)
- [ ] Implement UI service Dockerfile
- [ ] Set up inter-service networking
- [ ] Configure production deployment

### Phase 4: Automation (Future)
- [ ] Terraform for infrastructure provisioning
- [ ] Automated rollback on failure
- [ ] Blue-green deployments

---

## 12. References

- [System Architecture](./system-architecture.md)
- [GCP Cloud Run Documentation](https://cloud.google.com/run/docs)
- [GitHub Actions for GCP](https://github.com/google-github-actions)
