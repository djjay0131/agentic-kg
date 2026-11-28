import { test, expect } from './support/fixtures';
import {
  WORKFLOW_E2E_TAG,
  authorizationHeader,
  cancelTaggedWorkflows,
  cancelWorkflowIfActive,
  waitForSelectProblemGate,
} from './support/helpers';

test('A2 starts a workflow, reaches the select_problem gate, then rejects it', async ({
  page,
}) => {
  test.info().annotations.push({
    type: 'paid-llm',
    description:
      'Starting a workflow runs the ranking agent, which makes paid LLM calls. One workflow per run.',
  });

  await page.goto('/workflows');
  await expect(
    page.getByRole('heading', { name: 'Research Workflows' }),
  ).toBeVisible();

  // A previous failed suite may have left this suite's runs at a gate. They
  // carry the tag below; clean only those, never a human's pending gate.
  await cancelTaggedWorkflows(page);

  let runId: string | undefined;
  try {
    // Tag the run without changing its ranking filter: rewrite the UI's own
    // start request. The tag is what the next suite run sweeps if this suite
    // is cancelled before its finally block can run.
    await page.route('**/api/agents/workflows', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const body = JSON.parse(route.request().postData() || '{}');
      body.tag = WORKFLOW_E2E_TAG;
      await route.continue({ postData: JSON.stringify(body) });
    });

    const started = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/agents/workflows') &&
        response.request().method() === 'POST',
      { timeout: 120000 },
    );
    await page.getByRole('button', { name: /New Workflow/ }).click();

    const startResponse = await started;
    await page.unroute('**/api/agents/workflows');
    // Start is asynchronous now: 202 is the run row, not the finished ranking.
    expect(startResponse.status(), 'POST /api/agents/workflows').toBe(202);
    ({ run_id: runId } = (await startResponse.json()) as {
      run_id: string;
    });
    expect(runId).toBeTruthy();

    // The start failed banner must not be present.
    await expect(
      page.getByText('Failed to start workflow', { exact: false }),
    ).toHaveCount(0);

    // Wait until ranking has produced candidates and no problem is selected:
    // that is the first human gate.
    const state = await waitForSelectProblemGate(page, runId);
    expect(
      state.ranked_problems.length,
      'ranking should propose at least one problem',
    ).toBeGreaterThan(0);
    expect(state.selected_problem_id).toBeNull();
    expect(state.errors ?? []).toEqual([]);

    // The in-app detail page loads the same workflow without an error banner.
    await page.goto(`/workflows/${runId}`);
    await expect(page.getByRole('heading', { name: 'Workflow' })).toBeVisible();
    await expect(page.getByText(runId.slice(0, 12))).toBeVisible();
    await expect(
      page.getByText('Errors', { exact: true }),
    ).toHaveCount(0);

    // Clean up: reject at the selection gate. This ends the workflow and writes
    // nothing to the graph (selected_problem_id stays null).
    const rejected = await page.request.post(
      `/api/agents/workflows/${encodeURIComponent(
        runId,
      )}/checkpoints/select_problem`,
      {
        headers: authorizationHeader(),
        data: {
          decision: 'reject',
          feedback: 'post-deploy UI suite cleanup',
        },
      },
    );
    expect(rejected.status(), 'reject at select_problem').toBe(202);
    const final = (await rejected.json()) as {
      selected_problem_id: string | null;
      errors?: string[];
    };
    expect(final.selected_problem_id).toBeNull();
    expect(final.errors ?? []).toEqual([]);
  } finally {
    // A failing assertion above must not leave the run waiting at a gate.
    if (runId) await cancelWorkflowIfActive(page, runId);
  }
});
