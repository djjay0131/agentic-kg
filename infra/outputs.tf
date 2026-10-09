output "neo4j_ip" {
  description = "Internal (VPC) IP of the Neo4j VM. ADR-0006: private only."
  value       = google_compute_instance.neo4j.network_interface[0].network_ip
}

output "neo4j_bolt_uri" {
  description = "Neo4j Bolt connection URI (internal/VPC address; see ADR-0006)"
  value       = "bolt://${google_compute_instance.neo4j.network_interface[0].network_ip}:7687"
}

output "neo4j_browser_url" {
  description = "Neo4j Browser URL (internal/VPC address; see ADR-0006)"
  value       = "http://${google_compute_instance.neo4j.network_interface[0].network_ip}:7474"
}

# No `neo4j_password` output: the value is owned by the rotation workflow
# (ADR-0006), not Terraform. Read it from Secret Manager when needed.

output "api_url" {
  description = "Cloud Run API URL"
  value       = google_cloud_run_v2_service.api.uri
}

output "ui_url" {
  description = "Cloud Run UI URL"
  value       = google_cloud_run_v2_service.ui.uri
}

output "artifact_registry" {
  description = "Artifact Registry repository path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg"
}

output "ingest_ledger_bucket" {
  description = "GCS bucket holding the durable KGIS ledger (null when unset)"
  value       = var.ingest_ledger_bucket != "" ? google_storage_bucket.ledger[0].name : null
}

output "cloudbuild_trigger_api" {
  description = "Cloud Build trigger URL for API"
  value       = var.enable_build_triggers ? "https://console.cloud.google.com/cloud-build/triggers/edit/${google_cloudbuild_trigger.api[0].trigger_id}?project=${var.project_id}" : null
}

output "cloudbuild_trigger_ui" {
  description = "Cloud Build trigger URL for UI"
  value       = var.enable_build_triggers ? "https://console.cloud.google.com/cloud-build/triggers/edit/${google_cloudbuild_trigger.ui[0].trigger_id}?project=${var.project_id}" : null
}
