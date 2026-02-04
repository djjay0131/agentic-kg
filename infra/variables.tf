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
