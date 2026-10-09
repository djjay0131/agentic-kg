---
title: Semantic Scholar API key (staging ingest Job)
parent: Operations
nav_order: 4
---

# Semantic Scholar API key runbook

**Applies to:** the staging stack in GCP project `vt-gcp-00042`. The
`agentic-kg-ingest-staging` Cloud Run Job reads
`SEMANTIC_SCHOLAR_API_KEY` from Secret Manager so live Semantic Scholar (S2)
calls are authenticated. Before this, the Job drew on the shared anonymous
rate-limit pool and its citation lookups failed under 429s (nightly-pipeline
P0-4).

**Ownership split** — the same shape ADR-0006 uses for the Neo4j password:

| Piece | Owner | Mechanism |
|---|---|---|
| Secret Manager **container** `SEMANTIC_SCHOLAR_API_KEY` | Terraform | `infra/main.tf` |
| Placeholder **seed version** (version 1) | Terraform | `google_secret_manager_secret_version.semantic_scholar_api_key_seed`, `deletion_policy = ABANDON`, `ignore_changes = [secret_data, enabled]` |
| The **enabled value** | `.github/workflows/sync-s2-key.yml` | `gcloud secrets versions add`, then disable superseded |
| **Source of truth** for the value | the repo GitHub Actions secret `SEMANTIC_SCHOLAR_API_KEY` | |

Terraform never writes the real key, so no credential enters state. The seed
placeholder (`unset-seed-not-an-api-key`) exists only so Cloud Run can create
the Job while the secret has no real version; the S2 client treats that exact
value as "no key" and sends no `x-api-key` header
(`packages/core/src/agentic_kg/data_acquisition/config.py`).

---

## Preconditions

- The GitHub environment `staging` has `GCP_WORKLOAD_IDENTITY_PROVIDER` and
  `GCP_SERVICE_ACCOUNT` set, exactly as `deploy-master.yml` uses them.
- The repo GitHub Actions secret `SEMANTIC_SCHOLAR_API_KEY` holds a real key
  (`gh secret list --repo djjay0131/agentic-kg`). This is the same secret the
  smoke and integration workflows use.
- The Terraform change that creates the container, the seed version and the
  Job env has been applied (**dispatch the `Terraform` workflow**, not a local
  apply — `infra/README.md` §The normal flow). Applying is the owner's action,
  never an agent session.

Capture the current state first:

```bash
gcloud secrets versions list SEMANTIC_SCHOLAR_API_KEY \
  --project=vt-gcp-00042 --format='table(name,state,createTime)'
```

---

## 1. Push the key into Secret Manager

Dispatch the workflow by hand; it never runs on push, PR or schedule:

```bash
gh workflow run sync-s2-key.yml \
  --repo djjay0131/agentic-kg \
  -f reason="initial staging key" \
  --ref master
gh run watch --repo djjay0131/agentic-kg
```

What it does, in order:

1. reads the GitHub secret into the step's shell environment (never argv);
2. `::add-mask::`s it before it can reach a log;
3. streams it from stdin into a **new** Secret Manager version
   (`printf '%s' "$S2_API_KEY" | gcloud secrets versions add ... --data-file=-`);
4. rolls `agentic-kg-ingest-staging` onto `latest` and stamps the
   `s2-key-synced` label, forcing a fresh revision that resolves the new
   version;
5. **only then** disables the superseded versions (including the Terraform
   seed).

The key is never printed, never written to a file, and never placed in
`GITHUB_OUTPUT` / `GITHUB_ENV` / the step summary.

**Verify** independently — exactly one enabled version, and the Job points at
`latest`:

```bash
gcloud secrets versions list SEMANTIC_SCHOLAR_API_KEY \
  --project=vt-gcp-00042 --format='table(name,state,createTime)'

gcloud run jobs describe agentic-kg-ingest-staging \
  --region=us-central1 --project=vt-gcp-00042 \
  --format='value(spec.template.spec.template.spec.containers[0].env)'
```

---

## 2. Run the Job and confirm S2 is authenticated

```bash
gcloud run jobs execute agentic-kg-ingest-staging \
  --region=us-central1 --project=vt-gcp-00042 --wait
```

The run's log stream should no longer show the `[semantic_scholar] Rate limit
exceeded` search error, and a citation-populated paper should land `CITES`
edges. Read the summary with:

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="agentic-kg-ingest-staging"' \
  --limit=50 --format='value(textPayload)'
```

---

## 3. Rotating or replacing the key

Re-run the same workflow whenever the S2 key changes (new key, suspected
leak). It mints a new version and disables the old one; nothing else changes.
There is no scheduled rotation — S2 keys do not expire on a fixed cadence.

---

## Rollback

The previous version is disabled, not destroyed. To go back:

```bash
# Re-enable the version you want (find its number in the versions list).
gcloud secrets versions enable <version> \
  --secret=SEMANTIC_SCHOLAR_API_KEY --project=vt-gcp-00042

# Point the Job back at it and force a revision.
gcloud run jobs update agentic-kg-ingest-staging \
  --region=us-central1 --project=vt-gcp-00042 \
  --update-secrets=SEMANTIC_SCHOLAR_API_KEY=SEMANTIC_SCHOLAR_API_KEY:<version> \
  --update-labels=s2-key-rolled-back=$(date +%s)
```

To run the Job **without** a key (anonymous pool) deliberately, disable every
version and re-run; the client treats an absent value as "no key". Do not
point the Job back at the Terraform seed version in normal operation — it is
a placeholder.

---

## Related

- [`ADR-0006`](https://github.com/djjay0131/agentic-kg/blob/master/llm/governance/adr/0006-staging-neo4j-private.md) — the credential-ownership split this mirrors
- `infra/main.tf` — the container, seed version and Job env
- `.github/workflows/sync-s2-key.yml` — the value owner
- `packages/core/src/agentic_kg/data_acquisition/config.py` — placeholder handling
