# =============================================================================
# GCP APIs
# =============================================================================
resource "google_project_service" "apis" {
  for_each = toset([
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    # Nightly pipeline (ADR-0008): Cloud Workflows orchestrates the nightly Job
    # and Cloud Scheduler triggers it at 02:30 America/New_York.
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
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
# Neo4j password
# =============================================================================
# The password VALUE is deliberately NOT managed by Terraform any more. The
# Secret Manager container `google_secret_manager_secret.neo4j_password` is
# still managed (created/replicated by Terraform); the ENABLED version inside
# it is owned by `.github/workflows/rotate-neo4j-password.yml` (ADR-0006).
# Terraform must never write a version back, or it would fight the rotation.
# See the ADR-0006 consequences and infra/README.md §Neo4j credential ownership.

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

  # The firewall, not the absence of a NAT IP, is the ingress control.
  # `neo4j_assign_public_ip` defaults to true so that applying ADR-0006
  # cannot replace the instance; with the firewall closed the address is
  # unreachable. Detaching it is a documented follow-up.
  network_interface {
    network = var.network

    dynamic "access_config" {
      for_each = var.neo4j_assign_public_ip ? [1] : []
      content {} # Ephemeral public IP
    }
  }

  shielded_instance_config {
    enable_secure_boot = true
  }

  # Adoption safety (see infra/imports.tf): these attributes either force a new
  # resource or would mutate a running, already-provisioned VM. They are set on
  # fresh creates but left as-is on the adopted staging VM.
  #
  #   metadata                  — the live VM carries `startup-script` and
  #                               possibly console-managed ssh-keys; Terraform
  #                               does not need to reconcile them.
  #   metadata_startup_script   — provider ForceNew: the live attribute is not
  #                               returned on import, so Terraform would propose
  #                               a destroy/recreate to "fix" the drift.
  #   boot_disk                 — initialize_params image/size/type churn against
  #                               the live disk (`debian-12` is a family alias).
  #   shielded_instance_config  — not ForceNew, but changing it requires stopping
  #                               the VM (provider errors without
  #                               allow_stopping_for_update). Adoption must not
  #                               stop Neo4j. Fresh environments still get
  #                               secure boot from the block below at create.
  #
  # network_interface / access_config stay managed: the firewall and ADR-0006
  # depend on the NIC's network and tag, and the VM NIC stays as-is.
  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      metadata,
      metadata_startup_script,
      boot_disk,
      shielded_instance_config,
    ]
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

    # Seed the initial password from Secret Manager. Terraform no longer
    # generates or writes it; the operator (or the rotation workflow) creates
    # the first NEO4J_PASSWORD version before the VM is created. The VM's
    # default compute service account holds roles/secretmanager.secretAccessor.
    NEO4J_PASSWORD_SECRET="${local.neo4j_password_secret_id}"
    PROJECT_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/project/project-id" -H "Metadata-Flavor: Google")
    TOKEN=$(curl -s "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" -H "Metadata-Flavor: Google" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
    NEO4J_PWD=$(curl -s "https://secretmanager.googleapis.com/v1/projects/$PROJECT_ID/secrets/$NEO4J_PASSWORD_SECRET/versions/latest:access" -H "Authorization: Bearer $TOKEN" | python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["payload"]["data"]).decode())')
    neo4j-admin dbms set-initial-password "$NEO4J_PWD"

    systemctl enable neo4j
    systemctl start neo4j
  SCRIPT

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Network — the subnetwork Neo4j and Cloud Run egress share
# =============================================================================
data "google_compute_subnetwork" "neo4j" {
  name    = var.subnetwork
  region  = var.region
  project = var.project_id
}

locals {
  # Default to the subnetwork CIDR so Neo4j is reachable only from inside the
  # VPC (Cloud Run Direct VPC egress sources from this range). ADR-0006.
  neo4j_source_ranges = length(var.neo4j_allowed_source_ranges) > 0 ? (
    var.neo4j_allowed_source_ranges
  ) : [data.google_compute_subnetwork.neo4j.ip_cidr_range]

  # Secret IDs are env-scoped once, here, and reused by the containers and the
  # VM bootstrap so prod's `_PROD` suffix cannot drift between them.
  neo4j_uri_secret_id                = "NEO4J_URI${var.env == "prod" ? "_PROD" : ""}"
  neo4j_password_secret_id           = "NEO4J_PASSWORD${var.env == "prod" ? "_PROD" : ""}"
  neo4j_password_next_secret_id      = "NEO4J_PASSWORD_NEXT${var.env == "prod" ? "_PROD" : ""}"
  semantic_scholar_api_key_secret_id = "SEMANTIC_SCHOLAR_API_KEY${var.env == "prod" ? "_PROD" : ""}"

  # Placeholder value seeded as the first version of the S2 key secret so the
  # ingest Job can be created before the sync workflow runs (Cloud Run refuses
  # an env that references `<secret>:latest` with no versions). The client
  # treats this exact value as "no key" — see
  # packages/core/src/agentic_kg/data_acquisition/config.py.
  semantic_scholar_api_key_placeholder = "unset-seed-not-an-api-key"
}

# =============================================================================
# Firewall — allow Neo4j ports from inside the VPC only
# =============================================================================
resource "google_compute_firewall" "neo4j" {
  name        = "allow-neo4j-${var.env}"
  network     = var.network
  description = "Neo4j bolt/http from the VPC subnetwork only. Never 0.0.0.0/0. See ADR-0006."

  allow {
    protocol = "tcp"
    ports    = ["7474", "7687"]
  }

  source_ranges = local.neo4j_source_ranges
  target_tags   = ["neo4j-server"]

  depends_on = [google_project_service.apis]
}

# Optional break-glass SSH via Identity-Aware Proxy. IAP is allowed on 22
# ONLY — never on 7474/7687. See ADR-0006.
resource "google_compute_firewall" "neo4j_iap_ssh" {
  count       = var.enable_iap_ssh ? 1 : 0
  name        = "allow-iap-ssh-neo4j-${var.env}"
  network     = var.network
  description = "SSH to the Neo4j VM through IAP only (tcp/22, IAP range). See ADR-0006."

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["neo4j-server"]

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Secrets
# =============================================================================
resource "google_secret_manager_secret" "neo4j_uri" {
  secret_id = local.neo4j_uri_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Intentionally a NEW Secret Manager version on each apply: the container is
# imported (infra/imports.tf) but this version is not, so applying points
# `latest` at the VM's internal VPC address without replacing the container.
# See ADR-0006.
resource "google_secret_manager_secret_version" "neo4j_uri" {
  secret = google_secret_manager_secret.neo4j_uri.id
  # Internal VPC address: Cloud Run reaches it through Direct VPC egress.
  # See ADR-0006.
  secret_data = "bolt://${google_compute_instance.neo4j.network_interface[0].network_ip}:7687"
}

# The CONTAINER is managed; the password VALUE is not. No
# `google_secret_manager_secret_version.neo4j_password` exists on purpose —
# the rotation workflow owns the enabled version (ADR-0006). Adding one here
# would fight the rotation and re-introduce a Terraform-known credential.
resource "google_secret_manager_secret" "neo4j_password" {
  secret_id = local.neo4j_password_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Semantic Scholar API key. Same ownership split as NEO4J_PASSWORD: Terraform
# creates the CONTAINER only. `.github/workflows/sync-s2-key.yml` writes the
# value as a new version (the GitHub Actions secret
# SEMANTIC_SCHOLAR_API_KEY is the source), so no credential enters state.
resource "google_secret_manager_secret" "semantic_scholar_api_key" {
  secret_id = local.semantic_scholar_api_key_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Seed the placeholder version so the ingest Job's `:latest` reference is
# valid at create/update time (Cloud Run refuses a secret ref with no
# versions). The sync workflow writes the real key as a NEW version on top;
# this one is then disabled by that workflow. ignore_changes keeps Terraform
# from re-enabling or rewriting it, and ABANDON means a destroy never deletes
# real key material.
resource "google_secret_manager_secret_version" "semantic_scholar_api_key_seed" {
  secret          = google_secret_manager_secret.semantic_scholar_api_key.id
  secret_data     = local.semantic_scholar_api_key_placeholder
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [secret_data, enabled]
  }
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

# The project-level binding above already lets the Job's runtime SA read every
# secret, so this is belt-and-braces. It is declared anyway so the S2 key's
# accessor is visible in the plan and survives any future narrowing of the
# project-level grant.
resource "google_secret_manager_secret_iam_member" "semantic_scholar_api_key_accessor" {
  secret_id = google_secret_manager_secret.semantic_scholar_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"

  depends_on = [google_project_service.apis]
}

# Cloud Run Direct VPC egress runs as this service account; it needs
# networkUser to attach to the subnetwork. ADR-0006.
resource "google_project_iam_member" "network_user" {
  project = var.project_id
  role    = "roles/compute.networkUser"
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

    # Direct VPC egress: Neo4j is private, public APIs still use managed
    # egress. ADR-0006.
    vpc_access {
      network_interfaces {
        network    = var.network
        subnetwork = data.google_compute_subnetwork.neo4j.id
      }
      egress = var.neo4j_vpc_egress
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

      # GCP config for Cloud Run Jobs trigger
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GCP_REGION"
        value = var.region
      }

      env {
        name  = "GCP_ENV"
        value = var.env
      }

      env {
        name  = "CORS_ORIGINS"
        value = "*"
      }

      # KGIS/KGCS opt-in seam (ADR-0004/ADR-0005, issue #112). Declared here so
      # the deploy workflows — which now roll the image only — cannot drop them.
      dynamic "env" {
        for_each = var.kgis_kgcs_enabled ? [1] : []
        content {
          name  = "CANONICAL_API_ENABLED"
          value = "1"
        }
      }

      dynamic "env" {
        for_each = var.kgis_kgcs_enabled ? [1] : []
        content {
          name  = "KGCS_CANONICAL_NAMESPACE"
          value = var.canonical_namespace
        }
      }
    }
  }

  # The image and the deploy-stamped revision labels/annotations are owned by
  # `.github/workflows/deploy-*.yml`; Terraform owns everything else. See the
  # ownership table in infra/README.md.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.secret_accessor,
    google_project_iam_member.network_user,
    google_secret_manager_secret_version.neo4j_uri,
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
# KGIS ledger bucket — durable shadow ledger/evidence for the ingest Job
# =============================================================================
# The KGIS shadow ledger and evidence registry are SQLite files. Cloud Run gives
# each revision a private, ephemeral filesystem, so staging held them in
# /tmp/kgis-shadow and lost them on scale-in (nightly-pipeline design P0-2).
# This bucket is mounted into the ingest Job with a Cloud Run GCS volume
# (gcsfuse) and INGEST_LEDGER_DIR points at a namespaced subdirectory, so the
# ledger survives the Job's instance scaling in.
#
# Created only when var.ingest_ledger_bucket names it: environments that keep
# the job-local /tmp ledger get no bucket and no volume.
resource "google_storage_bucket" "ledger" {
  count = var.ingest_ledger_bucket != "" ? 1 : 0

  name                        = var.ingest_ledger_bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  # Keep the live object; delete superseded (noncurrent) generations after 30
  # days. The ledger is written in place, so an operator recovering a corrupted
  # write has a month of generations to fall back to.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# The ingest Job's runtime SA writes the ledger, so it gets object admin on THIS
# bucket only — never a project-wide storage role.
resource "google_storage_bucket_iam_member" "ingest_ledger" {
  count = var.ingest_ledger_bucket != "" ? 1 : 0

  bucket = google_storage_bucket.ledger[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Cloud Run Job — Ingestion (long-running paper ingestion)
# =============================================================================
resource "google_cloud_run_v2_job" "ingest" {
  provider = google-beta

  name     = "agentic-kg-ingest-${var.env}"
  location = var.region

  # The GCS ledger volume (`gcs` below) is a Cloud Run preview feature, so the
  # Job opts into a preview launch stage. This is an in-place update to the
  # existing Job, not a replacement.
  launch_stage = "BETA"

  template {
    # The durable ledger is single-writer SQLite. One task, no intra-execution
    # parallelism: Cloud Storage FUSE does not provide file locking, so two
    # concurrent tasks sharing the mount would corrupt the ledger (see
    # docs/operations/kgis-kgcs-staging-runbook.md §Durable ledger). This is also
    # enforced in code by the writer lease in migration/ingestion/writer_lease.py.
    parallelism = 1
    task_count  = 1

    template {
      dynamic "volumes" {
        for_each = var.ingest_ledger_bucket != "" ? [1] : []
        content {
          name = "ledger"
          gcs {
            bucket    = var.ingest_ledger_bucket
            read_only = false
          }
        }
      }

      containers {
        image   = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/job:latest"
        command = ["python", "-m", "agentic_kg.job_runner"]

        dynamic "volume_mounts" {
          for_each = var.ingest_ledger_bucket != "" ? [1] : []
          content {
            name       = "ledger"
            mount_path = var.ingest_ledger_mount_path
          }
        }

        resources {
          limits = {
            memory = var.ingest_job_memory
            cpu    = var.ingest_job_cpu
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

        # Nightly-pipeline P0-4: live Semantic Scholar calls from the Job were
        # unauthenticated and hit the shared anonymous rate-limit pool. The
        # value is owned by `.github/workflows/sync-s2-key.yml`; Terraform
        # declares where the Job reads it from.
        env {
          name = "SEMANTIC_SCHOLAR_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.semantic_scholar_api_key.secret_id
              version = "latest"
            }
          }
        }

        # KGIS/KGCS opt-in seam (ADR-0004/ADR-0005, issue #112). These were
        # applied by hand per the staging runbook and were silently dropped by
        # the next deploy; Terraform now owns them.
        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "INGEST_MODE"
            value = "kgis_kgcs"
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGIS_INGESTION_ENABLED"
            value = "1"
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGCS_RESOLUTION_ENABLED"
            value = "1"
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGCS_CANONICAL_NAMESPACE"
            value = var.canonical_namespace
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "INGEST_LEDGER_DIR"
            value = var.ingest_ledger_dir
          }
        }
      }

      # Direct VPC egress: same private path to Neo4j as the API. ADR-0006.
      vpc_access {
        network_interfaces {
          network    = var.network
          subnetwork = data.google_compute_subnetwork.neo4j.id
        }
        egress = var.neo4j_vpc_egress
      }

      timeout     = "${var.ingest_job_timeout}s"
      max_retries = 0
    }
  }

  # Image and deploy-stamped labels/annotations are owned by the deploy
  # workflows (the Job is re-pointed at the freshly-built image there).
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.secret_accessor,
    google_project_iam_member.network_user,
    google_secret_manager_secret_version.neo4j_uri,
    google_secret_manager_secret_version.semantic_scholar_api_key_seed,
  ]
}

# IAM — API service account can trigger and poll ingest job (minimum permissions)
resource "google_cloud_run_v2_job_iam_member" "api_can_run_ingest" {
  name     = google_cloud_run_v2_job.ingest.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
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

  # Image and deploy-stamped labels/annotations are owned by the deploy
  # workflows. The UI declares no secret env; the deploy workflows no longer
  # set one either (the UI reads only API_URL).
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
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

# Removed by ADR-0006: STAGING_NEO4J_URI / STAGING_NEO4J_PASSWORD. GitHub
# runners no longer reach Neo4j directly (it is VPC-private), and the
# credential now has a single source of truth in Secret Manager. Any stale
# values should be deleted from the repository settings by the owner.

resource "github_actions_secret" "staging_api_url" {
  count           = var.sync_github_secrets && var.env == "staging" ? 1 : 0
  repository      = var.github_repo
  secret_name     = "STAGING_API_URL"
  plaintext_value = google_cloud_run_v2_service.api.uri
}

# =============================================================================
# Password rotation (ADR-0006)
# =============================================================================
# The rotation job runs INSIDE the VPC (Neo4j is private). The workflow
# stages the new password in NEO4J_PASSWORD_NEXT so it never has to be
# passed to the job through logs or execution metadata; the job reads
# both the current and the next value from Secret Manager.

resource "google_secret_manager_secret" "neo4j_password_next" {
  secret_id = local.neo4j_password_next_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Cloud Run refuses to create a Job whose env references `<secret>:latest`
# when the secret has no versions. Seed one placeholder version so
# google_cloud_run_v2_job.rotate_password can be created. The value is never
# used: rotate-neo4j-password.yml writes a fresh NEXT version before every
# execution. ignore_changes keeps Terraform from ever touching it again, and
# ABANDON means a destroy never deletes real rotation material.
resource "google_secret_manager_secret_version" "neo4j_password_next_seed" {
  secret          = google_secret_manager_secret.neo4j_password_next.id
  secret_data     = "unset-seed-not-a-password"
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [secret_data, enabled]
  }
}

resource "google_cloud_run_v2_job" "rotate_password" {
  name     = "agentic-kg-rotate-neo4j-${var.env}"
  location = var.region

  template {
    template {
      # Dedicated identity (ADR-0007): the only *workload* allowed to add
      # NEO4J_PASSWORD versions, so the password is generated and stored
      # inside GCP and never transits a GitHub runner. (The apply identity
      # gh-deploy still holds secretmanager.admin; see ADR-0007 §Risks.)
      service_account = google_service_account.neo4j_rotator.email

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/job:latest"
        # In-GCP generation lives in its own module (ADR-0007): an image that
        # predates it fails with ModuleNotFoundError before touching Neo4j,
        # instead of running the legacy entrypoint against the placeholder.
        command = ["python", "-m", "agentic_kg.rotate_password_gcp"]

        resources {
          limits = {
            memory = "512Mi"
            cpu    = "1"
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
          name = "NEO4J_PASSWORD_NEXT"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.neo4j_password_next.secret_id
              version = "latest"
            }
          }
        }

        # Where the job writes what it generates (ADR-0007). Names, not values.
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "NEO4J_PASSWORD_SECRET_ID"
          value = google_secret_manager_secret.neo4j_password.secret_id
        }
        env {
          name  = "NEO4J_PASSWORD_NEXT_SECRET_ID"
          value = google_secret_manager_secret.neo4j_password_next.secret_id
        }
      }

      # Same private path to Neo4j as the other workloads.
      vpc_access {
        network_interfaces {
          network    = var.network
          subnetwork = data.google_compute_subnetwork.neo4j.id
        }
        egress = var.neo4j_vpc_egress
      }

      timeout     = "300s"
      max_retries = 0
    }
  }

  # Image and deploy-stamped labels/annotations are owned by the deploy
  # workflows (the rotation job runs the same `job` image as ingest).
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.secret_accessor,
    google_project_iam_member.network_user,
    google_secret_manager_secret_version.neo4j_uri,
    google_secret_manager_secret_version.neo4j_password_next_seed,
    google_secret_manager_secret_iam_member.rotator_accessor,
    google_secret_manager_secret_iam_member.rotator_version_adder,
    google_project_iam_member.rotator_network_user,
  ]
}

# =============================================================================
# ADR-0007: narrow identities for secret handling
# =============================================================================
# 1. neo4j_rotator runs the rotation Job. It may read the Neo4j secrets and
#    ADD versions to NEO4J_PASSWORD / NEO4J_PASSWORD_NEXT — nothing else. The
#    API and ingest workloads keep the default compute identity, which can read
#    but not write.
# 2. ci_vendor_keys is what GitHub workflows impersonate to READ the vendor
#    keys (Semantic Scholar, OpenAI) at run time. It replaces the GitHub-secret
#    copies; it can read exactly those two secrets and nothing else, so a PR
#    workflow never holds the deployer's broad roles just to get an API key.

resource "google_service_account" "neo4j_rotator" {
  account_id   = "neo4j-rotator-${var.env}"
  display_name = "Neo4j password rotation job (${var.env})"
  description  = "ADR-0007: generates the Neo4j password inside GCP and stores it; never exposed."
}

resource "google_secret_manager_secret_iam_member" "rotator_accessor" {
  for_each = {
    uri      = google_secret_manager_secret.neo4j_uri.id
    password = google_secret_manager_secret.neo4j_password.id
    next     = google_secret_manager_secret.neo4j_password_next.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.neo4j_rotator.email}"
}

resource "google_secret_manager_secret_iam_member" "rotator_version_adder" {
  for_each = {
    password = google_secret_manager_secret.neo4j_password.id
    next     = google_secret_manager_secret.neo4j_password_next.id
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.neo4j_rotator.email}"
}

# Direct VPC egress runs as the job's service account (ADR-0006).
resource "google_project_iam_member" "rotator_network_user" {
  project = var.project_id
  role    = "roles/compute.networkUser"
  member  = "serviceAccount:${google_service_account.neo4j_rotator.email}"
}

# The deploy identity must be able to act as the rotator to update/execute
# the Job.
resource "google_service_account_iam_member" "deployer_acts_as_rotator" {
  count              = var.deploy_service_account_email == "" ? 0 : 1
  service_account_id = google_service_account.neo4j_rotator.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deploy_service_account_email}"
}

resource "google_service_account" "ci_vendor_keys" {
  account_id   = "gh-ci-vendor-keys-${var.env}"
  display_name = "GitHub CI vendor-key reader (${var.env})"
  description  = "ADR-0007: read-only access to vendor API keys for CI; impersonated via WIF."
}

# The services reference OPENAI_API_KEY with no env suffix; match them.
resource "google_secret_manager_secret_iam_member" "ci_vendor_keys_accessor" {
  for_each = {
    semantic_scholar = google_secret_manager_secret.semantic_scholar_api_key.id
    openai           = "projects/${var.project_id}/secrets/OPENAI_API_KEY"
  }
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ci_vendor_keys.email}"
}

