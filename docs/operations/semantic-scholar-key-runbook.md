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
| The **enabled value** | the owner, directly in Secret Manager (ADR-0007) | one new version per key the vendor issues |
| Rolling consumers onto it | `.github/workflows/roll-vendor-keys.yml` | label-stamped `:latest` roll, then disable superseded (never reads the value) |
| CI access | `gh-ci-vendor-keys-staging` via WIF | `get-secretmanager-secrets` in `smoke-ingest.yml` / `integration-tests.yml` |

**ADR-0007:** Secret Manager is the key's only home. There is no GitHub-secret
copy and no workflow that copies the value; no agent ever handles it.

Terraform never writes the real key, so no credential enters state. The seed
placeholder (`unset-seed-not-an-api-key`) exists only so Cloud Run can create
the Job while the secret has no real version; the S2 client treats that exact
value as "no key" and sends no `x-api-key` header
(`packages/core/src/agentic_kg/data_acquisition/config.py`).

---

## Preconditions

- The GitHub environment `staging` has `GCP_WORKLOAD_IDENTITY_PROVIDER` and
  `GCP_SERVICE_ACCOUNT` set, exactly as `deploy-master.yml` uses them.
- The Terraform changes that create the container, the seed version, the Job
  env and the `gh-ci-vendor-keys-staging` reader have been applied (dispatch
  the `Terraform` workflow; applying is the owner's approval).

---

## 1. Add the key (owner, once per key the vendor issues)

Semantic Scholar issues the key, so no workflow can generate it. Add it
**directly in Secret Manager** — the GCP console (*Security → Secret Manager →
SEMANTIC_SCHOLAR_API_KEY → New version*), or from your own terminal with the
value read from stdin so it never lands in shell history or argv:

```bash
gcloud secrets versions add SEMANTIC_SCHOLAR_API_KEY \
  --project=vt-gcp-00042 --data-file=-   # paste, then Ctrl-D
```

Never paste the key into a chat, an issue, a PR, a GitHub secret or an agent
session.

Then roll the consumers onto it — this workflow never reads the value:

```bash
gh workflow run roll-vendor-keys.yml --repo djjay0131/agentic-kg --ref master \
  -f secret=SEMANTIC_SCHOLAR_API_KEY -f reason="new S2 key"
gh run watch --repo djjay0131/agentic-kg
```

It refuses to roll if the newest enabled version is still the Terraform seed,
rolls `agentic-kg-ingest-staging` onto `latest` with a label stamp, and only
then disables the superseded versions. CI picks the key up automatically on
its next run (it reads `latest` through the reader identity).

**Verify** — exactly one enabled version, and the Job points at `latest`:

```bash
gcloud secrets versions list SEMANTIC_SCHOLAR_API_KEY \
  --project=vt-gcp-00042 --format='table(name,state,createTime)'
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

Repeat §1 whenever the S2 key changes (new key, suspected leak): add the new
version in Secret Manager, then run `roll-vendor-keys.yml`. There is no
scheduled rotation — S2 issues keys by request and they do not expire on a
fixed cadence, so automation cannot mint one (ADR-0007, vendor-issued keys).

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
- [`ADR-0007`](https://github.com/djjay0131/agentic-kg/blob/master/llm/governance/adr/0007-machine-only-secrets.md) — secrets are machine-generated and never seen; vendor keys live only in Secret Manager
- `.github/workflows/roll-vendor-keys.yml` — rolls consumers onto a new version without reading it
- `packages/core/src/agentic_kg/data_acquisition/config.py` — placeholder handling
