---
title: "ADR-0014: Gate infrastructure and key rotation, not routine staging deploys"
nav_exclude: true
---

# ADR-0014: Gate infrastructure and key rotation, not routine staging deploys

Status: Proposed
Date: 2026-11-27

## Context

Every workflow that touched staging declared the same GitHub environment,
`staging`. The environment was the single approval surface: a required reviewer
on `staging` therefore stopped routine service deployments as well as
infrastructure applies and secret rotations.

Those operations do not carry the same risk:

- **Routine staging deploys** roll an image that already passed the PR test
  gate and the post-merge test job. A human waiting to click *Approve* adds
  latency, not judgement: the reviewer cannot meaningfully inspect the merged
  commit faster than CI can assert the running revision.
- **Terraform `apply`** creates and changes IAM, networking, secrets, service
  accounts and Cloud Run configuration — changes a PR plan must be reviewed
  before they reach the project.
- **Key and credential rotation** changes the live API key, Neo4j password or a
  vendor key. A bad rotation can lock the API out of its data store or disable a
  credential that is still in use.

A single environment conflates these, so every routine deploy waits on the same
approval as an IAM change. The owner asked for routine staging deployments to
proceed on green CI while infrastructure and secret changes stay gated.

## Decision

1. **Two staging GitHub environments.**
   - `staging` — routine service deploys. No required reviewer.
   - `staging-infra` — infrastructure and secret changes. Required reviewer.

2. **Only the privileged workflows declare `staging-infra`:**

   | Workflow | Job | What it does |
   |---|---|---|
   | `terraform.yml` | `apply` | `terraform apply` for `infra/` |
   | `roll-vendor-keys.yml` | `roll` | rolls Cloud Run onto a new vendor key version |
   | `rotate-api-key.yml` | `rotate` | generates, rolls and verifies the API key |
   | `rotate-neo4j-password.yml` | `rotate` | generates, rolls and verifies the Neo4j password |

   `terraform.yml`'s read-only `plan` job stays ungated, so PR plans run
   automatically.

3. **Everything else keeps its existing environment.**

   | Workflow | Job | Environment |
   |---|---|---|
   | `deploy-master.yml` | `deploy-staging` | `staging` |
   | `deploy-branch.yml` | `deploy` | the dispatched `inputs.environment` (`staging` or `dev`) |
   | `nightly-run.yml` | `run` | `staging` |
   | `gcp-diagnose.yml` | `diagnose` | `staging` |
   | `deploy-tag.yml` | `deploy-production` | `production` |

4. **The Terraform `workflow_dispatch` input named `environment` is not the
   GitHub environment.** It selects the Terraform target environment (state
   prefix `agentic-kg/<environment>` and `envs/<environment>.tfvars`) and stays
   `staging`. Approval comes from the `apply` job's `staging-infra`
   environment.

5. **Routine staging deployments are verified automatically.** The gate is:
   the PR test job, the post-merge test/build jobs, and the post-deploy
   assertion that the running Cloud Run revision's `commit` label equals the
   merged SHA (`scripts/assert_deploy_parity.sh`). The deploy workflow's
   `integration-test` job is currently a placeholder; making it a real
   post-deploy test suite is tracked as follow-up work and does not change this
   decision.

## Rationale

The approval should attach to the *blast radius*, not to the word "staging".
Routine image rolls are reversible and already test-gated. IAM, secret and
credential operations are the changes where a human review of a plan or a
rotation reason adds value.

Splitting by environment also keeps the reviewer signal meaningful: an approval
request now means "an infrastructure or credential change is about to run",
not "another merge happened".

## Alternatives Considered

### Keep one `staging` environment and make routine deploys not use it

Removing `environment:` from `deploy-master.yml` would remove the gate but also
lose environment-scoped deployment history and any future environment-level
configuration for service deploys. A second environment keeps both.

### Gate only Terraform, leave rotations on `staging`

Rotation workflows run on a schedule or on demand and change live credentials.
Leaving them gated by the routine-deploy environment makes the two meanings
ambiguous; a rotation would either inherit the un-gated environment or keep
blocking routine deploys.

### Use a single environment with required reviewers disabled and rely on branch
protection

Branch protection does not cover scheduled or dispatched workflows, so a
rotation or apply could run without a reviewed commit or a human decision.

## Consequences

### Positive

- Routine staging deploys run on green CI without an approval click.
- Terraform apply and credential rotation remain behind a required reviewer.
- Approval requests carry an unambiguous meaning.

### Negative / Tradeoffs

- Two environments to configure and keep in sync.
- The owner must create `staging-infra` with a required reviewer **before** the
  workflows run against it.

### Risks

- **GitHub auto-creates a missing environment without protection.** If
  `staging-infra` does not exist when a privileged workflow first runs, GitHub
  creates it with no reviewer and the apply/rotation proceeds ungated. The PR
  that introduces this split must not merge until the owner has created the
  environment with a required reviewer.
- A future workflow could accidentally declare `staging-infra` and be
  unnecessarily gated, or declare `staging` and bypass the gate. A structural
  test pins the allowed set.

## Environment-scoped configuration

At the time of this decision, the repository's Workload Identity Federation
binding is scoped to `attribute.repository`, not `attribute.environment`
(`infra/main.tf`), and the GCP secrets and `GCP_PROJECT_ID` variable are
repository-level, not environment-level. No IAM or secret change is therefore
required to run under `staging-infra`. The owner should re-check the
environment's secrets and variables when creating it; if any are later moved to
the `staging` environment only, they must also exist on `staging-infra` or the
privileged workflows will not authenticate.

## Impacted Areas

- [ ] Product
- [ ] Domain model
- [ ] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [ ] Integrations
- [ ] UX
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `docs/operations/deploy-approvals.md`
- `docs/operations/deploy-runbook.md`
- `infra/README.md`
- `packages/core/tests/test_staging_environment_split.py`

## Related Issues / PRs

- PR: `chore(ci): gate only infra and key rotation; staging deploys run on green`

## Supersedes

None.

## Superseded By

None.