resource "google_service_account_iam_member" "ci_vendor_keys_wif" {
  service_account_id = google_service_account.ci_vendor_keys.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${var.wif_pool_id}/attribute.repository/${var.wif_repository}"
}

# =============================================================================
# Cloud Build Triggers
# =============================================================================
# Triggers for automatic builds on push to master branch.
# Requires GitHub connection to be set up in Cloud Build console first:
# https://console.cloud.google.com/cloud-build/triggers/connect

# API service trigger - builds on changes to packages/api/** or packages/core/**
resource "google_cloudbuild_trigger" "api" {
  count    = var.enable_build_triggers ? 1 : 0
  name     = "agentic-kg-api-${var.env}"
  location = var.region

  github {
    owner = var.github_owner
    name  = var.github_repo

    push {
      branch = var.env == "staging" ? "^master$" : "^release/.*$"
    }
  }

  included_files = [
    "packages/api/**",
    "packages/core/**",
    "docker/Dockerfile.api",
    "cloudbuild.yaml",
  ]

  filename = "cloudbuild.yaml"

  substitutions = {
    _SERVICE = "api"
    _REGION  = var.region
  }

  depends_on = [google_project_service.apis]
}

# UI service trigger - builds on changes to packages/ui/**
resource "google_cloudbuild_trigger" "ui" {
  count    = var.enable_build_triggers ? 1 : 0
  name     = "agentic-kg-ui-${var.env}"
  location = var.region

  github {
    owner = var.github_owner
    name  = var.github_repo

    push {
      branch = var.env == "staging" ? "^master$" : "^release/.*$"
    }
  }

  included_files = [
    "packages/ui/**",
    "docker/Dockerfile.ui",
    "cloudbuild.yaml",
  ]

  filename = "cloudbuild.yaml"

  substitutions = {
    _SERVICE = "ui"
    _REGION  = var.region
  }

  depends_on = [google_project_service.apis]
}

