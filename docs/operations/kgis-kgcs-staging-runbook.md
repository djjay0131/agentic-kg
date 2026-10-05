---
title: Deploy and run the KGIS/KGCS staging path
parent: Operations
nav_order: 3
---

# KGIS/KGCS staging runbook

**Applies to:** the non-production staging stack in GCP project `vt-gcp-00042`,
for the adoption plan's Phase 8 first slice — making the
`KGIS -> KGCS -> canonical graph` path runnable and observable end to end.

Nothing in this runbook is run by the agent that wrote it. It is the operator's
procedure; the orchestrator deploys after merge.

Isolation is decided in **ADR-0005**: the canonical graph shares the one
staging Community Neo4j database but lives under the `Canon__*` labels, scoped to
a dedicated namespace. Nothing here can reach the legacy `Paper` / `Topic` /
`RESEARCHES` graph.

---

## 1. Deploy the integration branch to staging

```bash
gh workflow run deploy-branch.yml \
  --ref integration/kgis-kgcs-adoption \
  -f services=all \
  -f environment=staging
```

This builds and deploys `agentic-kg-api-staging`, `agentic-kg-ui-staging` and the
`agentic-kg-ingest-staging` Cloud Run Job. Both images now install the opt-in
`migration` extra (`docker/Dockerfile.api`, `docker/Dockerfile.job`); the code
path is still opt-in at the flag level, so the default `INGEST_MODE=legacy`
behaviour is unchanged.

The workflow does **not** set the migration flags. Enable them explicitly on the
two resources (these are `gcloud` mutations — the orchestrator runs them, not the
implementation agent):

```bash
# Job: opt in to the migration path and keep its ledger job-local.
gcloud run jobs update agentic-kg-ingest-staging \
  --region=us-central1 \
  --update-env-vars=INGEST_MODE=kgis_kgcs,KGIS_INGESTION_ENABLED=1,KGCS_RESOLUTION_ENABLED=1,KGCS_CANONICAL_NAMESPACE=staging,INGEST_LEDGER_DIR=/tmp/kgis-shadow

# API: expose the read-only canonical projection.
gcloud run services update agentic-kg-api-staging \
  --region=us-central1 \
  --update-env-vars=CANONICAL_API_ENABLED=1,KGCS_CANONICAL_NAMESPACE=staging
```

`KGCS_CANONICAL_NAMESPACE` must be the **same value** on both resources; it is
the isolation boundary.

## 2. Execute the Job in `kgis_kgcs` mode

Leave `INGEST_DOIS` unset to run the whole frozen eight-paper corpus (the corpus
is the eight committed ground-truth papers; there is no live PDF acquisition in
this slice):

```bash
gcloud run jobs execute agentic-kg-ingest-staging \
  --region=us-central1 \
  --wait
```

To run a subset, pass comma-separated DOIs or slugs as an execution override
(unknown values are refused, not silently ignored):

```bash
gcloud run jobs execute agentic-kg-ingest-staging \
  --region=us-central1 \
  --update-env-vars=INGEST_DOIS=10.1007/978-3-031-19433-7_39,10.1038/s41597-025-05200-8 \
  --wait
```

The Job prints a JSON summary to stdout (Cloud Logging). Read it with:

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="agentic-kg-ingest-staging"' \
  --limit=50 --format='value(textPayload)'
```

The summary's fields:

| Field | Meaning |
|---|---|
| `candidates_by_kind` | KGIS candidates submitted, by kind |
| `routes` | `AUTO` / `LLM_ASSESS` / `HUMAN` over validated candidates |
| `committed_operations` | KGCS operations the executor actually applied |
| `deferred_candidates` / `deferral_reasons` | validated-but-not-committed, classified |
| `epoch` | latest published canonical epoch |
| `committed` | whether **this** run advanced the epoch |
| `honest_nulls` | the stages that are deliberately not wired |

### Idempotency check (required)

Run the Job a second time with the same flags. The expected result is
`"committed": false` with the same `epoch` and no change to the entity count —
the deterministic candidate ids re-plan the same operations and the store
refuses them rather than duplicating canonical state. A second run that reports
`"committed": true` with `CREATE_IDENTITY` operations is a defect: stop and
report it.

## 3. Query the read-only canonical API

```bash
API_URL=$(gcloud run services describe agentic-kg-api-staging \
  --region=us-central1 --format='value(status.url)')

curl -s "$API_URL/api/canonical/summary"
curl -s "$API_URL/api/canonical/entities?type=Paper&q=cskg"
curl -s "$API_URL/api/canonical/entities/<identity_id>"   # assertions + evidence_refs
```

All three are `GET`-only. With `CANONICAL_API_ENABLED` unset they return 404;
with it set but the `migration` extra missing from the image they return 503.
The endpoints read the canonical graph at the latest epoch through a reader that
is structurally not a `GraphMutationStore` — there is no canonical write surface
on this path.

## 4. What the first slice does and does not do

**Does:** acquire from the committed corpus, run KGIS shadow ingestion, run KGCS
curation, commit DOI-keyed `Paper` identities into the isolated canonical
namespace, publish an epoch, and report all of it as JSON.

**Does not (honest nulls, carried in the summary):**

- **Entity resolution is not wired.** Every assertion whose subject is still an
  alias is *deferred*, not committed — so the canonical graph holds identities
  but no attribute assertions yet. The deferred count and reasons are in the
  summary.
- **The LLM adviser and human review queue are not wired**, so `LLM_ASSESS` and
  `HUMAN` routes are deferred by name rather than adjudicated.
- **The replay client reproduces the committed importer output, not a live
  model.** `extraction_quality` is `not_measured` in the summary for exactly
  this reason.
- **The KGIS ledger/evidence is job-local SQLite** (`/tmp/kgis-shadow`, or
  in-memory if unset) and is lost when the Job's instance scales in. The durable,
  observable artifact is the canonical epoch in Neo4j. A durable ledger backend
  is deferred (ADR-0003 / ADR-0012).

## 5. Rollback

The legacy path is untouched. To return the stack to it:

```bash
gcloud run jobs update agentic-kg-ingest-staging \
  --region=us-central1 --remove-env-vars=INGEST_MODE
gcloud run services update agentic-kg-api-staging \
  --region=us-central1 --remove-env-vars=CANONICAL_API_ENABLED
```

The canonical labels and namespace can be left in place; they are invisible to
every legacy read path.

## Related

- `llm/governance/adr/0005-kgis-kgcs-staging-isolation.md` — the isolation decision
- `llm/governance/adr/0004-kgis-kgcs-adoption-opt-in-seam.md` — the opt-in flags
- `packages/core/src/agentic_kg/migration/run.py` — the run orchestration
- `packages/core/src/agentic_kg/migration/canonical_read.py` — the read facade
- `packages/api/src/agentic_kg_api/routers/canonical.py` — the endpoints
