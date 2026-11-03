---
title: Adding a tester (staging Google sign-in)
nav_exclude: true
---

# Adding a tester to staging

Staging is protected by Identity-Aware Proxy (IAP) on the UI Cloud Run service
(ADR-0011). The API is private: the UI proxies every request server-side and
forwards the IAP-verified identity, so approvals and research-gate decisions are
recorded against a real Google email. There is no password and no shared user
id.

## The normal case: one line + apply

1. Add the person to `app_allowed_members` in
   `infra/envs/staging.tfvars`:

   ```hcl
   app_allowed_members = [
     "user:djjay@vt.edu",
     "user:new.tester@vt.edu",
   ]
   ```

   The default (when the variable is omitted) is just `user:djjay@vt.edu`.

2. Apply Terraform through the reviewed path:

   GitHub → **Actions** → **Terraform** → **Run workflow**
   - `environment`: `staging`
   - `apply`: `true`

   The apply grants `roles/iap.httpsResourceAccessor` to each member. Per the
   IAP documentation, a project-level principal applies to every IAP-secured
   resource in the project.

3. Ask the tester to open the UI URL in a fresh incognito window, sign in with
   the Google account you added, and confirm the sidebar shows that email.

Removing a tester is the same operation with the line deleted. The apply
revokes the role; there is no separate deprovisioning step.

## First-time owner bootstrap (only if IAP has never been enabled)

The CI apply identity can create Cloud Run and IAM resources, but it cannot
create an OAuth client/brand, and IAP cannot create one programmatically in a
project without an organization. If the first `app_iap_enabled = true` apply
stops at the UI service, the **owner** must do this once:

1. Open Cloud Run → `agentic-kg-ui-staging` → **Security**.
2. Select **Require authentication** → **Identity-Aware Proxy (IAP)** and save.
   If prompted, configure the OAuth consent screen (audience **External** for a
   personal/no-org project; **Internal** works only for same-org users).
3. Add `user:djjay@vt.edu` (and any testers) under the IAP policy, or let the
   next Terraform apply add them.
4. Re-run **Actions → Terraform → apply: true**. The annotation, IAM bindings
   and API env converge on the second run.

For a project without an organization, if the console asks for a custom OAuth
client, follow **Configure a custom OAuth client** in
<https://cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run>,
then apply the client with:

```bash
gcloud iap settings set iap_settings.yaml --project=vt-gcp-00042
```

If the apply itself stops with a permission error while enabling IAP (for
example `clientauthconfig.clients.create` or an IAP policy permission), the
owner grants the apply identity the missing role once, then re-runs the apply:

```bash
gcloud projects add-iam-policy-binding vt-gcp-00042 \
  --member=serviceAccount:gh-deploy@vt-gcp-00042.iam.gserviceaccount.com \
  --role=roles/iap.admin

# Only needed when granting access to users outside the organization:
gcloud projects add-iam-policy-binding vt-gcp-00042 \
  --member=serviceAccount:gh-deploy@vt-gcp-00042.iam.gserviceaccount.com \
  --role=roles/oauthconfig.editor
```

This is the only part an agent/CI identity must not work around. Do not grant
`allUsers` back to either service.

## Machine callers (for operators, not testers)

The API is private (`allUsers` removed) when `app_iap_enabled = true`. Automated
callers authenticate with a Google ID token whose audience is the API URL:

```bash
API_URL="$(gcloud run services describe agentic-kg-api-staging \
  --region=us-central1 --format='value(status.url)')"
TOKEN="$(gcloud auth print-identity-token --audiences="$API_URL")"
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_URL/health"
```

- The UI uses its runtime service account's metadata token.
- `check_api_key` does the same from inside GCP.
- CI smoke/E2E mints the token with `token_format: id_token` and passes it as
  `STAGING_ID_TOKEN`.
- `GET /api/auth/check` still requires `X-API-Key` (ADR-0007), independent of
  the human identity.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `403` from the UI `run.app` URL | The Google account is not in `app_allowed_members`, or the user is outside the OAuth brand's allowed audience. |
| Sidebar shows "Not signed in" | The UI proxy could not reach `/api/auth/me`; check the API is private and the UI service account has `run.invoker` on it. |
| `401 invalid IAP assertion` | The forwarded assertion's audience does not match `IAP_AUDIENCE` on the API; confirm the UI service name/region and re-apply. |
| Rollout fails for the API health probe | A rotation workflow ran before the API was redeployed with the IAM binding; re-run it. |
