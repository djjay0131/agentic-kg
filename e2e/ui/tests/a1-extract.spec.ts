import { test, expect } from './support/fixtures';
import { expectNoVisibleLoadError } from './support/helpers';

/**
 * A deliberately small abstract whose Limitations sentence states a concrete
 * gap, so a correctly wired extractor returns at least one problem. Keeping it
 * short bounds the paid-LLM cost of the run.
 */
const SHORT_ABSTRACT = [
  'Existing knowledge graph construction pipelines do not scale to heterogeneous scientific literature.',
  'In particular, they lack an automated method for aligning extracted problem statements across papers,',
  'which limits cross-paper synthesis and makes duplicate problems hard to detect.',
].join(' ');

test('A1.10 Extract From Text returns at least one problem (@paid-llm)', async ({
  page,
}) => {
  test.info().annotations.push({
    type: 'paid-llm',
    description:
      'Calls POST /api/extract, which invokes the configured paid LLM providers. Allowed for the post-deploy run.',
  });

  await page.goto('/extract');
  await page.getByRole('button', { name: 'From Text' }).click();

  await page.locator('input[type="text"]').first().fill(
    'E2E post-deploy extract check',
  );
  await page.locator('textarea').fill(SHORT_ABSTRACT);

  const extraction = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/extract') &&
      response.request().method() === 'POST',
    { timeout: 240000 },
  );
  await page.getByRole('button', { name: /Extract Problems/ }).click();

  const response = await extraction;
  expect(response.status(), 'POST /api/extract').toBe(200);

  const body = (await response.json()) as {
    success: boolean;
    problems_extracted: number;
    problems: unknown[];
    stages?: unknown[];
  };
  expect(
    body.success,
    `extraction reported failure; stages=${JSON.stringify(body.stages)}`,
  ).toBe(true);
  expect(
    body.problems_extracted,
    'extraction should yield at least one problem',
  ).toBeGreaterThanOrEqual(1);
  expect(body.problems.length).toBeGreaterThanOrEqual(1);

  await expect(page.getByText('Extraction Successful')).toBeVisible({
    timeout: 60000,
  });
  await expect(page.getByText(/Extracted Problems \(\d+\)/)).toBeVisible();
  await expectNoVisibleLoadError(page);
});
