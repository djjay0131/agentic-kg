---
title: Neo4j hardening runbook (private staging + password rotation)
nav_order: 2
parent: Operations
---

# Neo4j hardening runbook

Operator steps to (1) take staging Neo4j off the public internet and
(2) rotate its credential safely. Projects the decision in
[`ADR-0003`](https://github.com/djjay0131/agentic-kg/blob/master/llm/governance/adr/0003-staging-neo4j-private.md);
the two planes rule means the ADR is authoritative and this page is the
derived runbook.

**Project:** `vt-gcp-00042` (staging) · **Region:** `us-central1`
**Terraform state:** local (`infra/terraform.tfstate`) — the owner applies;
CI never does.

---

## Preconditions

- `gcloud` authenticated to the **`vt-gcp-00042`** VT (non-prod) project.
- Terraform `>= 1.5` on `PATH`, run from `infra/`.
- `roles/owner`-class access to the project (firewall, IAM, Secret Manager,
  Cloud Run).
- The `job` image has been rebuilt since the last core change (the rotation
  job runs `python -m agentic_kg.rotate_password` from that image).
- The GitHub environment `staging` has `GCP_WORKLOAD_IDENTITY_PROVIDER` and
  `GCP_SERVICE_ACCOUNT` set, exactly as `deploy-master.yml` uses them.
- **Do not** run these commands from an agent session or CI.

Capture the current state first:

```bash
gcloud compute firewall-rules describe allow-neo4j-staging \
  --project=vt-gcp-00042 --format='value(sourceRanges)'
gcloud secrets versions list NEO4J_PASSWORD \
  --project=vt-gcp-00042 --format='table(name,state,createTime)'
```

---

## Part 1 — Make Neo4j private (staged apply, zero downtime)

The order matters: **egress first, then the internal address, then close the
firewall.** Each stage is verified before the next.

### Step 1 — Add VPC egress and rotation infrastructure (firewall still open)

```bash
cd infra
terraform init -backend=false        # provider only; never against real state from CI
terraform plan -var-file=envs/staging.tfvars
```

Confirm the plan shows **no replacement** of `google_compute_instance.neo4j`
(`prevent_destroy` will refuse one) and no change to `NEO4J_URI` yet.

```bash
terraform apply -var-file=envs/staging.tfvars \
  -target=google_project_iam_member.network_user \
  -target=google_cloud_run_v2_service.api \
  -target=google_cloud_run_v2_job.ingest \
  -target=google_cloud_run_v2_job.rotate_password \
  -target=google_secret_manager_secret.neo4j_password_next
```

**Verify** the API still reaches Neo4j (it is still using the public URI,
now through the VPC path is not yet exercised):

```bash
curl -fsS "$(gcloud run services describe agentic-kg-api-staging \
  --region=us-central1 --project=vt-gcp-00042 --format='value(status.url)')/health"
# expect {"status":"ok","neo4j_connected":true,...}
```

**Rollback:** re-run `terraform apply` after reverting the skipped targets;
VPC egress is additive and harmless on its own.

### Step 2 — Switch `NEO4J_URI` to the internal address

```bash
terraform apply -var-file=envs/staging.tfvars \
  -target=google_secret_manager_secret_version.neo4j_uri \
  -target=google_cloud_run_v2_service.api \
  -target=google_cloud_run_v2_job.ingest
```

This creates a new Cloud Run revision whose `NEO4J_URI` is
`bolt://<internal-ip>:7687`, reached through the Direct VPC egress added in
Step 1. The **firewall is still open**, so even if egress were misconfigured
the old revision would still work.

**Verify** the API health again (same command). Confirm the new revision:

```bash
gcloud run services describe agentic-kg-api-staging --region=us-central1 \
  --project=vt-gcp-00042 --format='value(status.latestReadyRevisionName)'
```

**Rollback:** re-apply with the previous URI value (the NAT IP). The
firewall is still open, so this is instant.

### Step 3 — Close the firewall and remove the duplicated CI secrets

```bash
terraform apply -var-file=envs/staging.tfvars
```

This is the full convergence: it narrows
`google_compute_firewall.neo4j.source_ranges` to the subnetwork CIDR, adds
the optional IAP-SSH rule, and **deletes** the `STAGING_NEO4J_URI` /
`STAGING_NEO4J_PASSWORD` GitHub-secret resources.

Then delete the now-dangling repository secrets by hand (Terraform cannot
delete values it no longer manages):

```bash
gh secret delete STAGING_NEO4J_URI --repo djjay0131/agentic-kg || true
gh secret delete STAGING_NEO4J_PASSWORD --repo djjay0131/agentic-kg || true
```

**Verify** the port is closed from the internet and open from inside:

```bash
gcloud compute firewall-rules describe allow-neo4j-staging \
  --project=vt-gcp-00042 --format='value(sourceRanges)'   # expect the subnet CIDR, not 0.0.0.0/0
curl -v --max-time 5 "http://<old-nat-ip>:7474" ; echo "exit=$?"  # expect timeout/refused
```

**Rollback:** re-open the firewall only (`terraform apply` with
`neo4j_allowed_source_ranges=["0.0.0.0/0"]` **as a deliberate, temporary**
override), rotate the credential immediately afterwards.

---

## Part 2 — Rotate the password

### After Part 1 (the designed path)

Dispatch the workflow (it never runs on its own):

```bash
gh workflow run rotate-neo4j-password.yml \
  --repo djjay0131/agentic-kg \
  -f reason="post-hardening rotation"
gh run watch --repo djjay0131/agentic-kg
```

What the workflow does, in order: generates a `secrets.token_urlsafe(48)`
password and `::add-mask::`s it; stages it in `NEO4J_PASSWORD_NEXT`; runs
`agentic-kg-rotate-neo4j-staging` (inside the VPC) which executes
`ALTER CURRENT USER SET PASSWORD FROM $current TO $next` and reconnects with
the new credential to run `RETURN 1 AS ok`; promotes the value to a new
`NEO4J_PASSWORD` version; rolls the API service and ingest Job; verifies the
API health; and only then disables the superseded version and destroys the
transport version.

**Verify** independently:

```bash
curl -fsS "$(gcloud run services describe agentic-kg-api-staging \
  --region=us-central1 --project=vt-gcp-00042 --format='value(status.url)')/health"
gcloud secrets versions list NEO4J_PASSWORD --project=vt-gcp-00042 \
  --format='table(name,state,createTime)'   # exactly one ENABLED version
```

**Rollback:** re-enable the disabled version and point a fresh revision at
it, then disable the new one:

```bash
gcloud secrets versions enable <previous-version> --secret=NEO4J_PASSWORD \
  --project=vt-gcp-00042
gcloud run services update agentic-kg-api-staging --region=us-central1 \
  --project=vt-gcp-00042 --update-secrets=NEO4J_PASSWORD=NEO4J_PASSWORD:<previous-version> \
  --update-labels=neo4j-password-rolled-back=$(date +%s)
gcloud run jobs update agentic-kg-ingest-staging --region=us-central1 \
  --project=vt-gcp-00042 --update-secrets=NEO4J_PASSWORD=NEO4J_PASSWORD:<previous-version>
```

To complete the rollback, also set the *database* password back (run the
rotation job with the roles reversed) — or accept the new credential and
just re-point the services, which is the normal recovery.

### Before Part 1 (legacy / public endpoint)

Until Part 1 is applied, Neo4j is still reachable on the old NAT IP. The
same rotation can be run locally:

```bash
export NEO4J_URI="bolt://<old-nat-ip>:7687"
export NEO4J_PASSWORD="$(gcloud secrets versions access latest \
  --secret=NEO4J_PASSWORD --project=vt-gcp-00042)"
export NEO4J_PASSWORD_NEXT="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
.venv/bin/python -m agentic_kg.rotate_password
```

Then follow the workflow's promote → roll → verify → disable sequence by
hand. Do not paste either password into a terminal that records history.

---

## Part 3 — Verification queries

Run through IAP SSH (the new `allow-iap-ssh-neo4j-staging` rule permits
tcp/22 from `35.235.240.0/20`):

```bash
gcloud compute ssh agentic-kg-neo4j-staging --zone=us-central1-a \
  --tunnel-through-iap --project=vt-gcp-00042
```

On the VM, `cypher-shell` prompts for the password (it is never typed on the
command line):

```bash
cypher-shell -u neo4j
# at the prompt:
MATCH (n) RETURN count(n) AS nodes;
```

Or, without touching the database directly, use the API:

```bash
curl -fsS "<api-url>/api/stats"    # counts present → the private path is live
```

---

## Residual risks and follow-ups

- **`default-allow-ssh` is still open to `0.0.0.0/0`.** It is a default
  network rule Terraform does not manage. Remove or scope it, or add a
  higher-precedence deny, as a follow-up.
- **The ephemeral public IP is still attached** (unreachable on
  `7474`/`7687`). Detach it with `neo4j_assign_public_ip=false` in a window;
  it may replace the instance, so lift `prevent_destroy` for that apply.
- **The old address is in git history** and cannot be removed without a
  rewrite. The firewall is the control.
- **Core DB `e2e` tests no longer run in CI** (see ADR-0003 Consequences).
