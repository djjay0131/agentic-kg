---
title: "ADR-0006: Staging Neo4j is private (VPC-only) with a repeatable password rotation"
nav_exclude: true
---

# ADR-0006: Staging Neo4j is private (VPC-only) with a repeatable password rotation

Status: Proposed
Date: 2026-10-05

## Context

The staging Neo4j database runs on a Compute Engine VM in GCP project
`vt-gcp-00042`. Two facts force a decision:

1. **The VM is on the public internet.** `infra/main.tf` attaches an
   ephemeral public IP and `google_compute_firewall.neo4j` allows
   `tcp/7474` and `tcp/7687` from `0.0.0.0/0` to the `neo4j-server`
   tag. The bolt and Browser endpoints were published in this
   repository's history (issue #79); the address cannot be un-published
   without a history rewrite, so the firewall must be the control.
2. **The owner has asked to rotate the credential** (2026-10-05) and
   wants the operation to happen "safely behind the scenes" for a
   GCP-hosted application.

The consumers of Neo4j are all GCP-side today:

- **Cloud Run API service** `agentic-kg-api-staging` (reads
  `NEO4J_URI` / `NEO4J_PASSWORD` from Secret Manager).
- **Cloud Run Job** `agentic-kg-ingest-staging` (same secrets).
- **GitHub-hosted CI runners** (`integration-tests.yml`) that connect
  directly to the database using the GitHub secrets
  `STAGING_NEO4J_URI` / `STAGING_NEO4J_PASSWORD`.
- The `random_password.neo4j` resource is the single origin of the
  password; Terraform writes it to Secret Manager and, when
  `sync_github_secrets = true`, to GitHub. That duplication is the
  reason a rotation today would leave one of the copies stale.

Terraform state is **local** (`infra/terraform.tfstate`, location not
recorded). No apply can be run by an agent; this ADR ships code and a
runbook for the owner to apply deliberately.

## Decision

**Staging Neo4j is reachable only from inside the VPC.**

