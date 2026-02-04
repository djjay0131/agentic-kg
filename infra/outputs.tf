output "neo4j_ip" {
  description = "External IP of the Neo4j VM"
  value       = google_compute_instance.neo4j.network_interface[0].access_config[0].nat_ip
}

output "neo4j_bolt_uri" {
  description = "Neo4j Bolt connection URI"
  value       = "bolt://${google_compute_instance.neo4j.network_interface[0].access_config[0].nat_ip}:7687"
}

output "neo4j_browser_url" {
  description = "Neo4j Browser URL"
  value       = "http://${google_compute_instance.neo4j.network_interface[0].access_config[0].nat_ip}:7474"
}

output "neo4j_password" {
  description = "Neo4j password"
  value       = random_password.neo4j.result
  sensitive   = true
}

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
