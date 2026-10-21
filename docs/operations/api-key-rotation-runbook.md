---
title: API key rotation (staging gated endpoints)
parent: Operations
nav_order: 7
---

# API key rotation runbook

**Projects:** `llm/features/r3-answer-with-provenance.md` (component C5, AC-5
and AC-6) and `llm/governance/adr/0007-machine-only-secrets.md`.

**Applies to:** the staging stack in GCP project `vt-gcp-00042`.

`GET /api/auth/check` is the first route gated by `require_api_key`. PR-5
mounts `/api/query` and `/api/provenance/*` behind the same dependency. Public
read endpoints (`/`, `/health`, `/api/problems`, …) are unchanged.

**The key is generated inside GCP and never seen.** No person, agent or GitHub
runner handles the value (ADR-0007 rule 2).

## Ownership split

| Piece | Owner | Mechanism |
|---|---|---|
| Secret Manager **container** `API_KEY` | Terraform | `infra/main.tf` |
| Public placeholder **seed** (version 1, `unset-seed-not-an-api-key`) | Terraform | `google_secret_manager_secret_version.api_key_seed`, `deletion_policy = ABANDON`, `ignore_changes = [secret_data, enabled]` |
| Generating and storing a new version | `agentic-kg-rotate-api-key-staging` Cloud Run Job as `api-key-rotator-staging` | `python -m agentic_kg.rotate_api_key_gcp` (REST Secret Manager, stdlib only) |
| Rolling the API onto it | `.github/workflows/rotate-api-key.yml` | label-stamped `API_KEY:latest` roll |
| Proving the new key works | `agentic-kg-check-api-key-staging` Job as the API's identity | reads `API_KEY` in-process, calls `GET /api/auth/check`, prints only pass/fail |
| Disabling superseded versions | the workflow | version **metadata** only, never a value |

## Fail-closed behaviour

The API returns **503 `auth not configured`** on gated routes while `API_KEY`
is empty or still the public Terraform placeholder. It never compares against
the placeholder as if it were a key. A missing or wrong key returns the same
**401** body — it does not say which was wrong.

Until the first rotation runs, `/api/auth/check` is therefore 503 even with a
header. That is deliberate: the seed is in the repository and is not a
credential.

## Rotation

The workflow is scheduled monthly (04:23 UTC on the 1st) and dispatchable:

```bash
gh workflow run rotate-api-key.yml --repo djjay0131/agentic-kg --ref master \
  -f reason="scheduled rotation"
gh run watch --repo djjay0131/agentic-kg
```

It pins both Jobs to the ingest Job's image, executes the rotation Job, rolls
the API service, executes the check Job, and **only then** disables superseded
`API_KEY` versions. It never calls `gcloud secrets versions access` or
`versions add` and prints no value.

**Verify by effect, never by value:**

```bash
gcloud run jobs executions list --job=agentic-kg-check-api-key-staging \
  --region=us-central1 --project=vt-gcp-00042 --limit=1
gcloud secrets versions list API_KEY --project=vt-gcp-00042 \
  --filter='state=ENABLED' --format='value(name.basename())'
```

The check execution must have succeeded; exactly one `API_KEY` version should
remain enabled.

## If a rotation fails

The workflow stops at the failing step and disables nothing. A re-run is safe:
the rotation Job adds another version, the roll resolves `:latest`, and the
check Job proves the effect. If the API still serves the old version, re-run the
workflow rather than reading the secret to compare.
