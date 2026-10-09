# Summarize a `terraform show -json <planfile>` document as one line per
# changed resource: "<comma-joined-actions>  <address>".
#
# Used by .github/workflows/terraform-drift.yml to build the body of the
# `infra-drift` tracking issue. ONLY `.change.actions` and `.address` are
# read — never `.change.before` / `.change.after` — so an attribute value
# that happens to hold a secret or an internal URI can never reach the issue
# or the job summary.
#
# `no-op` (unchanged) and `read` (data-source refresh) are not drift and are
# dropped. Output is sorted for a stable, diffable issue body.
[
  .resource_changes[]?
  | select(.change.actions != ["no-op"])
  | select(.change.actions != ["read"])
  | "\(.change.actions | join(","))  \(.address)"
] | sort[]
