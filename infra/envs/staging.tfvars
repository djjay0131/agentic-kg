project_id         = "vt-gcp-00042"
region             = "us-central1"
zone               = "us-central1-a"
env                = "staging"
neo4j_machine_type = "e2-medium"
neo4j_disk_size    = 20
api_memory         = "1Gi"
api_cpu            = "1"
api_min_instances  = 0
api_max_instances  = 5

# Ingest job
ingest_job_memory  = "2Gi"
ingest_job_cpu     = "2"
ingest_job_timeout = 1800

# GitHub Actions is the deploy path. Cloud Build triggers are not in use and
# the STAGING_API_URL GitHub-secret sync is no longer needed: ADR-0006 removed
# the duplicated Neo4j credentials, and the deploy workflows read URLs from
# Cloud Run directly.
github_owner          = "djjay0131"
github_repo           = "agentic-kg"
sync_github_secrets   = false
enable_build_triggers = false

# KGIS/KGCS opt-in seam (ADR-0004/ADR-0005, issue #112). These were hand-set
# per docs/operations/kgis-kgcs-staging-runbook.md and silently dropped by the
# next deploy; Terraform is now the single owner. The namespace MUST be the
# same value on the API and the ingest Job — it is the isolation boundary.
kgis_kgcs_enabled   = true
canonical_namespace = "staging"

# Durable KGIS ledger (nightly-pipeline design P0-2). The ingest Job mounts this
# bucket with a Cloud Run GCS volume and writes the ledger/evidence under the
# namespace subdirectory, so it survives scale-in instead of vanishing with
# /tmp. See infra/README.md §Durable KGIS ledger.
ingest_ledger_bucket = "vt-gcp-00042-agentic-kg-ledger-staging"
ingest_ledger_dir    = "/mnt/ledger/staging"
