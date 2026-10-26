---
title: Nightly pipeline notifications
parent: Operations
nav_order: 7
---

# Nightly pipeline run notifications

The nightly Cloud Run Job writes exactly one structured JSON line per finished
run. Cloud Logging stores it as `jsonPayload`, and two Terraform-managed
log-based alert policies notify on it:

| Event | Log severity | Alert policy |
|---|---|---|
| `pipeline_run_completed` | `INFO` | Nightly pipeline completed |
| `pipeline_run_failed` | `ERROR` | Nightly pipeline failed |

The email summary includes the run id, status, papers seen/new, committed and
deferred counts, review-queue size, estimated LLM spend, failure count and a
link to `<UI base>/runs/<run_id>`. The application holds **no mail
credentials**; Cloud Monitoring owns the email notification channel.

## Change the recipient

The recipient is a Terraform variable, so it is changed by a reviewed PR and
applied through the normal `infra/` path (never by hand):

```hcl
# infra/envs/staging.tfvars
notify_email = "someone-else@vt.edu"
```

The default in `infra/variables.tf` is `djjay@vt.edu`. The channel is created
only when both `nightly_enabled` and `nightly_notify_enabled` are true
(`nightly_notify_enabled` defaults to `true`).

After the change is applied, the existing policy keeps pointing at the same
`google_monitoring_notification_channel.nightly_email` resource, whose
`email_address` label is updated. The recipient must confirm the subscription
email from Google Cloud Monitoring before mail is delivered.

## Add the mobile-app channel

The Cloud Monitoring mobile app uses the `mobile-app` notification channel
type. Add it beside the email channel and list it on both policies:

```hcl
resource "google_monitoring_notification_channel" "nightly_mobile" {
  count = var.nightly_enabled && var.nightly_notify_enabled ? 1 : 0

  project      = var.project_id
  display_name = "Nightly pipeline mobile (${var.env})"
  type         = "mobile-app"
  labels = {
    # The Monitoring mobile app pairs with this project; the label is the
    # project id the app is signed in to.
    project_id = var.project_id
  }
}
```

Then add `google_monitoring_notification_channel.nightly_mobile[0].name` to
the `notification_channels` list of `google_monitoring_alert_policy.nightly_run`.
Each operator installs the Cloud Monitoring app and enables notifications for
the project; there is no per-device token to manage in Terraform.

To route to Slack/PagerDuty instead, create that channel type the same way and
add it to the same list.

## Verify a notification end to end

1. Confirm the Job's structured line (Logs Explorer):

   ```
   resource.type="cloud_run_job"
   jsonPayload.event=("pipeline_run_completed" OR "pipeline_run_failed")
   ```

2. Trigger a manual run: **Actions → Run nightly pipeline now**, or

   ```bash
   gcloud workflows run agentic-kg-nightly-staging \
     --location=us-central1 --data='{"trigger":"manual"}' --format='value(state)'
   ```

3. The email arrives within a few minutes. If it does not, check **Monitoring →
   Alerting → Policies** for the two `Nightly pipeline … (staging)` policies and
   their incident history; a policy with no notification channel configured
   creates incidents without email.

## Disable notifications temporarily

Set `nightly_notify_enabled = false` in the environment's `.tfvars` and apply.
This removes both policies and the channel; the Job's structured log line is
unaffected.