# =============================================================================
# Nightly pipeline (nightly-pipeline P1, ADR-0008)
# =============================================================================
# Cloud Scheduler (02:30 America/New_York) -> Cloud Workflows
# `agentic-kg-nightly-<env>` -> Cloud Run Job `agentic-kg-nightly-<env>`
# (`python -m agentic_kg.pipeline.nightly`). The Job plans tonight's queries,
# runs the configured ingest path in-process, and writes a PipelineRun report
# to the runs bucket and to a (:PipelineRun) Neo4j node. The report contract is
# docs/design/nightly-pipeline-contract.md.
#
# Everything here is gated by var.nightly_enabled (default false; staging sets
# true), so a prod apply without the flag creates nothing.

locals {
  # A dedicated SA is created unless the owner supplies one. Reusing an existing
  # SA (e.g. the compute runtime SA) is the documented fallback when the CI
  # apply principal cannot create service accounts (ADR-0008 §Consequences).
  create_nightly_sa = var.nightly_enabled && var.nightly_service_account_email == ""
  nightly_sa_email = (
    var.nightly_service_account_email != ""
    ? var.nightly_service_account_email
    : "agentic-kg-nightly@${var.project_id}.iam.gserviceaccount.com"
  )
}

resource "google_service_account" "nightly" {
  count = local.create_nightly_sa ? 1 : 0

  project      = var.project_id
  account_id   = "agentic-kg-nightly"
  display_name = "Agentic KG nightly pipeline"
  description  = "Runtime + OAuth identity for the nightly ingestion pipeline (ADR-0008)."

  depends_on = [google_project_service.apis]
}

