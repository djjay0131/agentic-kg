# Infrastructure as Code (Terraform)

Terraform configurations for provisioning and managing GCP infrastructure for
Agentic KG. The only allowed GCP project is the VT non-prod project
`vt-gcp-00042`; all applies flow through GitHub Actions (or a deliberate
operator session) — never from an agent.

## Directory structure

```
infra/
├── backend.tf           # GCS remote-state backend (bucket, no prefix)
├── main.tf              # Root module — composes all resources
├── variables.tf         # Input variables
├── outputs.tf           # Output values (URLs, IPs)
├── providers.tf         # Provider + required versions
├── imports.tf           # ONE-TIME staging adoption import blocks
├── envs/
│   ├── staging.tfvars   # Staging variable values
│   └── prod.tfvars      # Production variable values
└── deploy.sh            # Manual image build/push helper (out of the TF path)
```

## State

State lives in **GCP**, not on a laptop:

- Bucket: `gs://vt-gcp-00042-tfstate` (us-central1, versioned,
  public-access-prevention).
- The bucket is shared by environments; each environment gets a **prefix**, and
  the prefix is passed at init time (never hard-coded in `backend.tf`):

```bash
cd infra
terraform init -backend-config="prefix=agentic-kg/staging"
# prod: -backend-config="prefix=agentic-kg/prod"
```

Local state is gitignored (`.gitignore`); do not commit `*.tfstate`.

## The normal flow

Work does not run `terraform apply` by hand. The flow is:

1. **Merge a PR that touches `infra/`.** On the PR,
   `.github/workflows/terraform.yml` runs `terraform fmt -check`, `init`
   (GCS backend, read-only), `validate`, and
   `terraform plan -var-file=envs/staging.tfvars -lock=false`, then posts the
   plan summary (imports + actions only — no attribute values) to the job
   summary.
2. **Dispatch the apply** from Actions → *Terraform* → *Run workflow* with
   `apply: true`. The apply job is gated by the `staging` GitHub environment and
   runs `plan` + `apply`. It authenticates with the same Workload Identity
   Federation provider/service account as the deploy workflows.

```bash
gh workflow run terraform.yml -f apply=true -f environment=staging
gh run watch
```

Local `terraform init`/`plan` is fine for inspection:

```bash
cd infra
terraform init -backend=false        # provider only, no state access
terraform validate
terraform fmt -check -recursive
terraform plan -var-file=envs/staging.tfvars -lock=false
```

## Adopting the existing staging resources (one time — issue #112)

Staging resources already exist in `vt-gcp-00042` from earlier hand/`gcloud`
work, so the first apply must **import** them rather than create duplicates.
`infra/imports.tf` holds the `import {}` blocks (Terraform >= 1.7):

```bash
cd infra
terraform init -backend-config="prefix=agentic-kg/staging"
terraform plan  -var-file=envs/staging.tfvars -lock=false   # expect 0 to destroy/replace
terraform apply -var-file=envs/staging.tfvars
# then DELETE infra/imports.tf and commit — the import is complete.
```

`imports.tf` is staging-specific and hard-coded. Resources that do not yet
exist (the IAP SSH firewall, the `networkUser` binding, the
`NEO4J_PASSWORD_NEXT` secret, the `rotate_password` Job and the new
`NEO4J_URI` version) have **no** import block — they are created by the apply.
The full import/create matrix is in the file header.

## Resource ownership (ADR-0006, issue #112)

After adoption, two systems touch the same Cloud Run resources. The split is
deliberate:

| Attribute | Owner | Mechanism |
|---|---|---|
| Container **image** | deploy workflows (`deploy-master.yml`, `deploy-branch.yml`) | `gcloud run ... update --image`, `lifecycle.ignore_changes` in Terraform |
| Revision **labels / annotations** (`commit`, `environment`, rotation stamp) | deploy / rotation workflows | `--update-labels`, `lifecycle.ignore_changes` |
| **env / secrets / scaling / port / IAM / VPC** | **Terraform** | declared in `main.tf` |
| KGIS/KGCS opt-in flags | **Terraform** | `kgis_kgcs_enabled` + `canonical_namespace` in `envs/staging.tfvars` |
| Neo4j **password value** | rotation workflow | `.github/workflows/rotate-neo4j-password.yml` |
| Semantic Scholar **API key value** | sync workflow | `.github/workflows/sync-s2-key.yml` |

