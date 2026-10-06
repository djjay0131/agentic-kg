terraform {
  # >= 1.7.0 for `for_each` in `import {}` blocks (infra/imports.tf).
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    # The `random` provider was removed with `random_password.neo4j`: the Neo4j
    # password value is now owned by the rotation workflow, not Terraform.
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# GitHub provider - requires GITHUB_TOKEN env var or github_token variable
provider "github" {
  owner = var.github_owner
  token = var.github_token
}
