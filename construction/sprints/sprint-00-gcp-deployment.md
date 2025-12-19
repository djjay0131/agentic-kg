# Sprint 00: GCP Deployment

**Sprint Goal:** Deploy Denario to GCP Cloud Run with full CI/CD pipeline

**Start Date:** 2025-12-18
**Status:** In Progress

---

## Completed Tasks

### Task 1: GCP Project Setup ✅
- [x] Selected existing project: `vt-gcp-00042` (Agents4Research)
- [x] Verified billing is enabled
- [x] Project ID: `vt-gcp-00042`

### Task 2: Enable Required APIs ✅
```bash
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable aiplatform.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

### Task 3: Artifact Registry Setup ✅
```bash
gcloud artifacts repositories create denario \
  --repository-format=docker \
  --location=us-central1 \
  --description="Denario Docker images"
```
- Repository: `us-central1-docker.pkg.dev/vt-gcp-00042/denario`

### Task 4: Secret Manager Setup ✅
Created secrets for all LLM API keys:
- [x] `OPENAI_API_KEY`
- [x] `GOOGLE_API_KEY`
- [x] `ANTHROPIC_API_KEY`
- [x] `PERPLEXITY_API_KEY`

Granted Cloud Run service account access to all secrets.

### Task 5: Cloud Build Configuration ✅
- [x] Created `cloudbuild.yaml` with full CI/CD pipeline
- [x] Created `.gcloudignore` to optimize build context
- Pipeline: Build → Tag → Push → Deploy to Cloud Run

---

## In Progress Tasks

### Task 6: GitHub Integration 🔄
- [x] Created Cloud Build connection `denario-github`
- [ ] Complete OAuth authorization (user action required)
- [ ] Link repository `djjay0131/Denario`
- [ ] Create production trigger (master branch)
- [ ] Create development trigger (dev/* branches)

**OAuth Link:**
```
https://console.cloud.google.com/cloud-build/triggers/connect
```

---

## Pending Tasks

### Task 7: Verification
- [ ] Trigger build by pushing to dev branch
- [ ] Verify Cloud Run deployment succeeds
- [ ] Access Cloud Run URL in browser
- [ ] Verify Streamlit GUI loads
- [ ] Test LLM connectivity

---

## Architecture Decisions

- **ADR-004**: GCP Cloud Run for Initial Deployment
- **ADR-008**: Use GCP Cloud Build for Container Images
- **ADR-009**: GitHub-Triggered CI/CD Pipeline

See [architecturalDecisions.md](../../memory-bank/architecturalDecisions.md) for details.

---

## Infrastructure Summary

| Component | Value |
|-----------|-------|
| GCP Project | `vt-gcp-00042` |
| Region | `us-central1` |
| Artifact Registry | `us-central1-docker.pkg.dev/vt-gcp-00042/denario` |
| Cloud Run Service | `denario` |
| Port | `8501` |
| Memory | `2Gi` |

---

## Dependencies

- GCP account with billing enabled ✅
- LLM API keys (OpenAI, Google, Anthropic, Perplexity) ✅
- gcloud CLI installed and authenticated ✅
- GitHub OAuth authorization (pending)

---

## Risks and Blockers

| Risk | Mitigation | Status |
|------|------------|--------|
| GCP billing not set up | Billing enabled | ✅ Resolved |
| API keys not available | Stored in Secret Manager | ✅ Resolved |
| Docker build timeout | 30-minute timeout set | ✅ Configured |
| GitHub OAuth not completed | User must authorize | 🔄 Pending |

---

## Acceptance Criteria

1. [ ] GitHub triggers fire on push
2. [ ] Cloud Build completes successfully
3. [ ] Cloud Run deployment succeeds
4. [ ] Denario GUI accessible via Cloud Run URL
5. [ ] LLM calls succeed (at least one provider)
6. [ ] No errors in Cloud Run logs

---

## Notes

- Build time: ~20-30 minutes (TeX Live installation)
- See [techContext.md](../../memory-bank/techContext.md) for detailed context
- Secrets managed via GCP Secret Manager (not environment variables)