# The runs bucket holds one JSON PipelineRun report per execution at
# nightly/<run_id>.json (the contract's GCS object). Uniform access, public
# access prevention, and a 90-day lifecycle: reports are operational history,
# not a system of record.
resource "google_storage_bucket" "runs" {
  count = var.nightly_enabled && var.nightly_runs_bucket != "" ? 1 : 0

  name                        = var.nightly_runs_bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# Least privilege: the nightly SA writes the runs bucket only. The Job also
# mounts the durable ledger bucket (same as the ingest Job), so it gets object
# admin on that bucket too, and never a project-wide storage role.
resource "google_storage_bucket_iam_member" "nightly_runs" {
  count = var.nightly_enabled && var.nightly_runs_bucket != "" ? 1 : 0

  bucket = var.nightly_runs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${local.nightly_sa_email}"

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "nightly_ledger" {
  count = var.nightly_enabled && var.ingest_ledger_bucket != "" ? 1 : 0

  bucket = var.ingest_ledger_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${local.nightly_sa_email}"

  depends_on = [google_project_service.apis]
}

# The nightly SA reads the same secrets the ingest Job does (Neo4j, OpenAI,
# Anthropic, Semantic Scholar). Project-level, matching the compute SA's grant;
# narrowing to per-secret accessors is a follow-up once the unmanaged secrets
# (OPENAI_API_KEY / ANTHROPIC_API_KEY) are declared here.
resource "google_project_iam_member" "nightly_secret_accessor" {
  count = var.nightly_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${local.nightly_sa_email}"

  depends_on = [google_project_service.apis]
}

# Direct VPC egress to the private Neo4j VM (ADR-0006).
resource "google_project_iam_member" "nightly_network_user" {
  count = var.nightly_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/compute.networkUser"
  member  = "serviceAccount:${local.nightly_sa_email}"

  depends_on = [google_project_service.apis]
}

# ---- Cloud Run Job: nightly orchestrator --------------------------------
resource "google_cloud_run_v2_job" "nightly" {
  provider = google-beta
  count    = var.nightly_enabled ? 1 : 0

  name         = "agentic-kg-nightly-${var.env}"
  location     = var.region
  launch_stage = "BETA"

  template {
    # One task, no parallelism: the nightly Job walks its plan serially, and
    # reuses the single-writer KGIS ledger (parallelism must be 1 for it).
    parallelism = 1
    task_count  = 1

    template {
      service_account = local.nightly_sa_email

      dynamic "volumes" {
        for_each = var.ingest_ledger_bucket != "" ? [1] : []
        content {
          name = "ledger"
          gcs {
            bucket    = var.ingest_ledger_bucket
            read_only = false
          }
        }
      }

      dynamic "volumes" {
        for_each = var.nightly_runs_bucket != "" ? [1] : []
        content {
          name = "runs"
          gcs {
            bucket    = var.nightly_runs_bucket
            read_only = false
          }
        }
      }

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/job:latest"

        command = ["python", "-m", "agentic_kg.pipeline.nightly"]

        dynamic "volume_mounts" {
          for_each = var.ingest_ledger_bucket != "" ? [1] : []
          content {
            name       = "ledger"
            mount_path = var.ingest_ledger_mount_path
          }
        }

        dynamic "volume_mounts" {
          for_each = var.nightly_runs_bucket != "" ? [1] : []
          content {
            name       = "runs"
            mount_path = var.nightly_runs_mount_path
          }
        }

        resources {
          limits = {
            memory = var.ingest_job_memory
            cpu    = var.ingest_job_cpu
          }
        }

        # Same secrets as the ingest Job: the nightly path imports and runs the
        # same in-process ingest code.
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

        env {
          name = "SEMANTIC_SCHOLAR_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.semantic_scholar_api_key.secret_id
              version = "latest"
            }
          }
        }

        # KGIS/KGCS opt-in seam, matching the ingest Job (ADR-0004/ADR-0005).
        # The nightly backend is chosen independently of the ingest Job's
        # KGIS/KGCS opt-in: the kgis_kgcs path does not yet select papers from a
        # query (it replays the committed corpus), so nightly growth runs on the
        # query-driven legacy path until live KGIS acquisition lands.
        env {
          name  = "INGEST_MODE"
          value = var.nightly_ingest_mode
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGIS_INGESTION_ENABLED"
            value = "1"
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGCS_RESOLUTION_ENABLED"
            value = "1"
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "KGCS_CANONICAL_NAMESPACE"
            value = var.canonical_namespace
          }
        }

        dynamic "env" {
          for_each = var.kgis_kgcs_enabled ? [1] : []
          content {
            name  = "INGEST_LEDGER_DIR"
            value = var.ingest_ledger_dir
          }
        }

        # Nightly-specific configuration.
        env {
          name  = "NIGHTLY_CATALOG"
          value = var.nightly_catalog_path
        }

        env {
          name  = "NIGHTLY_MAX_PAPERS"
          value = tostring(var.nightly_max_papers)
        }

        env {
          name  = "NIGHTLY_MAX_LLM_USD"
          value = tostring(var.nightly_max_llm_usd)
        }

        env {
          name  = "NIGHTLY_NAMESPACE"
          value = var.canonical_namespace
        }

        env {
          name  = "NIGHTLY_RUNS_DIR"
          value = var.nightly_runs_mount_path
        }

        env {
          name  = "NIGHTLY_RUNS_BUCKET"
          value = var.nightly_runs_bucket
        }
      }

      vpc_access {
        network_interfaces {
          network    = var.network
          subnetwork = data.google_compute_subnetwork.neo4j.id
        }
        egress = var.neo4j_vpc_egress
      }

      timeout     = "${var.nightly_timeout}s"
      max_retries = 0
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.nightly_secret_accessor,
    google_project_iam_member.nightly_network_user,
    google_secret_manager_secret_version.neo4j_uri,
    google_secret_manager_secret_version.semantic_scholar_api_key_seed,
  ]
}

