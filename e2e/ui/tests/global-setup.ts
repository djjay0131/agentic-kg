/**
 * Fail before any test runs if the suite was invoked without its two inputs.
 *
 * A skipped or unauthenticated suite is worse than no suite: it looks green
 * without asserting anything about staging. The workflow also guards these
 * with `: "${VAR:?}"`; this is the in-process belt-and-braces check.
 */
export default async function globalSetup(): Promise<void> {
  const missing: string[] = [];
  if (!process.env.E2E_UI_URL?.trim()) {
    missing.push('E2E_UI_URL (the staging UI run.app URL)');
  }
  if (!process.env.E2E_IAP_JWT?.trim()) {
    missing.push(
      'E2E_IAP_JWT (short-lived service-account JWT minted through Workload Identity Federation)',
    );
  }
  if (missing.length > 0) {
    throw new Error(
      `post-deploy UI suite cannot run without: ${missing.join('; ')}. ` +
        'See e2e/ui/playwright.config.ts for how the workflow supplies them.',
    );
  }
}
