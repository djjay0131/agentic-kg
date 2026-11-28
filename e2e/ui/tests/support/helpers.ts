import { expect, type Locator, type Page } from '@playwright/test';

/** Domain filter that tags workflows created by this suite for cleanup. */
export const WORKFLOW_E2E_TAG = 'e2e-post-deploy';

const TERMINAL_STATUSES = new Set([
  'completed',
  'failed',
  'rejected',
  'cancelled',
]);

/** Cancel one run if it is still live (a rejected/completed run is left alone). */
export async function cancelWorkflowIfActive(
  page: Page,
  runId: string,
): Promise<void> {
  const response = await page.request.get(
    `/api/agents/workflows/${encodeURIComponent(runId)}`,
    { headers: authorizationHeader() },
  );
  if (response.status() !== 200) return;
  const state = (await response.json()) as { status?: string };
  if (state.status && TERMINAL_STATUSES.has(state.status)) return;
  await page.request.delete(
    `/api/agents/workflows/${encodeURIComponent(runId)}`,
    { headers: authorizationHeader() },
  );
}

/**
 * Cancel workflows left at a gate by an earlier failed deploy suite.
 *
 * Only runs carrying this suite's domain tag are touched, so a human's own
 * pending gate is never swept away. This is what stops every deploy from
 * leaving two workflows at ``select_problem`` forever.
 */
export async function cancelTaggedWorkflows(page: Page): Promise<number> {
  const list = await page.request.get('/api/agents/workflows', {
    headers: authorizationHeader(),
  });
  if (list.status() !== 200) return 0;
  const workflows = (await list.json()) as {
    run_id: string;
    status: string;
    tag?: string;
  }[];
  let cancelled = 0;
  for (const workflow of workflows) {
    if (TERMINAL_STATUSES.has(workflow.status)) continue;
    if (workflow.tag !== WORKFLOW_E2E_TAG) continue;
    await page.request.delete(
      `/api/agents/workflows/${encodeURIComponent(workflow.run_id)}`,
      { headers: authorizationHeader() },
    );
    cancelled += 1;
  }
  return cancelled;
}

/** Bearer token for direct API calls made alongside the UI. */
export function authorizationHeader(): Record<string, string> {
  const token = process.env.E2E_IAP_JWT?.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** The verified identity the sidebar renders (GET /api/auth/me). */
export async function signedInEmail(page: Page): Promise<string> {
  const response = await page.request.get('/api/auth/me', {
    headers: authorizationHeader(),
  });
  expect(response.status(), 'GET /api/auth/me').toBe(200);
  const body = (await response.json()) as { email?: string };
  expect(body.email, 'GET /api/auth/me must return the IAP email').toMatch(/@/);
  return body.email as string;
}

/**
 * Read one dashboard stat card. The card is `<h3>title</h3><p>value</p>...`,
 * so the parent's first paragraph is the number.
 */
export async function statCardValue(
  page: Page,
  title: string,
): Promise<number> {
  const heading = page.getByRole('heading', { name: title, exact: true });
  await expect(heading).toBeVisible();
  const value = heading.locator('..').locator('p').first();
  await expect(value).toHaveText(/^\d+$/);
  return Number((await value.textContent())?.replace(/\D/g, ''));
}

/** Fail if any page shows the explicit "request failed" state. */
export async function expectNoVisibleLoadError(page: Page): Promise<void> {
  for (const text of [
    'Failed to load',
    'could not be loaded',
    'upstream API unavailable',
  ]) {
    await expect(page.getByText(text, { exact: false })).toHaveCount(0);
  }
}

export interface WorkflowState {
  run_id: string;
  status: string;
  current_step: string;
  ranked_problems: unknown[];
  selected_problem_id: string | null;
  errors: string[];
}

/**
 * Poll a freshly started workflow until the `select_problem` gate is reached.
 *
 * The gate node is a passthrough that LangGraph runs *after* the interrupt, so
 * `current_step` still says `ranking` while the workflow waits. The gate is
 * therefore the fact that ranking produced candidates and no problem has been
 * selected — the same inference `derive_pending_gate` uses for the research
 * cycle.
 */
export async function waitForSelectProblemGate(
  page: Page,
  runId: string,
  timeoutMs = 180_000,
): Promise<WorkflowState> {
  const deadline = Date.now() + timeoutMs;
  let last: WorkflowState | undefined;

  while (Date.now() < deadline) {
    const response = await page.request.get(
      `/api/agents/workflows/${encodeURIComponent(runId)}`,
      { headers: authorizationHeader() },
    );
    expect(
      response.status(),
      `GET /api/agents/workflows/${runId}`,
    ).toBe(200);
    last = (await response.json()) as WorkflowState;

    if (last.status === 'failed' || (last.errors?.length ?? 0) > 0) {
      throw new Error(
        `workflow ${runId} failed before its first gate: ${JSON.stringify(
          last.errors,
        )}`,
      );
    }
    if ((last.ranked_problems?.length ?? 0) > 0 && !last.selected_problem_id) {
      return last;
    }
    await page.waitForTimeout(3000);
  }

  throw new Error(
    `workflow ${runId} did not reach the select_problem gate within ${timeoutMs}ms; ` +
      `last state: ${JSON.stringify(last)}`,
  );
}

export interface CanvasPoint {
  x: number;
  y: number;
}

/**
 * Find clickable node pixels in the force-graph canvas.
 *
 * The graph is drawn on a canvas, so there is no DOM node to click. Read the
 * pixels, match the three node colours, and return canvas-relative CSS
 * coordinates. Scanning is strided and de-duplicated per cell; the caller
 * retries candidates until the side panel opens.
 */
export async function graphNodePointCandidates(
  canvas: Locator,
): Promise<CanvasPoint[]> {
  return canvas.evaluate((element) => {
    const el = element as HTMLCanvasElement;
    const ctx = el.getContext('2d');
    if (!ctx) return [];

    let data: Uint8ClampedArray;
    try {
      data = ctx.getImageData(0, 0, el.width, el.height).data;
    } catch {
      return [];
    }

    const targets: [number, number, number][] = [
      [59, 130, 246], // problem
      [16, 185, 129], // paper
      [139, 92, 246], // topic / domain
    ];
    const rect = el.getBoundingClientRect();
    const scaleX = rect.width / el.width;
    const scaleY = rect.height / el.height;
    const seen = new Set<string>();
    const points: CanvasPoint[] = [];

    for (let py = 0; py < el.height; py += 2) {
      for (let px = 0; px < el.width; px += 2) {
        const i = (py * el.width + px) * 4;
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        const a = data[i + 3];
        if (a < 200) continue;

        const hit = targets.some(
          ([tr, tg, tb]) =>
            Math.abs(r - tr) <= 18 &&
            Math.abs(g - tg) <= 18 &&
            Math.abs(b - tb) <= 18,
        );
        if (!hit) continue;

        const cell = `${Math.floor((px * scaleX) / 24)}:${Math.floor(
          (py * scaleY) / 24,
        )}`;
        if (seen.has(cell)) continue;
        seen.add(cell);

        points.push({ x: px * scaleX, y: py * scaleY });
        if (points.length >= 48) return points;
      }
    }
    return points;
  });
}
