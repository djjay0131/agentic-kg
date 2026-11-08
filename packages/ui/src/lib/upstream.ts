/**
 * Server-side proxy to the private Cloud Run API (ADR-0012).
 *
 * The UI is the IAP-protected edge. The browser therefore calls same-origin
 * `/api/*` and `/health`; this module forwards the request to the API with:
 *   - the IAP signed assertion received from the edge, so the API can verify
 *     the human and record their email; and
 *   - a Google-signed ID token for the UI runtime service account, which is
 *     what satisfies Cloud Run IAM on the now-private API.
 *
 * Local development has no IAP and no metadata server: no assertion is
 * forwarded, and a development `X-User-ID` is injected only when AUTH_MODE is
 * not `iap` (staging configures `iap`, so a misconfigured UI still fails
 * closed at the API).
 */

import { NextRequest, NextResponse } from 'next/server';

const API_URL = (process.env.API_URL || 'http://localhost:8000').replace(/\/+$/, '');
const AUTH_MODE = (process.env.AUTH_MODE || 'dev').trim().toLowerCase();
const IAP_HEADER = 'x-goog-iap-jwt-assertion';
const DEV_USER = process.env.DEV_USER || 'dev@localhost';

const METADATA_IDENTITY_URL =
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity';

let tokenCache: { token: string; expiresAt: number } | null = null;

async function serviceAccountIdToken(audience: string): Promise<string | null> {
  if (tokenCache && Date.now() < tokenCache.expiresAt) {
    return tokenCache.token;
  }

  try {
    const url = `${METADATA_IDENTITY_URL}?audience=${encodeURIComponent(audience)}&format=full`;
    const response = await fetch(url, {
      headers: { 'Metadata-Flavor': 'Google' },
      cache: 'no-store',
    });
    if (!response.ok) {
      return null;
    }
    const token = (await response.text()).trim();
    if (!token) {
      return null;
    }
    // ID tokens last an hour; refresh well before that.
    tokenCache = { token, expiresAt: Date.now() + 5 * 60 * 1000 };
    return token;
  } catch {
    // Local development (and any non-GCP runtime) has no metadata server.
    return null;
  }
}

export async function proxyToApi(request: NextRequest): Promise<NextResponse> {
  // Preserve the original path encoding (IDs are already percent-encoded by
  // the client); re-encoding `params.path` would double-encode them.
  const target = `${API_URL}${request.nextUrl.pathname}${request.nextUrl.search}`;

  const headers = new Headers();
  for (const name of ['content-type', 'accept']) {
    const value = request.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }

  const assertion = request.headers.get(IAP_HEADER);
  if (assertion) {
    headers.set(IAP_HEADER, assertion);
  } else if (AUTH_MODE !== 'iap') {
    headers.set('X-User-ID', DEV_USER);
  }

  const token = await serviceAccountIdToken(API_URL);
  if (token) {
    headers.set('authorization', `Bearer ${token}`);
  }

  const method = request.method.toUpperCase();
  const body = method === 'GET' || method === 'HEAD' ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      redirect: 'manual',
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json({ detail: 'upstream API unavailable' }, { status: 502 });
  }

  const responseHeaders = new Headers();
  for (const name of ['content-type', 'cache-control']) {
    const value = upstream.headers.get(name);
    if (value) {
      responseHeaders.set(name, value);
    }
  }

  return new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: responseHeaders,
  });
}
