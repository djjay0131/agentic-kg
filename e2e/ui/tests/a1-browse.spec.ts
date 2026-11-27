import { test, expect } from './support/fixtures';
import {
  expectNoVisibleLoadError,
  graphNodePointCandidates,
  signedInEmail,
  statCardValue,
} from './support/helpers';

test.describe('A1 — dashboard, browsing and search', () => {
  test('A1.1 dashboard shows non-zero counts and an online graph', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: 'Dashboard' }),
    ).toBeVisible();

    const problems = await statCardValue(page, 'Total Problems');
    const papers = await statCardValue(page, 'Total Papers');

    expect(
      problems,
      'staging dashboard should report at least one extracted problem',
    ).toBeGreaterThan(0);
    expect(
      papers,
      'staging dashboard should report at least one source paper',
    ).toBeGreaterThan(0);
    await expect(page.getByText('Online', { exact: true })).toBeVisible();
  });

  test('sidebar shows the signed-in IAP identity (GET /api/auth/me)', async ({
    page,
  }) => {
    const email = await signedInEmail(page);

    await page.goto('/');
    const signedInBlock = page
      .locator('aside')
      .getByText('Signed in', { exact: true })
      .locator('..');
    await expect(signedInBlock).toContainText(email);
  });

  test('A1.2/A1.4–A1.6 problems list, status filter, pagination and detail', async ({
    page,
  }) => {
    await page.goto('/problems');

    const rows = page.locator('table tbody tr');
    await expect
      .poll(async () => rows.count(), { timeout: 40000 })
      .toBeGreaterThan(0);

    await expect(page.getByRole('button', { name: 'Previous' })).toBeVisible();
    const next = page.getByRole('button', { name: 'Next' });
    await expect(next).toBeVisible();

    if (await next.isEnabled()) {
      const paged = page.waitForResponse(
        (response) =>
          response.url().includes('/api/problems?') &&
          response.url().includes('offset=50'),
      );
      await next.click();
      expect((await paged).status()).toBeLessThan(500);
      await expect
        .poll(async () => rows.count(), { timeout: 40000 })
        .toBeGreaterThan(0);
      await page.getByRole('button', { name: 'Previous' }).click();
    }

    // Status filter issues a filtered request and renders without an error.
    const statusSelect = page.locator('select').first();
    const filtered = page.waitForResponse(
      (response) =>
        response.url().includes('/api/problems?') &&
        response.url().includes('status=open'),
    );
    await statusSelect.selectOption('open');
    expect((await filtered).status()).toBeLessThan(500);
    await expectNoVisibleLoadError(page);

    // Reset, then open one problem through its in-app detail route.
    await statusSelect.selectOption('');
    await expect
      .poll(async () => rows.count(), { timeout: 40000 })
      .toBeGreaterThan(0);

    const firstStatement = rows.first().locator('a').first();
    const statement = (await firstStatement.textContent())?.trim() ?? '';
    expect(statement.length).toBeGreaterThan(0);
    await firstStatement.click();

    await expect(page).toHaveURL(/\/problems\/[^/]+/);
    await expect(
      page.getByRole('link', { name: 'Back to problems' }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { level: 1 })).toContainText(
      statement.slice(0, 24),
    );
    await expect(page.getByText('Problem not found')).toHaveCount(0);
  });

  test('A1.2 papers list, pagination and in-app paper detail', async ({
    page,
  }) => {
    await page.goto('/papers');

    const rows = page.locator('table tbody tr');
    await expect
      .poll(async () => rows.count(), { timeout: 40000 })
      .toBeGreaterThan(0);
    await expect(page.getByText(/Showing \d+.\d+ of \d+ paper/)).toBeVisible();

    const next = page.getByRole('button', { name: /Next/ });
    await expect(next).toBeVisible();
    if (await next.isEnabled()) {
      const paged = page.waitForResponse(
        (response) =>
          response.url().includes('/api/papers?') &&
          response.url().includes('offset=50'),
      );
      await next.click();
      expect((await paged).status()).toBeLessThan(500);
      await expect
        .poll(async () => rows.count(), { timeout: 40000 })
        .toBeGreaterThan(0);
      await page.getByRole('button', { name: /Previous/ }).click();
    }

    await rows.first().locator('a').first().click();
    await expect(page).toHaveURL(/\/papers\/.+/);
    await expect(page.getByText(/DOI:/)).toBeVisible();
    await expect(page.getByText(/Extracted problems/)).toBeVisible();
    await expectNoVisibleLoadError(page);
  });

  test('A1.7 search returns results for a common term', async ({ page }) => {
    await page.goto('/problems');

    const searchResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/search') &&
        response.request().method() === 'POST',
      { timeout: 40000 },
    );
    await page
      .locator('input[placeholder="Search problems..."]')
      .first()
      .fill('knowledge graph');
    await page.getByRole('button', { name: 'Search' }).click();

    const response = await searchResponse;
    expect(response.status(), 'POST /api/search').toBe(200);
    const body = (await response.json()) as { results?: unknown[] };
    expect(
      body.results?.length ?? 0,
      'expected at least one result for "knowledge graph"',
    ).toBeGreaterThan(0);

    await expect
      .poll(async () => page.locator('table tbody tr').count(), {
        timeout: 40000,
      })
      .toBeGreaterThan(0);
    await expectNoVisibleLoadError(page);
  });

  test('A1.9 graph renders and a node click opens the side panel', async ({
    page,
  }) => {
    await page.goto('/graph');

    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible({ timeout: 40000 });
    await expect(page.getByText(/\d+ nodes/)).toBeVisible({ timeout: 40000 });

    // zoomToFit runs ~500 ms after data arrives; let the force layout settle.
    await page.waitForTimeout(2000);

    const candidates = await graphNodePointCandidates(canvas);
    expect(
      candidates.length,
      'the graph canvas should contain drawn nodes',
    ).toBeGreaterThan(0);

    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    const panel = page.locator('[aria-label="Close node panel"]');

    for (const point of candidates.slice(0, 32)) {
      await page.mouse.click(box!.x + point.x, box!.y + point.y);
      await page.waitForTimeout(150);
      if (await panel.isVisible().catch(() => false)) {
        await expect(panel).toBeVisible();
        await expect(
          page.getByRole('link', { name: /Open .* details/ }),
        ).toBeVisible();
        return;
      }
    }

    throw new Error(
      'clicked 32 rendered graph nodes but the side panel never opened',
    );
  });
});