1. **Ingress.** `google_compute_firewall.neo4j` source ranges become the
   region subnetwork CIDR (the default network's `default` subnet)
   instead of `0.0.0.0/0`, still scoped to the `neo4j-server` tag. An
   optional, separate rule allows `tcp/22` from the Google IAP range
   `35.235.240.0/20` only (for break-glass SSH); `7474`/`7687` are
   **never** open to the IAP range.
2. **Egress.** The API service and the ingest Job attach to the VPC via
   **Direct VPC egress** with `egress = "PRIVATE_RANGES_ONLY"`, so
   private-range traffic (Neo4j) traverses the VPC while calls to
   public APIs (OpenAI, Semantic Scholar, …) continue to use Cloud
   Run's managed egress. The Cloud Run service account is granted
   `roles/compute.networkUser` on the project.
3. **Address.** `NEO4J_URI` points at the VM's **internal** IP
   (`network_interface[0].network_ip`), not the NAT IP. Terraform
   outputs also report the internal address.
4. **CI.** GitHub-hosted runners lose direct database access. The
   least-machinery resolution:
   - the `integration-tests.yml` "Integration Tests" job runs against an
     **ephemeral testcontainers Neo4j** (already supported by
     `packages/core/tests/conftest.py`) instead of staging;
   - the "E2E Tests (Staging)" job targets the **deployed public API**
     (`packages/api/tests/e2e`) with only `STAGING_API_URL`. The API's
     `/health` response asserts `neo4j_connected`, which exercises the
     private path end to end;
   - the duplicated `STAGING_NEO4J_URI` / `STAGING_NEO4J_PASSWORD`
     GitHub secrets and their `github_actions_secret` resources are
     deleted. No VPC connector or Cloud Run Job test runner is built
     for CI.
5. **Rotation.** A `workflow_dispatch`-only workflow
   (`.github/workflows/rotate-neo4j-password.yml`, `environment:
   staging`, WIF) generates a strong password, masks it, stages it in a
   dedicated transport secret, runs an in-VPC rotation Cloud Run Job
   that executes `ALTER CURRENT USER SET PASSWORD FROM … TO …` and
   verifies a read with the new credential, promotes the new value to
   `NEO4J_PASSWORD`, rolls the API service and ingest Job, verifies via
   the API, and only then disables the superseded secret version.
6. **No destructive change.** `lifecycle { prevent_destroy = true }` is
   added to the Neo4j VM. The ephemeral public IP is retained for now so
   that applying this ADR cannot replace the instance; with the firewall
   closed the address is unreachable. Detaching it is a documented
   follow-up requiring a maintenance window (see Consequences/Risks).

## Rationale

- **The firewall is the only control that survives the history
  disclosure.** Authentication alone leaves an exposed attack surface;
  closing ingress removes it.
- **Direct VPC egress is a property of the resource, not a separate
  connector.** It is less machinery than a Serverless VPC Access
  connector and needs no extra network appliance.
- **One source of truth for the credential.** Deleting the GitHub copy
  makes Secret Manager authoritative, which is a precondition for
  rotation being idempotent.
- **Ordering is what avoids downtime.** VPC egress is deployed first
  (Cloud Run can still reach the public IP); then `NEO4J_URI` switches
  to the internal IP while ingress is still open; only then is the
  firewall closed. The runbook prescribes this with staged
  `terraform apply -target` calls.

## Alternatives Considered

### Alternative 1 — Keep the public IP, require strong auth only

Cheapest, but it preserves a globally reachable bolt endpoint for a
database whose address is already public. Rejected: it does not meet
"off the public internet", and it leaves brute-force/protocol traffic in
scope.

### Alternative 2 — Reach the private DB from CI over an IAP tunnel

`gcloud compute start-iap-tunnel <vm> 7687` would forward the bolt port
to the runner. It requires allowing `35.235.240.0/20` to **tcp/7687**,
which is exactly the ingress the ADR closes. Rejected.

### Alternative 3 — A Cloud Run Job-based CI test runner

Fully in-VPC and faithful to the existing core `e2e` suite, but it adds
an image entrypoint, a service-account dance, and result plumbing for
coverage that the API-level suite already exercises. More machinery than
the coverage is worth; rejected for now and recorded as a follow-up.

### Alternative 4 — Serverless VPC Access connector

The legacy path to VPC reachability for Cloud Run. Direct VPC egress is
the current mechanism and needs no connector instances or extra subnet.
Rejected as superseded.

## Consequences

### Positive

- Neo4j bolt/http are no longer reachable from the internet; the leaked
  address is inert.
- Cloud Run reaches Neo4j privately; public-API traffic is unchanged.
- Rotation has a single source of truth and an auditable, dispatch-only
  workflow that never logs the password.
- The `prevent_destroy` guard plus staged applies make an accidental
  instance replacement impossible to apply silently.

### Negative / Tradeoffs

- Local/manual database access now requires an IAP SSH session (or a
  bastion) rather than a direct bolt connection.
- Core `e2e` tests that assert on graph contents are no longer run in
  CI; they remain runnable locally against a private endpoint or a
  testcontainer.
- Rotation runs through a Cloud Run Job, so it depends on the job image
  being current (the `job` image is rebuilt on core changes).

### Risks

- **Residual public SSH.** The `default` network's auto-created
  `default-allow-ssh` rule still allows `tcp/22` from `0.0.0.0/0`.
  Terraform does not manage that rule. Removing or scoping it is a
  recommended follow-up outside this change; the new IAP rule does not
  override it.
- **Ephemeral public IP remains attached.** It is unreachable on
  `7474`/`7687` but still billed/attached. `neo4j_assign_public_ip =
  false` detaches it; applying that may replace the instance, so it must
  be done in a window with the `prevent_destroy` guard temporarily
  lifted.
- **`-target` applies** leave the state partially converged until the
  final full apply; the runbook requires the final convergence.

## Impacted Areas

- [x] Product
- [ ] Domain model
- [x] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [x] Integrations
- [ ] UX
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `docs/operations/neo4j-hardening-runbook.md` (operator runbook, data
  plane — projects this decision)
- `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`
- `.github/workflows/rotate-neo4j-password.yml`
- `packages/core/src/agentic_kg/rotate_password.py`

## Related Issues / PRs

- Refs #79 — staging Neo4j endpoint published in the public repo; the
  earlier scrub was partial.

## Supersedes

None.

## Superseded By

None.