The deploy workflows were reduced to an image roll for exactly this reason:
`gcloud run deploy` with its own flags (and `--set-secrets`, which clears
unlisted secret env vars) was dropping the hand-set KGIS/KGCS flags on every
deploy (#112). Terraform now declares them, so they cannot be dropped.

`deploy-master.yml` is the **only automatic deploy path**. `cloudbuild.yaml` is
**manual-only** (operator `gcloud builds submit`); it now also rolls only the
image + `commit` label, so the manual escape hatch cannot drop Terraform-owned
config either. Two legacy **global** Cloud Build triggers
(`agentic-kg-api-staging`, `agentic-kg-ui-staging`, created 2026-02-04, running
as the default compute SA) still fire on pushes; before the guard they
`ReplaceService`d staging with a spec-less `gcloud run deploy`. They are not in
Terraform state (`enable_build_triggers = false`) and the WIF CI SA cannot delete
them, so the **owner deletes them by hand** — Console → Cloud Build → Triggers
(region **global**) → Delete, or

```bash
gcloud builds triggers delete agentic-kg-api-staging --region=global --project=vt-gcp-00042
gcloud builds triggers delete agentic-kg-ui-staging  --region=global --project=vt-gcp-00042
```

Do not touch the `denario-*` triggers. Until deletion, `cloudbuild.yaml` guards
every step: a triggered build exits 0 in seconds without deploying. Full
procedure: [`docs/operations/deploy-runbook.md`](../docs/operations/deploy-runbook.md).

### KGIS/KGCS opt-in flags

`envs/staging.tfvars` sets:

```hcl
kgis_kgcs_enabled   = true
canonical_namespace = "staging"
ingest_ledger_dir   = "/tmp/kgis-shadow"
```

`kgis_kgcs_enabled = true` sets `CANONICAL_API_ENABLED=1` and
`KGCS_CANONICAL_NAMESPACE` on the API service, and `INGEST_MODE=kgis_kgcs`,
`KGIS_INGESTION_ENABLED=1`, `KGCS_RESOLUTION_ENABLED=1`,
`KGCS_CANONICAL_NAMESPACE` and `INGEST_LEDGER_DIR` on the ingest Job. The
namespace must be the same value on both (ADR-0005 isolation boundary). Set the
flag to `false` to remove them cleanly.

## Neo4j credential ownership

The Secret Manager **container** `NEO4J_PASSWORD` is managed by Terraform; the
version inside it is **not**. The value is generated, staged and promoted by
`.github/workflows/rotate-neo4j-password.yml`, which now runs **quarterly on a
schedule** and on demand (owner decision 2026-10-06). Terraform never writes a
`google_secret_manager_secret_version.neo4j_password` — doing so would fight the
rotation and put a credential back into state.

A fresh environment bootstraps by seeding the first `NEO4J_PASSWORD` version
out-of-band (operator or a one-off rotation bootstrap), then the VM's
`metadata_startup_script` reads it from Secret Manager and calls
`neo4j-admin dbms set-initial-password`. After that the rotation workflow owns
it.

## Semantic Scholar API key ownership

The Secret Manager **container** `SEMANTIC_SCHOLAR_API_KEY` and a placeholder
**seed version** are managed by Terraform; the real value is **not**. The seed
exists only so the ingest Job's `:latest` secret reference is valid at create
time (Cloud Run refuses a secret ref with no versions) — its value
(`unset-seed-not-an-api-key`) is treated as "no key" by the S2 client, so a
run before the first sync simply goes unauthenticated. The enabled value is
written by `.github/workflows/sync-s2-key.yml`, which mints a new version from
the repo GitHub Actions secret `SEMANTIC_SCHOLAR_API_KEY` and then disables the
superseded ones (the seed included). Operator procedure:
[`docs/operations/semantic-scholar-key-runbook.md`](../docs/operations/semantic-scholar-key-runbook.md).

## Resources managed

- GCP APIs (compute, run, cloudbuild, artifactregistry, secretmanager)
- Compute Engine VM running Neo4j 5.x (adopted; `prevent_destroy` + targeted
  `ignore_changes` so adoption cannot replace or stop it)
- Firewall rules for Neo4j ports (7474, 7687), VPC-scoped only (ADR-0006)
- Secret Manager containers (NEO4J_URI, NEO4J_PASSWORD, NEO4J_PASSWORD_NEXT,
  SEMANTIC_SCHOLAR_API_KEY)
- Artifact Registry Docker repository
- Cloud Run API/UI services and ingest/rotate Jobs
- IAM bindings for secret access and VPC egress

See [`docs/operations/neo4j-hardening-runbook.md`](../docs/operations/neo4j-hardening-runbook.md)
for the operator procedure.
