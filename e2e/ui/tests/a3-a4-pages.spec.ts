import { test, expect } from './support/fixtures';
import { expectNoVisibleLoadError } from './support/helpers';

/**
 * A3/A4: Runs, Review and Research Loop must show real data or a *true* empty
 * state. A failed request is never rendered as an empty list (the pages were
 * changed to distinguish the two), so this asserts both the absence of the
 * error state and that the loading state settles. The shared fixture fails the
 * test if any /api/* response was >= 500.
 */
test.describe('A3/A4 — runs, review and research loop', () => {
  test('A3 Runs page loads with runs or an honest empty state', async ({
    page,
  }) => {
    await page.goto('/runs');
    await expect(
      page.getByRole('heading', { name: 'Nightly Runs' }),
    ).toBeVisible();

    await expect
      .poll(async () => page.getByText('Loading runs...').count(), {
        timeout: 40000,
      })
      .toBe(0);

    const loaded = page
      .locator('table tbody tr')
      .or(page.getByText(/No nightly runs yet/));
    await expect(loaded.first()).toBeVisible({ timeout: 40000 });
    await expectNoVisibleLoadError(page);
  });

  test('A3 Review page loads with items or an honest empty state', async ({
    page,
  }) => {
    await page.goto('/review');
    await expect(
      page.getByRole('heading', { name: 'Review Queue' }),
    ).toBeVisible();

    await expect
      .poll(async () => page.getByText('Loading review items...').count(), {
        timeout: 40000,
      })
      .toBe(0);

    const loaded = page
      .locator('table tbody tr')
      .or(page.getByText(/No review items match these filters/));
    await expect(loaded.first()).toBeVisible({ timeout: 40000 });
    await expectNoVisibleLoadError(page);
  });

  test('A4 Research Loop page loads with gates/cycles or honest empty states', async ({
    page,
  }) => {
    await page.goto('/research');
    await expect(
      page.getByRole('heading', { name: 'Research Loop' }),
    ).toBeVisible();

    await expect
      .poll(
        async () =>
          page.getByText(/Loading (gates|agreement|cycles)\.\.\./).count(),
        { timeout: 40000 },
      )
      .toBe(0);

    await expect(
      page
        .getByText(/No gates are waiting/)
        .or(page.getByText(/Select problem|Approve proposal|Validate result/))
        .first(),
    ).toBeVisible();
    await expect(
      page
        .getByText(/No agreement evidence recorded yet/)
        .or(page.locator('table tbody tr').first())
        .first(),
    ).toBeVisible();
    await expect(
      page
        .getByText(/No research cycles yet/)
        .or(page.locator('table tbody tr').first())
        .first(),
    ).toBeVisible();

    await expectNoVisibleLoadError(page);
  });
});
