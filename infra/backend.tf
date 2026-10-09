# Remote state lives in GCP (owner decision 2026-10-06). The bucket already
# exists in the VT project `vt-gcp-00042` and is versioned with public access
# prevention enabled.
#
# The bucket is shared by every environment; the environment is isolated by
# the object prefix, supplied at init time — never hard-coded here:
#
#   terraform init -backend-config="prefix=agentic-kg/staging"
#   terraform init -backend-config="prefix=agentic-kg/prod"
#
# `.github/workflows/terraform.yml` passes the staging prefix for PR plans and
# for the dispatch apply. No local state is committed; `infra/.gitignore`
# ignores `*.tfstate`.
terraform {
  backend "gcs" {
    bucket = "vt-gcp-00042-tfstate"
  }
}
