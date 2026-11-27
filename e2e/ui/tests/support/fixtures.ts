import { expect, test as base } from '@playwright/test';

export interface ServerError {
  method: string;
  status: number;
  url: string;
}

/**
 * A page-scoped guard for the T54 requirement:
 *
 *   "fail on any /api/* response >=500 seen during the run".
 *
 * The UI renders explicit error states for non-2xx responses, but a failure is
 * still a failed deploy even if the page degrades gracefully. Recording the
 * responses (instead of only looking at the DOM) also catches background
 * requests the user never sees.
 */
export const test = base.extend<{ serverErrors: ServerError[] }>({
  // `auto: true` attaches the guard to every test in this suite, even though
  // no test names `serverErrors` in its fixture list.
  serverErrors: [
    async ({ page }, use) => {
      const serverErrors: ServerError[] = [];

      page.on('response', (response) => {
        const request = response.request();
        let pathname = '';
        try {
          pathname = new URL(response.url()).pathname;
        } catch {
          return;
        }
        if (pathname.startsWith('/api/') && response.status() >= 500) {
          serverErrors.push({
            method: request.method(),
            status: response.status(),
            url: response.url(),
          });
        }
      });

      await use(serverErrors);

      expect(
        serverErrors,
        `Saw /api/* responses >= 500 during the run:\n${JSON.stringify(
          serverErrors,
          null,
          2,
        )}`,
      ).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
