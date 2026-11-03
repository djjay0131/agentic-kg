# agentic-kg staging test scripts

Working copy (live, commentable): https://claude.ai/artifact/TDebk9Din9sPKmnzkJcMyg. This file is the versioned audit-trail copy, first committed 2026-11-03. Record results in the working copy; changes to the scripts land here by PR.

## Before you start

These scripts test agentic-kg on staging: papers and research problems pulled from the literature, research agents that propose and evaluate next steps, and a nightly pipeline whose output humans review. Track A needs only a browser; Track B adds the API.

| What | Where |
| --- | --- |
| App (UI) | https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app |
| API docs (try requests in the browser) | https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/docs |
| Tester account | your @vt.edu Google account (allowlisted) |

**Sign-in.** Open the app and sign in with your vt.edu Google account. If you see "You don't have access", tell Jason; your email has not been added yet. Every approve or reject you make is recorded under your signed-in email.

**Scope.** Staging is a test environment. Data can be reset, and runs may use paid LLM calls, so start agent workflows only where a script says to.

**Reporting.** For each step, mark Pass or Fail in the results log at the end. On a Fail, leave a comment on that step with what you saw, the time, and a screenshot if you can. Comments on this doc reach Jason.

**Not ready yet (skip if missing):** the Research Loop page (A4) and the nightly-run email. Each is marked in its section.

## A1. Browse the knowledge graph

Pass means every page loads in under 10 seconds with real content and no error banners.

| # | Do | Expect |
| --- | --- | --- |
| A1.1 | Open the home page | Dashboard loads with counts for papers and problems (not zero) |
| A1.2 | Open **Papers**; open any paper | Title, DOI, authors, year; its extracted problems are listed |
| A1.3 | On a paper, follow a citation or reference | Lands on the cited paper, or shows it as an external reference |
| A1.4 | Open **Problems**; filter by a topic | List narrows to that topic; the count changes |
| A1.5 | Open any problem | Statement, assumptions, constraints, datasets, metrics (some may be empty) and an evidence quote with the source paper |
| A1.6 | Click the evidence's source paper | Opens the paper the quote came from |
| A1.7 | Search for "knowledge graph construction" | Ranked problems or papers appear; the top results are on topic |
| A1.8 | Search for a nonsense word ("zzqxv") | An empty-state message, not an error |
| A1.9 | Open **Graph** | A network renders; clicking a node shows its neighbours |
| A1.10 | Open **Extract**; paste one paragraph from any paper's Limitations section | Problems are extracted from the text you pasted, each with a quote |

While browsing, note anything that looks wrong: a problem that isn't really a research problem, a quote that doesn't support its problem, or duplicates of the same problem. Those notes are evaluation data, not just bug reports.

## A2. Run a research-agent workflow

One workflow walks a problem through four agents: ranking, continuation, evaluation and synthesis. It pauses at three checkpoints for you to decide. Run **one** workflow per test session; each one makes paid LLM calls and may start a sandboxed experiment.

1. Open **Workflows** and start a new workflow. Expect: it shows a running status, then pauses at **Select problem** with a ranked list.
2. Check the ranking. Expect: each problem has a score and a short reason; the reasons refer to the problem's evidence or data.
3. Pick one problem and continue. Expect: the continuation agent proposes a next step (an experiment, an extension or an analysis) that names datasets, metrics and baselines.
4. At **Approve proposal**, read it critically and then approve it, adding a note. Expect: your decision and note appear in the workflow history under your email.
5. Wait for evaluation (up to 5 minutes). Expect: generated code ran in the sandbox and returned results or a clear failure reason; the result records an experiment id and code hash.
6. At **Review evaluation**, approve it if the result is plausible, otherwise reject it with a reason. Expect: an approval runs synthesis; a rejection ends the workflow without writing anything.
7. After synthesis, open the problem again. Expect: the outcome is attached to the problem (refinement or result), or it is waiting in the **Review** queue.
8. Start a second workflow and **reject** at Approve proposal. Expect: the workflow ends and nothing is written to the graph.

Record, for steps 3 and 5, whether you would actually do the proposed next step (yes / maybe / no) and why. That judgement is research data for the paper.

## A3. Nightly runs and the review queue

