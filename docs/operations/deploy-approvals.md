---
title: Deploy approvals (who approves what)
parent: Operations
nav_order: 6
---

# Deploy approvals — who approves what

**Applies to:** the GitHub Actions workflows for the staging stack in GCP
project `vt-gcp-00042`. Decision record:
[ADR-0014](https://github.com/djjay0131/agentic-kg/blob/master/llm/governance/adr/0014-split-staging-deploy-approvals.md).

## Two staging environments

| GitHub environment | Who approves | What runs there |
|---|---|---|
| `staging` | No required reviewer | routine service/image deploys, nightly "run now", read-only diagnostics |
| `staging-infra` | Required reviewer (the repository owner) | Terraform `apply`, key rotation, credential rotation |
| `production` | Required reviewer | tagged production release |

A green CI run is the gate for routine staging deploys. A human decision is the
gate for changes to infrastructure, IAM, secrets and live credentials.

## Workflow → environment matrix

| Workflow | Job | Environment | Privileged? |
|---|---|---|---|
| `terraform.yml` | `plan` | *(none)* | read-only PR plan |
| `terraform.yml` | `apply` | `staging-infra` | yes — changes IAM, secrets, networking, Cloud Run config |
| `roll-vendor-keys.yml` | `roll` | `staging-infra` | yes — rolls consumers onto a new vendor key |
| `rotate-api-key.yml` | `rotate` | `staging-infra` | yes — generates and rolls the API key |
| `rotate-neo4j-password.yml` | `rotate` | `staging-infra` | yes — generates and rolls the Neo4j password |
| `deploy-master.yml` | `deploy-staging` | `staging` | no — image + labels only |
| `deploy-branch.yml` | `deploy` | `inputs.environment` (`staging` or `dev`) | no — image + labels |
| `nightly-run.yml` | `run` | `staging` | no — dispatches the nightly workflow |
| `gcp-diagnose.yml` | `diagnose` | `staging` | no — read-only |
| `deploy-tag.yml` | `deploy-production` | `production` | yes — production release |

The Terraform `workflow_dispatch` input named `environment` is **not** the
GitHub environment. It selects the Terraform target (state prefix
`agentic-kg/<environment>` and `envs/<environment>.tfvars`) and stays
`staging`.

## One-time owner setup (before merging the split)

**Do not let a privileged workflow run before this exists.** If
`staging-infra` is absent, GitHub auto-creates it without protection and the
first `terraform apply` or rotation would run ungated.

1. GitHub → **Settings** → **Environments** → **New environment** →
   `staging-infra`.
2. Under **Deployment protection rules**, enable **Required reviewers** and add
   the repository owner (`djjay0131` / `djjay@vt.edu`).
3. Save. Do not add a wait timer unless that is a deliberate policy change.

Verify the environment now has a reviewer:

```bash
gh api repos/djjay0131/agentic-kg/environments/staging-infra \
  --jq '.protection_rules'
```

The result must list a `required_reviewers` rule. If it is `[]` or the call is
404, stop and fix the environment before dispatching any privileged workflow.

### Environment-scoped secrets and variables

At the time of the split, the GCP secrets (`GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_SERVICE_ACCOUNT`) and `GCP_PROJECT_ID` are **repository-level**, and the
Workload Identity Federation binding is scoped to `attribute.repository`, not
`attribute.environment`. No environment-scoped copy is required for
`staging-infra`.

If those secrets or variables are ever moved to environment scope, copy them to
**both** `staging` and `staging-infra`; otherwise the privileged workflows will
fail to authenticate. Never paste a secret value into a chat, issue or PR.

## Why routine deploys do not need a human

`deploy-master.yml` runs only after a PR has passed the required `test (3.12)`
check, then runs the post-merge test job and builds the image. After the roll it
asserts that every deployed Cloud Run revision reports the merged commit in its
`commit` label (`scripts/assert_deploy_parity.sh`), so a deploy that silently
kept an old image fails the run.

The workflow's `integration-test` job is currently a placeholder; replacing it
with a real post-deploy suite is follow-up work. Until then the automated
guarantees are the PR test gate and the deployed-SHA parity assertion, not a
reviewer's inspection of staging.

## Related

- [Deploy paths (automatic vs manual)](deploy-runbook.html)
- [API key rotation](api-key-rotation-runbook.html)
- [Neo4j hardening and password rotation](neo4j-hardening-runbook.html)
- [Semantic Scholar key](semantic-scholar-key-runbook.html)