# The workflow's SA (also the Job's runtime SA) may invoke the nightly Job.
resource "google_cloud_run_v2_job_iam_member" "nightly_runner" {
  count = var.nightly_enabled ? 1 : 0

  name     = google_cloud_run_v2_job.nightly[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.nightly_sa_email}"
}

# ---- Cloud Workflows: run the Job and wait -------------------------------
resource "google_workflows_workflow" "nightly" {
  count = var.nightly_enabled ? 1 : 0

  project         = var.project_id
  region          = var.region
  name            = "agentic-kg-nightly-${var.env}"
  description     = "Runs the nightly ingestion Job and waits for it (ADR-0008)."
  service_account = local.nightly_sa_email

  # `$${...}` escapes Terraform interpolation so the value stays a Workflows
  # expression; `${...}` is filled in by Terraform.
  source_contents = <<-YAML
    main:
      params: [args]
      steps:
        - init:
            assign:
              - trigger: $${default(args.trigger, "schedule")}
        - runJob:
            try:
              call: googleapis.run.v2.projects.locations.jobs.run
              args:
                name: ${google_cloud_run_v2_job.nightly[0].id}
                body:
                  overrides:
                    containerOverrides:
                      - env:
                          - name: NIGHTLY_TRIGGER
                            value: $${trigger}
              result: operation
            except:
              as: error
              steps:
                - logFailure:
                    call: sys.log
                    args:
                      severity: ERROR
                      text: $${"pipeline_run_failed " + json.encode_to_string(error)}
                - fail:
                    raise: $${error}
        - poll:
            call: googleapis.run.v2.projects.locations.operations.get
            args:
              name: $${operation.name}
            result: status
        - checkDone:
            switch:
              - condition: $${status.done != true}
                next: wait
            next: checkResult
        - wait:
            call: sys.sleep
            args:
              seconds: 30
            next: poll
        - checkResult:
            switch:
              - condition: $${status.error != null}
                next: failed
            next: succeeded
        - succeeded:
            return: $${operation.name}
        - failed:
            raise: $${status.error}
  YAML

  depends_on = [google_project_service.apis]
}

