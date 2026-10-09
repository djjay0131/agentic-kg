variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "us-central1-a"
}

variable "env" {
  description = "Environment name (staging, prod)"
  type        = string
}

# Networking — Neo4j is private (ADR-0006); Cloud Run reaches it via Direct
# VPC egress.
variable "network" {
  description = "VPC network the Neo4j VM and Cloud Run egress attach to"
  type        = string
  default     = "default"
}

variable "subnetwork" {
  description = "Subnetwork (in var.region) whose CIDR is allowed to reach Neo4j"
  type        = string
  default     = "default"
}

variable "neo4j_allowed_source_ranges" {
  description = <<-EOT
    Explicit CIDRs allowed to reach Neo4j on 7474/7687. Empty (the default)
    means the var.subnetwork CIDR only — never 0.0.0.0/0.
  EOT
  type        = list(string)
  default     = []
}

variable "neo4j_vpc_egress" {
  description = "Cloud Run Direct VPC egress mode (PRIVATE_RANGES_ONLY or ALL_TRAFFIC)"
  type        = string
  default     = "PRIVATE_RANGES_ONLY"

  validation {
    condition     = contains(["PRIVATE_RANGES_ONLY", "ALL_TRAFFIC"], var.neo4j_vpc_egress)
    error_message = "neo4j_vpc_egress must be PRIVATE_RANGES_ONLY or ALL_TRAFFIC."
  }
}

variable "neo4j_assign_public_ip" {
  description = <<-EOT
    Attach the VM's ephemeral public IP. Defaults to true so applying ADR-0006
    cannot replace the instance; the firewall is the ingress control. Set false
    in a maintenance window to detach it (may replace the instance).
  EOT
  type        = bool
  default     = true
}

variable "enable_iap_ssh" {
  description = "Allow tcp/22 to the Neo4j VM from the IAP range (35.235.240.0/20)"
  type        = bool
  default     = true
}

# Neo4j
variable "neo4j_machine_type" {
  description = "Machine type for Neo4j VM"
  type        = string
  default     = "e2-medium"
}

variable "neo4j_disk_size" {
  description = "Boot disk size in GB for Neo4j VM"
  type        = number
  default     = 20
}

# Cloud Run
variable "api_memory" {
  description = "Memory for API Cloud Run service"
  type        = string
  default     = "1Gi"
}

variable "api_cpu" {
  description = "CPU for API Cloud Run service"
  type        = string
  default     = "1"
}

variable "api_min_instances" {
  description = "Minimum instances for API"
  type        = number
  default     = 0
}

variable "api_max_instances" {
  description = "Maximum instances for API"
  type        = number
  default     = 5
}

# Ingest Job (Cloud Run Job)
variable "ingest_job_memory" {
  description = "Memory for ingest Cloud Run Job"
  type        = string
  default     = "2Gi"
}

variable "ingest_job_cpu" {
  description = "CPU for ingest Cloud Run Job"
  type        = string
  default     = "2"
}

variable "ingest_job_timeout" {
  description = "Timeout in seconds for ingest Cloud Run Job"
  type        = number
  default     = 1800
}

# UI (Next.js)
variable "ui_memory" {
  description = "Memory for UI Cloud Run service"
  type        = string
  default     = "512Mi"
}

variable "ui_cpu" {
  description = "CPU for UI Cloud Run service"
  type        = string
  default     = "1"
}

variable "ui_min_instances" {
  description = "Minimum instances for UI"
  type        = number
  default     = 0
}

variable "ui_max_instances" {
  description = "Maximum instances for UI"
  type        = number
  default     = 3
}

# GitHub
variable "github_owner" {
  description = "GitHub repository owner (user or organization)"
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "agentic-kg"
}

variable "github_token" {
  description = "GitHub personal access token (or use GITHUB_TOKEN env var)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "sync_github_secrets" {
  description = "Whether to sync secrets to GitHub Actions"
  type        = bool
  default     = false
}

# Cloud Build
variable "enable_build_triggers" {
  description = "Whether to create Cloud Build triggers (requires GitHub connection in Cloud Build console)"
  type        = bool
  default     = false
}

