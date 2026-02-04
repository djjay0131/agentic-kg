# =============================================================================
# GCP APIs
# =============================================================================
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# =============================================================================
# Artifact Registry
# =============================================================================
resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "agentic-kg"
  format        = "DOCKER"
  description   = "Agentic KG Docker images"

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Neo4j Password
# =============================================================================
resource "random_password" "neo4j" {
  length  = 24
  special = false
}

# =============================================================================
# Neo4j Compute Engine VM
# =============================================================================
resource "google_compute_instance" "neo4j" {
  name         = "agentic-kg-neo4j-${var.env}"
  machine_type = var.neo4j_machine_type
  zone         = var.zone
  tags         = ["neo4j-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.neo4j_disk_size
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {} # Ephemeral public IP
  }

  shielded_instance_config {
    enable_secure_boot = true
  }

  metadata = {
    neo4j-password = random_password.neo4j.result
  }

  metadata_startup_script = <<-SCRIPT
    #!/bin/bash
    set -e

    # Install Neo4j 5.x
    apt-get update
    apt-get install -y curl gnupg
    curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
    echo 'deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5' > /etc/apt/sources.list.d/neo4j.list
    apt-get update
    apt-get install -y neo4j || apt-get install -y neo4j=1:5.15.0

    # Configure
    cat >> /etc/neo4j/neo4j.conf <<EOF
    server.default_listen_address=0.0.0.0
    server.bolt.listen_address=0.0.0.0:7687
    server.http.listen_address=0.0.0.0:7474
    server.memory.heap.initial_size=512m
    server.memory.heap.max_size=1G
    server.memory.pagecache.size=512m
    dbms.security.procedures.unrestricted=apoc.*
    dbms.security.procedures.allowlist=apoc.*
    EOF

    # Set password
    NEO4J_PWD=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/attributes/neo4j-password" -H "Metadata-Flavor: Google")
    neo4j-admin dbms set-initial-password "$NEO4J_PWD"

    systemctl enable neo4j
    systemctl start neo4j
  SCRIPT

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Firewall — allow Neo4j ports
# =============================================================================
resource "google_compute_firewall" "neo4j" {
  name    = "allow-neo4j-${var.env}"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["7474", "7687"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["neo4j-server"]

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Secrets
# =============================================================================
resource "google_secret_manager_secret" "neo4j_uri" {
  secret_id = "NEO4J_URI${var.env == "prod" ? "_PROD" : ""}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "neo4j_uri" {
  secret      = google_secret_manager_secret.neo4j_uri.id
  secret_data = "bolt://${google_compute_instance.neo4j.network_interface[0].access_config[0].nat_ip}:7687"
}

resource "google_secret_manager_secret" "neo4j_password" {
  secret_id = "NEO4J_PASSWORD${var.env == "prod" ? "_PROD" : ""}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "neo4j_password" {
  secret      = google_secret_manager_secret.neo4j_password.id
  secret_data = random_password.neo4j.result
}

# =============================================================================
# IAM — Cloud Run service account can read secrets
# =============================================================================
data "google_project" "current" {}

resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Cloud Run — API service
# =============================================================================
resource "google_cloud_run_v2_service" "api" {
  name     = "agentic-kg-api-${var.env}"
  location = var.region

  template {
    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/api:latest"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          memory = var.api_memory
          cpu    = var.api_cpu
        }
      }

      env {
        name = "NEO4J_URI"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.neo4j_uri.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "NEO4J_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.neo4j_password.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = "OPENAI_API_KEY"
            version = "latest"
          }
        }
      }

      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = "ANTHROPIC_API_KEY"
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.secret_accessor,
    google_secret_manager_secret_version.neo4j_uri,
    google_secret_manager_secret_version.neo4j_password,
  ]
}

# Allow unauthenticated access to API
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# =============================================================================
# Cloud Run — UI service (Next.js)
# =============================================================================
resource "google_cloud_run_v2_service" "ui" {
  name     = "agentic-kg-ui-${var.env}"
  location = var.region

  template {
    scaling {
      min_instance_count = var.ui_min_instances
      max_instance_count = var.ui_max_instances
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/ui:latest"

      ports {
        container_port = 3000
      }

      resources {
        limits = {
          memory = var.ui_memory
          cpu    = var.ui_cpu
        }
      }

      env {
        name  = "API_URL"
        value = google_cloud_run_v2_service.api.uri
      }

      env {
        name  = "NODE_ENV"
        value = "production"
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_cloud_run_v2_service.api,
  ]
}

# Allow unauthenticated access to UI
resource "google_cloud_run_v2_service_iam_member" "ui_public" {
  name     = google_cloud_run_v2_service.ui.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# =============================================================================
# GitHub Actions Secrets (optional - enabled via sync_github_secrets)
# =============================================================================
# These secrets are automatically synced to GitHub Actions so CI can run
# integration tests against the staging environment without manual setup.

resource "github_actions_secret" "staging_neo4j_uri" {
  count           = var.sync_github_secrets && var.env == "staging" ? 1 : 0
  repository      = var.github_repo
  secret_name     = "STAGING_NEO4J_URI"
  plaintext_value = "bolt://${google_compute_instance.neo4j.network_interface[0].access_config[0].nat_ip}:7687"
}

resource "github_actions_secret" "staging_neo4j_password" {
  count           = var.sync_github_secrets && var.env == "staging" ? 1 : 0
  repository      = var.github_repo
  secret_name     = "STAGING_NEO4J_PASSWORD"
  plaintext_value = random_password.neo4j.result
}

resource "github_actions_secret" "staging_api_url" {
  count           = var.sync_github_secrets && var.env == "staging" ? 1 : 0
  repository      = var.github_repo
  secret_name     = "STAGING_API_URL"
  plaintext_value = google_cloud_run_v2_service.api.uri
}
