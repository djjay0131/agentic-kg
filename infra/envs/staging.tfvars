project_id        = "vt-gcp-00042"
region            = "us-central1"
zone              = "us-central1-a"
env               = "staging"
neo4j_machine_type = "e2-medium"
neo4j_disk_size    = 20
api_memory         = "1Gi"
api_cpu            = "1"
api_min_instances  = 0
api_max_instances  = 5

# GitHub Actions secrets sync
# Set github_owner and sync_github_secrets=true to auto-sync staging credentials
# Requires GITHUB_TOKEN env var with repo and secrets permissions
github_owner        = "djjay0131"
github_repo         = "agentic-kg"
sync_github_secrets = true

# Cloud Build triggers
# Requires GitHub connection in Cloud Build console first:
# https://console.cloud.google.com/cloud-build/triggers/connect
enable_build_triggers = true