# KGIS/KGCS opt-in seam (ADR-0004 / ADR-0005, issue #112)
variable "kgis_kgcs_enabled" {
  description = <<-EOT
    Opt the staging API service and ingest Job into the KGIS/KGCS migration
    path. When true, Terraform sets CANONICAL_API_ENABLED /
    KGCS_CANONICAL_NAMESPACE on the API and INGEST_MODE /
    KGIS_INGESTION_ENABLED / KGCS_RESOLUTION_ENABLED /
    KGCS_CANONICAL_NAMESPACE / INGEST_LEDGER_DIR on the ingest Job. The deploy
    workflows only roll the image, so Terraform alone owns these values.
  EOT
  type        = bool
  default     = false
}

variable "canonical_namespace" {
  description = "KGCS canonical namespace, shared by the API and the ingest Job (ADR-0005 isolation boundary)"
  type        = string
  default     = "staging"
}

variable "ingest_ledger_dir" {
  description = <<-EOT
    Directory the KGIS ledger/evidence writes to. The default
    "/tmp/kgis-shadow" is job-local and lost on scale-in. When
    var.ingest_ledger_bucket is set, point this at a namespaced subdirectory of
    var.ingest_ledger_mount_path (e.g. "/mnt/ledger/staging") so the ledger is
    durable in GCS.
  EOT
  type        = string
  default     = "/tmp/kgis-shadow"
}

# Durable KGIS ledger (nightly-pipeline design P0-2). Empty (the default)
# mounts no volume and keeps the ledger job-local in var.ingest_ledger_dir.
variable "ingest_ledger_bucket" {
  description = <<-EOT
    GCS bucket mounted into the ingest Job as a Cloud Run GCS volume (gcsfuse)
    for the durable KGIS ledger/evidence. Empty (the default) mounts nothing.
    Staging sets "vt-gcp-00042-agentic-kg-ledger-staging".
  EOT
  type        = string
  default     = ""
}

variable "ingest_ledger_mount_path" {
  description = "Mount path for the ledger GCS volume inside the ingest Job container"
  type        = string
  default     = "/mnt/ledger"
}

# =============================================================================
# Nightly pipeline (nightly-pipeline P1, ADR-0007)
# =============================================================================
# The whole nightly stack is gated by nightly_enabled. Default false so a prod
# apply (or any environment that has not opted in) creates nothing.
variable "nightly_enabled" {
  description = "Create the nightly pipeline (runs bucket, SA, Job, Workflow, Scheduler). Default false."
  type        = bool
  default     = false
}

variable "nightly_runs_bucket" {
  description = "GCS bucket for PipelineRun JSON reports. Empty disables the runs bucket and volume. Staging sets vt-gcp-00042-agentic-kg-runs-staging."
  type        = string
  default     = ""
}

variable "nightly_runs_mount_path" {
  description = "Mount path for the runs GCS volume inside the nightly Job container."
  type        = string
  default     = "/mnt/runs"
}

variable "nightly_catalog_path" {
  description = "Path to ingest-queries.yaml inside the Job image."
  type        = string
  default     = "/app/config/ingest-queries.yaml"
}

variable "nightly_max_papers" {
  description = "Default paper budget for one night (NIGHTLY_MAX_PAPERS)."
  type        = number
  default     = 50
}

variable "nightly_max_llm_usd" {
  description = "Estimated-USD budget for one night (NIGHTLY_MAX_LLM_USD). <= 0 disables the cap."
  type        = number
  default     = 0
}

variable "nightly_timeout" {
  description = "Timeout in seconds for the nightly Cloud Run Job."
  type        = number
  default     = 3600
}

variable "nightly_service_account_email" {
  description = <<-EOT
    Runtime + OAuth service account for the nightly pipeline. Empty (the
    default) creates agentic-kg-nightly@. Set this to an existing SA (e.g. the
    compute runtime SA) when the applying principal cannot create service
    accounts — the documented ADR-0007 fallback.
  EOT
  type        = string
  default     = ""
}

variable "nightly_ingest_mode" {
  description = "Backend for the nightly pipeline: \"legacy\" (query-driven S2/arXiv acquisition + extraction) or \"kgis_kgcs\" (replays the committed corpus until live KGIS acquisition exists)."
  type        = string
  default     = "legacy"
  validation {
    condition     = contains(["legacy", "kgis_kgcs"], var.nightly_ingest_mode)
    error_message = "nightly_ingest_mode must be legacy or kgis_kgcs."
  }
}
