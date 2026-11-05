---
title: "ADR-0012: Staging sign-in is Google IAP; human decisions carry a verified email"
nav_exclude: true
---

# ADR-0012: Staging sign-in is Google IAP; human decisions carry a verified email

Status: Proposed
Date: 2026-11-03

## Context

The staging UI and API were public, and the only human-attribution mechanism on
mutating decisions was an unauthenticated `X-User-ID` header:

- `POST /api/review/{id}` (nightly review queue)
- `POST /api/reviews/{id}/assign|unassign|resolve` (legacy review queue)
- `POST /api/research/gates/{cycle}/{workflow}` (research loop)

Anyone who could reach the service could approve a candidate, reject it, or
claim to be someone else. That is not acceptable before a second tester is
added.

The deploy identity can create Cloud Run services, IAM bindings and service
accounts, but it cannot create OAuth clients/brands programmatically. The
project may also be outside a Google organization, where IAP requires a custom
OAuth client.

## Decision

1. **IAP directly on the UI Cloud Run service, with no load balancer.** Enabling
   IAP on the `run.app` endpoint is the documented preferred path and avoids a
   load balancer. Terraform sets the `run.googleapis.com/iap-enabled` service
   annotation (the switch `gcloud run services update --iap` flips) because the
   pinned Google provider does not expose `iap_enabled`.

2. **The API is private, not public, and is reached server-side.** IAP issues a
   signed assertion per *resource*: the `aud` claim is the UI service's IAP
   audience, not the API's. A browser calling the API on a second `run.app`
   origin would need a second IAP login (and cross-origin cookies/redirects that
   do not work reliably). Instead:
   - the browser calls same-origin `/api/*` and `/health`;
   - a Next.js route handler forwards the request to the API with the original
     `x-goog-iap-jwt-assertion` header and a Google-signed ID token for the UI
     runtime service account;
   - Cloud Run IAM on the API allows only the UI service account, the API-key
     check Job and the CI deploy/vendor-key identities;
   - `allUsers` is removed from the API.

   The API still verifies the assertion itself. It never trusts the unsigned
   `X-Goog-Authenticated-User-*` headers, so a caller that somehow reaches the
   private API cannot forge a human.

3. **`current_user` is the one human-identity dependency.** In `AUTH_MODE=iap`
   it requires an ES256 assertion signed by IAP, with `iss =
   https://cloud.google.com/iap` and the audience configured in
   `IAP_AUDIENCE`; it returns the verified email. Missing/invalid assertions are
   401; a missing audience is 503 (fail closed). In `AUTH_MODE=dev` — the
   default only for local development and tests — it accepts `X-User-ID`.
   Unknown modes are treated as `iap`. The review and research-gates routers
   use `current_user` and record its return value as `decided_by`.

4. **The allowlist lives in Terraform.** `var.app_allowed_members` is a list of
   IAM members (`"user:djjay@vt.edu"` by default). Terraform grants each
   `roles/iap.httpsResourceAccessor` project-wide; per the IAP documentation,
   project-level principals apply to every IAP-secured resource in the project,
   and there is exactly one. Adding a tester is one tfvars entry plus an apply.
   `var.app_iap_enabled` gates the whole posture (staging true, prod unchanged
   by default).

5. **Machine callers use service-account identity or the existing API key.**
   - UI proxy → private API: the UI runtime SA's metadata ID token (audience =
     API URL), allowed by `roles/run.invoker`.
   - `check_api_key` Job: it runs as the API's runtime identity and now sends a
     metadata ID token with the `X-API-Key` call.
   - Smoke/E2E workflows: the CI reader SA mints an ID token
     (`token_format: id_token`) and passes it as `STAGING_ID_TOKEN`; the E2E
     fixtures attach it as `Authorization: Bearer`.
   - Rotation health checks: the deploy SA mints an ID token for `/health`.
   - `GET /api/auth/check` keeps the ADR-0007 API-key gate unchanged.

## Alternatives Considered

### IAP on both services, browser calls the API directly

Rejected. IAP audiences are per-service, and cross-origin IAP login in a browser
is not reliable without a shared domain (which requires a load balancer or
custom domain). Server-side proxying is the simpler correct option and also
keeps the API off the public internet.

### IAP on the UI only, API left public

Rejected. It protects decisions but leaves the data and the service reachable by
anyone, which is not "restrict the app to an allowlist".

### A load balancer with one domain and IAP at the edge

Rejected for now: extra cost and moving parts for one staging app. The UI
proxy gives the same single-origin effect without a load balancer.

### A custom OAuth app and brand managed in Terraform

There is no Terraform resource that creates an OAuth client, and the CI identity
lacks `clientauthconfig.clients.*`. That part is owner bootstrap (see the
runbook), not something to work around.

## Consequences

### Positive

- Every approval/rejection/assignment records a Google-verified email.
- The staging UI and API are reachable only by allowlisted humans (UI) and
  named service accounts (API).
- No new secret: IAP and ID tokens are short-lived and keyless.

### Negative / Tradeoffs

- The API is not directly usable by a human with `curl`; humans go through the
  UI. Machine callers must present an ID token or API key.
- WebSocket updates on the workflow page cannot traverse the Next.js proxy.
  The page already polls every 3 seconds, so staging uses polling; local
  development with `NEXT_PUBLIC_API_URL` keeps the WebSocket.
- Project-level `roles/iap.httpsResourceAccessor` grants access to all
  IAP-secured web apps in the project. There is one today; if that changes,
  move to per-service IAP IAM.

### Risks / Owner bootstrap

- Enabling IAP for the first time in a project without an OAuth brand/consent
  screen cannot be done by the CI service account. The first apply may stop at
  the UI service; the owner must enable IAP once in the Cloud Run console (or
  configure a custom OAuth client and run `gcloud iap settings set`), then
  re-run the Terraform apply. Exact commands are in
  `docs/operations/adding-a-tester.md` and the PR body.
- If `djjay@vt.edu` is outside the project's organization and the project uses
  the Google-managed OAuth client, the owner must make the OAuth audience
  external (custom client) before that user can sign in.

### Research-gates adoption

`feat/research-loop` has merged to `master`, so its
`POST /api/research/gates/{cycle}/{workflow}` router now uses
`agentic_kg_api.dependencies.current_user` exactly as the review routers do;
the local `_require_user` / `X-User-ID` path is gone, and the decision records
the verified email.

## Impacted Areas

- [x] Security/privacy
- [x] Integrations
- [x] Implementation
- [x] Operations

## Related Documents

- ADR-0006 (private staging Neo4j), ADR-0007 (machine-only secrets)
- `docs/operations/adding-a-tester.md`
- `infra/main.tf`, `packages/api/src/agentic_kg_api/iap.py`,
  `packages/ui/src/lib/upstream.ts`