Every night at 02:30 Eastern the pipeline searches for new papers, ingests them, and has an adviser grade what it found. Nothing the adviser grades enters the curated graph until a human approves it.

| # | Do | Expect |
| --- | --- | --- |
| A3.1 | Open **Runs** | One row per night: date, status, papers seen / new / committed / deferred, LLM spend, review-queue size |
| A3.2 | Open the most recent run | Totals, the queries it ran, failures (if any) with reasons, and proposed new queries |
| A3.3 | Click the run's review-queue count | Opens **Review**, filtered to that run |
| A3.4 | In **Review**, open one pending item | The candidate, the adviser's verdict (admit / reject / merge) with confidence and rationale, and quoted evidence text |
| A3.5 | Approve an item whose evidence supports it, with a note | Status becomes Approved under your email; the item now appears in the curated graph |
| A3.6 | Reject an item whose evidence does not support it, with a note | Status becomes Rejected under your email; nothing is added to the graph |
| A3.7 | Re-open an item you decided | The decision is read-only; there is no second approve |
| A3.8 | Back on the run page | The adviser–human agreement figure has updated |
| A3.9 | (When the email is live) check your inbox after a nightly run | A summary email with a link that opens that run |

If **Runs** is empty, the nightly job has not run yet. Mark A3 "blocked", not "fail". For each item you decide in A3.5 and A3.6, say whether you agreed with the adviser. Agreement is the evidence for later letting the adviser decide some items itself.

## A4. Research loop gates (not ready yet)

The research loop runs A2 on a schedule. Each cycle picks the top 3 problems and stops at every human gate. In this release every gate needs a human; nothing runs past a gate unattended. Skip this section until Jason says the **Research Loop** page is live.

1. Open **Research Loop**. Expect: the current cycle, its budget, the selected problems and the gates waiting for you.
2. Approve one **Select problem** gate, edit one **Approve proposal** before approving (change a dataset or metric), and reject one. Expect: each decision is recorded with your email and note. The edited proposal is what runs next.
3. After the next cycle, open the cycle report from **Runs**. Expect: a row marked `research_cycle` with what each workflow did and its spend.
4. Leave a gate undecided for a day. Expect: it is still waiting, unchanged, on the next cycle. It must not time out into an approval.

## B. Technical track (API)

Use the API docs page (link above) while signed in: open an endpoint, click **Try it out**, then **Execute**. Every check below is read-only except B10.

| # | Request | Expect |
| --- | --- | --- |
| B1 | `GET /health` | `status: ok`, `neo4j_connected: true` |
| B2 | `GET /api/problems?limit=5` | 5 problems, each with evidence and a source paper DOI |
| B3 | `GET /api/search?q=knowledge%20graph` | Ranked results with scores |
| B4 | `GET /api/canonical/summary` | An epoch number and counts of ACTIVE identities by type (Papers at minimum) |
| B5 | `GET /api/canonical/entities?type=Paper`, then one by id | Each identity has a DOI alias, title, assertions and evidence references |
| B6 | `GET /api/runs`, then `GET /api/runs/{run_id}` | The same totals the Runs page shows; status, budget, deferral reasons, proposed queries |
| B7 | `GET /api/review?status=pending` | The same items as the Review page; each carries evidence text and adviser model and prompt versions |
| B8 | After A2, `GET /api/agents/outputs/{output_id}/provenance` | A provenance envelope: the assertion and evidence ids the agent used, plus `derived_from` links to earlier agent outputs |
| B9 | `POST /api/review/{id}` with no sign-in (e.g. curl from a terminal) | `401`. An unsigned request must never approve anything |
| B10 | Ask Jason to trigger a nightly run, then repeat B6 | A new run appears with today's date; the email arrives (once live) |

B9 is the security check: try it once, and report any response other than 401 or 403 straight away.

## Known limitations

- The curated graph holds only Paper identities until reviewers approve items in the queue (A3), so curated-graph views may look sparse at first.
- Research agents read the older graph by default; the curated-graph switch comes after the first round of reviews.
- Relations whose endpoints need entity resolution are not queued yet, and "merge into" verdicts are recorded but not executed.
- Semantic Scholar rate limits can leave some citation links missing on newly ingested papers.

## Results log

Results are recorded in the working copy (link at the top), one row per section: Result (Not run / Pass / Fail / Blocked), failing steps, date tested, notes.
