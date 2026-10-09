# =============================================================================
# ONE-TIME staging adoption — import blocks (Terraform >= 1.7)
# =============================================================================
#
# These blocks adopt the EXISTING staging resources in `vt-gcp-00042` into the
# GCS remote state (`agentic-kg/staging`) without recreating them. They are
# deliberately hard-coded to staging: they are a one-shot migration, not
# reusable configuration.
#
# OPERATOR PROCEDURE
#   1. terraform init -backend-config="prefix=agentic-kg/staging"
#   2. terraform plan  -var-file=envs/staging.tfvars -lock=false
#      — review: ZERO destroy/replace. Updates (KGIS env, scaling, the new
#        NEO4J_URI version) are expected; replacement is not.
#   3. terraform apply -var-file=envs/staging.tfvars
#   4. DELETE THIS FILE and commit — the resources are now in state, and the
#      import blocks are only valid until the first successful apply.
#      (`terraform plan` after step 3 will also remind you.)
#
# Graph of what is imported and what is deliberately NOT:
#
#   IMPORTED (already live)                  NOT IMPORTED (created by apply)
#   ------------------------------           ------------------------------
#   google_project_service.apis (x6)         google_compute_firewall.neo4j_iap_ssh
#   google_artifact_registry_repository       google_project_iam_member.network_user
#   google_compute_instance.neo4j            google_secret_manager_secret
#   google_compute_firewall.neo4j              .neo4j_password_next
#   google_secret_manager_secret             google_cloud_run_v2_job
#     .neo4j_uri / .neo4j_password             .rotate_password
#   google_project_iam_member                google_secret_manager_secret_version
#     .secret_accessor                         .neo4j_uri (a NEW version on the
#   google_cloud_run_v2_service                adopted container)
#     .api / .ui
#   google_cloud_run_v2_job.ingest
#   *_iam_member.api_public / .ui_public /
#     .api_can_run_ingest
# =============================================================================

import {
  for_each = toset([
    "compute.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
  ])

  to = google_project_service.apis[each.key]
  id = "vt-gcp-00042/${each.key}"
}

import {
  to = google_artifact_registry_repository.docker
  id = "projects/vt-gcp-00042/locations/us-central1/repositories/agentic-kg"
}

import {
  to = google_compute_instance.neo4j
  id = "projects/vt-gcp-00042/zones/us-central1-a/instances/agentic-kg-neo4j-staging"
}

import {
  to = google_compute_firewall.neo4j
  id = "projects/vt-gcp-00042/global/firewalls/allow-neo4j-staging"
}

import {
  to = google_secret_manager_secret.neo4j_uri
  id = "projects/vt-gcp-00042/secrets/NEO4J_URI"
}

import {
  to = google_secret_manager_secret.neo4j_password
  id = "projects/vt-gcp-00042/secrets/NEO4J_PASSWORD"
}

# Space-delimited: project, role, member.
import {
  to = google_project_iam_member.secret_accessor
  id = "vt-gcp-00042 roles/secretmanager.secretAccessor serviceAccount:542888988741-compute@developer.gserviceaccount.com"
}

import {
  to = google_cloud_run_v2_service.api
  id = "projects/vt-gcp-00042/locations/us-central1/services/agentic-kg-api-staging"
}

import {
  to = google_cloud_run_v2_service.ui
  id = "projects/vt-gcp-00042/locations/us-central1/services/agentic-kg-ui-staging"
}

import {
  to = google_cloud_run_v2_job.ingest
  id = "projects/vt-gcp-00042/locations/us-central1/jobs/agentic-kg-ingest-staging"
}

# IAM member imports are space-delimited: resource, role, member.
import {
  to = google_cloud_run_v2_service_iam_member.api_public
  id = "projects/vt-gcp-00042/locations/us-central1/services/agentic-kg-api-staging roles/run.invoker allUsers"
}

import {
  to = google_cloud_run_v2_service_iam_member.ui_public
  id = "projects/vt-gcp-00042/locations/us-central1/services/agentic-kg-ui-staging roles/run.invoker allUsers"
}

import {
  to = google_cloud_run_v2_job_iam_member.api_can_run_ingest
  id = "projects/vt-gcp-00042/locations/us-central1/jobs/agentic-kg-ingest-staging roles/run.invoker serviceAccount:542888988741-compute@developer.gserviceaccount.com"
}
