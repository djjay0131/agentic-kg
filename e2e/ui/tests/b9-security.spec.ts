import { test, expect } from './support/fixtures';

/**
 * B9: an unsigned request must never be able to approve anything.
 *
 * IAP sits in front of the UI, so an unauthenticated POST is rejected at the
 * edge. `maxRedirects: 0` prevents a browser-login redirect from turning this
 * into a misleading 200 from accounts.google.com.
 */
test('B9 unauthenticated POST is rejected with 401 or 403', async ({
  playwright,
  baseURL,
}) => {
  expect(baseURL, 'baseURL is supplied by playwright.config.ts').toBeTruthy();

  const context = await playwright.request.newContext({
    baseURL,
    maxRedirects: 0,
    extraHTTPHeaders: { Accept: 'application/json' },
  });

  try {
    const response = await context.post('/api/review/e2e-not-a-real-id', {
      data: { decision: 'approve', note: 'must never apply' },
      failOnStatusCode: false,
    });
    const location = response.headers()['location'] ?? '';
    expect(
      [401, 403],
      `unauthenticated POST /api/review/... returned ${response.status()}` +
        (location ? ` (redirected to ${location})` : ''),
    ).toContain(response.status());
  } finally {
    await context.dispose();
  }
});
