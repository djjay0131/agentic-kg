#!/usr/bin/env bash
# Dry-run of the Terraform drift summarizer against a fixed plan document.
#
# Proves the jq extraction used by .github/workflows/terraform-drift.yml emits
# exactly the resource addresses and planned actions, in a stable order, and
# NEVER an attribute value: the fixture embeds flag names, a connection URI, a
# token and a project number that must not appear in the output.
#
# Run directly, or via packages/core/tests/test_terraform_drift_workflow.py.
#
# Usage: scripts/test_terraform_drift_summary.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
filter="$here/terraform_drift_summary.jq"
fixture="$here/fixtures/terraform_plan.sample.json"

if ! command -v jq >/dev/null 2>&1; then
  echo "FAIL: jq is required but not on PATH" >&2
  exit 1
fi

actual="$(jq -r -f "$filter" "$fixture")"
read -r -d '' expected <<'EOF' || true
create  module.ingest.google_cloud_run_v2_job.ingest
delete,create  module.db.google_sql_database.main
update  module.api.google_cloud_run_v2_service.api
EOF

if [ "$actual" != "$expected" ]; then
  echo "FAIL: summarizer output mismatch" >&2
  echo "--- expected ---" >&2
  printf '%s\n' "$expected" >&2
  echo "--- actual ---" >&2
  printf '%s\n' "$actual" >&2
  exit 1
fi

# No attribute value may leak. Every string below lives in the fixture's
# `.change.before` / `.change.after` objects, which the filter never reads.
for needle in \
  "KGIS_KGCS_ENABLED" \
  "CANONICAL_NAMESPACE" \
  "NEO4J_URI" \
  "bolt://10.0.0.2:7687" \
  "super-secret-token" \
  "internal.example.invalid" \
  "542888988741"; do
  if printf '%s' "$actual" | grep -qF "$needle"; then
    echo "FAIL: summarizer leaked attribute value: $needle" >&2
    exit 1
  fi
done

# Entries that are not drift must be excluded entirely.
if printf '%s' "$actual" | grep -qE 'no-op|read'; then
  echo "FAIL: no-op/read entries should not be reported" >&2
  exit 1
fi

echo "PASS: terraform drift summarizer (3 changed resources, 0 attribute values)"
