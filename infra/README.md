# Infrastructure as Code (Terraform)

Terraform configurations for provisioning and managing GCP infrastructure for Agentic KG.

## Directory Structure

```
infra/
├── main.tf              # Root module — composes all resources
├── variables.tf         # Input variables
├── outputs.tf           # Output values (URLs, IPs)
├── providers.tf         # GCP provider config
├── envs/
│   ├── staging.tfvars   # Staging variable values
│   └── prod.tfvars      # Production variable values
└── deploy.sh            # Build + deploy helper (wraps cloudbuild)
```

## Usage

```bash
cd infra

# Initialize Terraform
terraform init

# Plan changes for staging
terraform plan -var-file=envs/staging.tfvars

# Apply changes for staging
terraform apply -var-file=envs/staging.tfvars

# Deploy API service after infrastructure is ready
./deploy.sh staging api

# Destroy staging environment
terraform destroy -var-file=envs/staging.tfvars
```

## Environments

| Environment | Neo4j | Cloud Run | Purpose |
|-------------|-------|-----------|---------|
| staging | e2-medium VM | 0-5 instances | Testing |
| prod | e2-standard-2 VM | 1-10 instances | Production |

## State

Terraform state is stored locally in `infra/terraform.tfstate`.
For team use, migrate to a GCS backend (see `backend.tf.example`).

## Resources Managed

- GCP APIs (compute, run, cloudbuild, secretmanager, artifactregistry)
- Compute Engine VM running Neo4j 5.x
- Firewall rules for Neo4j ports (7474, 7687)
- Secret Manager secrets (NEO4J_URI, NEO4J_PASSWORD)
- Artifact Registry Docker repository
- Cloud Run API service
- IAM bindings for secret access