# The scheduler (and the manual GHA run) invoke the workflow. The google
# provider has no workflow-scoped IAM resource, so this is a project-level
# binder; workflows.invoker is the only workflow permission the SA holds.
resource "google_project_iam_member" "nightly_workflow_invoker" {
  count = var.nightly_enabled ? 1 : 0

  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${local.nightly_sa_email}"

  depends_on = [google_project_service.apis]
}

# Cloud Scheduler needs to mint an OAuth token for the workflow-invoking SA.
resource "google_service_account_iam_member" "scheduler_token_creator" {
  count = local.create_nightly_sa ? 1 : 0

  service_account_id = google_service_account.nightly[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

# ---- Cloud Scheduler: 02:30 America/New_York -----------------------------
resource "google_cloud_scheduler_job" "nightly" {
  count = var.nightly_enabled ? 1 : 0

  project     = var.project_id
  region      = var.region
  name        = "agentic-kg-nightly-${var.env}"
  description = "Trigger the nightly ingestion workflow at 02:30 America/New_York (ADR-0008)."
  schedule    = "30 2 * * *"
  time_zone   = "America/New_York"

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.nightly[0].name}/executions"
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({
      argument = jsonencode({ trigger = "schedule" })
    }))
    oauth_token {
      service_account_email = local.nightly_sa_email
    }
  }

  depends_on = [google_project_service.apis]
}
