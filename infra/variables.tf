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
