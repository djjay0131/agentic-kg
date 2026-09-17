# agentic-kg — KGIS/KGCS Adoption & Migration Orchestration Plan

Date: 2026-09-17
Status: Proposed execution plan
Target: `djjay0131/agentic-kg`
Source platform: `djjay0131/agentic-kgis` + `djjay0131/agentic-kgcs`

## Mission

Adopt KGIS and KGCS underneath agentic-kg without a big-bang rewrite. Preserve or improve the research graph on real evidence before retiring the legacy path. Non-production GCP environments may be used throughout; production/master cutover remains an explicit owner gate.

Target flow:

`research sources -> KGIS -> Candidate + Evidence -> ledger -> KGCS -> CurationPlan -> executor -> canonical graph/epoch -> agentic-kg agents/retrieval`

KGIS/KGCS own reusable admission/curation. agentic-kg retains research-domain ontology mapping, application agents, retrieval, API/UI and deployment.

## Baseline

- agentic-kg currently has a bespoke Neo4j ingestion path in `packages/core/src/agentic_kg/ingestion.py`; its governance delta explicitly records KGIS/KGCS as future dependencies and says there are currently zero `kg_contracts` references.
- The 8-paper connected ground-truth chain at `packages/core/tests/extraction/fixtures/ground_truth_chain/` was built specifically for live importer validation. It records 10 verified citation edges and recurring concepts. Only `reconciled/` is authoritative; human/Claude files are evidence.
- Ground truth is incomplete and the segmenter backlog can confound recall. Do not score unreconciled material as negative evidence.
- Issue #58 is a real legacy defect: current smoke ingestion writes zero `CITES` edges while the other five graph-shape assertions pass.
- KGIS and KGCS v1 implementation is merged and their cross-repo E2E includes evidence-driven re-curation.
- Multiple GCP environments are available; use an isolated development/shadow environment and graph.

## Non-negotiable rules

1. No big-bang replacement.
2. Production remains authoritative until explicit cutover approval.
3. Shadow work never mutates production canonical state.
4. New canonical mutations flow only through KGCS `CurationPlan` + executor.
5. Preserve evidence/provenance end to end.
6. Domain ontology mapping stays in the adopter layer, not reusable KGIS/KGCS core.
7. Do not duplicate reusable KGIS/KGCS functionality in agentic-kg merely to ease migration.
8. Semantic quality beats node-count parity.
9. Recorded/replay LLM clients are mandatory in deterministic CI; live models belong in integration environments.
10. Every semantic task uses implementer -> independent reviewer -> fix loop -> tests -> PR -> CI.
11. Agents may manage and approve intermediate PRs only as current governance permits; never weaken branch protection, fabricate approvals, bypass checks, or merge production/master without authority.
12. Production promotion is a human-owner decision.

## Success / cutover gates

Before production cutover prove:

- typed KGIS Candidates + Evidence for required research semantics;
- KGCS canonicalization with conservative ER and governed LLM advice;
- meaningful evaluation against sufficiently reconciled gold, with honest-null treatment of missing labels;
- no material required-entity/edge regression and acceptable false-merge/false-split risk;
- citation semantics verified independently of legacy #58;
- evidence evolution/re-curation with history retained;
- current research agents/retrieval work against the canonical projection;
- GCP development deployment passes ingest -> curate -> query -> agent smoke;
- rollback to legacy production is tested;
- owner explicitly approves cutover.

## Orchestration roles

Top-level Orchestrator plus isolated-worktree subagents:

- Governance/Repository Steward
- Legacy Pipeline Archaeologist
- KGIS Integration Architect
- KGCS Integration Architect
- Ontology/Mapping Agent
- Ground-Truth/Evaluation Agent
- Segmenter/Corpus Readiness Agent
- Neo4j/Projection Adapter Agent
- Issue-58 Citation Investigator
- Research-Agent Compatibility Agent
- GCP Deployment Agent
- Security/Secrets Reviewer
- Independent Architecture Reviewer
- Independent Test/Adversarial Reviewer
- Release/Cutover Steward

Parallelize discovery and non-overlapping implementation; serialize shared seams.

## Branch/environment strategy

Create `integration/kgis-kgcs-adoption` from current `master`. Feature PRs target it during migration. Keep it current with master. Do not develop directly on master.

Use a dedicated GCP development/shadow environment. Isolate Neo4j canonical state, KGIS ledger/evidence, KGCS review/audit, service configuration and deployment manifests from production. Use existing secret management. Record agentic-kg/KGIS/KGCS commit SHAs in live-test reports.

## Phase 0 — Preflight and reproducible baseline

1. Refresh master, open PRs/issues, governance, memory bank, BACKLOG and deploy docs.
2. Record exact KGIS/KGCS package/tag/commit pins; do not float migration dependencies.
3. Run current unit/integration/governance gates; separate known-red from migration regressions.
4. Reproduce #58 or preserve latest reproducible evidence if an external service prevents it.
5. Inventory direct Neo4j write surfaces and current schema.
6. Create integration branch and migration status record under current governance.
7. Inspect current GCP deployment/environment mechanism before naming or changing environments.

Exit: baseline report, dependency pins, known-red list, integration branch, no production change.

## Phase 1 — Compatibility and mapping specification

Run four parallel analyses, then reconcile into one reviewed spec:

A. Trace every Paper/Topic/ResearchConcept/Model/Method/Problem/CITES and related write. Classify as source acquisition, extraction, candidate construction, curation, projection or application-only.

B. Map legacy outputs to KGIS candidate variants: stable semantic keys/aliases, evidence/source coordinates, score axes, validity, model/extractor/prompt versions and ontology requirements.

C. Map KGCS canonical identities/assertions to the Neo4j shape current application queries expect. Prefer a projection/compatibility adapter over domain pollution in KGCS.

D. Define deterministic legacy-ID <-> canonical-ID migration and collision handling. No silent identity fork.

Deliverable: migration mapping spec + ADR for durable adopter decisions. No implementation before review.

## Phase 2 — Evaluation corpus readiness

1. Audit all eight papers for human/Claude/reconciled completeness. Never fabricate missing reconciliation.
2. Resolve the named-resource question currently blocking importer diffing.
3. Disposition SEG-3/4/5/6/7. Implement prerequisites that materially affect scoring, or freeze/version the segmentation input and state which recall claims are invalid.
4. Finish a deterministic reconciled-fixture loader/runner.
5. Reuse `kg_eval` via a narrow provider/adapter where possible.
6. Measure entity and relation P/R, citation P/R, evidence validity, provenance completeness, false merge/split, abstention/review, calibration where supported, latency and LLM cost.
7. Exclude `acceptable_extras` from precision denominator per fixture contract.

Exit: deterministic evaluation report with counts and honest nulls.

## Phase 3 — KGIS shadow ingestion

1. Add pinned KGIS/KGCS dependencies behind an opt-in migration configuration.
2. Reuse existing source/PDF acquisition where sensible; adapt it to KGIS documents/structured inputs.
3. Configure extractors/builders for the domain types approved by the mapping spec.
4. Register quoted passage/source Evidence with stable coordinates.
5. Persist to isolated ledger/evidence stores only.
6. Recorded/replay LLM fixtures in CI; live provider only in GCP dev.
7. Compare candidates against legacy output and gold before allowing KGCS shadow writes.

Exit: reproducible candidate/evidence set; no production graph writes.

## Phase 4 — KGCS shadow curation and Neo4j projection

1. Implement or reuse conforming Neo4j GraphReader/GraphMutationStore adapter and run shared contract suites.
2. Configure research-paper curation profiles and conservative ER; prefer abstention/review over false merge.
3. Wire bounded KGCS advisers with recorded CI client and approved live dev provider.
4. Materialize only to isolated shadow canonical graph.
5. Build compatibility projection for existing Cypher/read expectations at a published curation epoch.
6. Prove application code cannot obtain a raw KGCS write surface.

Exit: KGIS -> ledger/evidence -> KGCS -> shadow Neo4j -> compatibility query passes.

## Phase 5 — Three-way shadow comparison + Issue #58

Run the frozen corpus through legacy and new paths and report:

`legacy vs gold | new vs gold | legacy vs new`

Investigate #58 in parallel. Never make `CITES=0` expected merely because legacy is broken. Gold/source verification is the oracle. Classify every material delta as new regression, legacy defect, gold ambiguity, expected architecture difference, improvement, or insufficient evidence.

Exit: no unexplained material differences.

## Phase 6 — Evidence-evolution acceptance

Use two connected papers touching the same concept/assertion:

1. Paper A -> curate -> epoch N.
2. Capture canonical/evidence/audit state.
3. Paper B adds corroborating/conflicting/superseding evidence.
4. Targeted CurationTrigger selects affected knowledge.
5. Deterministic baseline runs first; adviser is optional and evidence-citing.
6. Deterministic policy gates recommendation.
7. Execute -> epoch N+1.
8. Old assertion/history remains queryable; both papers' evidence remains traceable.
9. Replay recorded semantic decision and detect divergence.

Also run a non-paper synthetic fixture to prove reusable KGCS behavior is not hard-coded to research papers.

Exit: replay CI and live GCP dev both pass.

## Phase 7 — Application-agent/retrieval compatibility

1. Inventory Cypher/query assumptions in Ranking, Continuation, Evaluation, Synthesis, API and retrieval paths.
2. Run read-only compatibility tests against legacy and shadow graphs.
3. Add projection aliases/fields only when the mapping spec requires them.
4. Never expose ledger/provisional state to application reads.
5. Compare deterministic pre-LLM context/retrieved evidence for frozen research queries; do not demand exact live-LLM prose equality.

Exit: no blocking read-path regression.

## Phase 8 — GCP development deployment and soak

1. Deploy the integration branch to the designated non-production GCP environment using the repo's current deployment mechanism.
2. Pin and record exact three-repo SHAs.
3. Provision/isolate shadow graph + ledger/evidence + review/audit persistence.
4. Run migrations/bootstrap twice to prove idempotency.
5. Execute 8-paper ingest/curate/eval.
6. Execute evidence-evolution scenario.
7. Execute representative API/query/research-agent smoke tests.
8. Exercise retry, partial failure, LLM timeout/malformed output, stale-plan rejection and review-queue failure paths.
9. Observe latency, cost, queue depth, review rate, error rate and time-to-canonicalization.
10. Soak for an owner-approved interval or repeated batch count; do not invent a production SLA if none exists.

Exit: documented green development deployment with reproducible test report and no production mutation.

## Phase 9 — Cutover rehearsal

Create a release-candidate branch from the integrated result. Rehearse, in non-production:

- fresh install/deploy from nothing;
- legacy graph snapshot/export;
- new stores/bootstrap;
- migration/backfill of the chosen corpus;
- application reads switched to canonical projection;
- rollback to legacy read/write configuration;
- repeat migration without duplicate canonical state;
- audit/replay after restart.

Produce a cutover runbook with explicit stop/go conditions and rollback commands/procedures. Secrets and environment identifiers remain external/configured, not hard-coded.

Exit: rollback has been demonstrated, not merely documented.

## Phase 10 — Owner cutover gate

The orchestrator stops before production/master cutover and presents:

- semantic comparison report;
- gold coverage/completeness statement;
- #58 disposition;
- ER/curation risk metrics;
- GCP soak report;
- application compatibility report;
- unresolved review queue/backlog;
- dependency pins/releases;
- rollback rehearsal evidence;
- proposed legacy components to retire and those to retain temporarily.

Only the human owner decides whether to promote the integration/release candidate to production/master.

## Phase 11 — Post-cutover cleanup (only after owner approval)

After successful production observation:

1. Remove/deprecate legacy ingestion/curation code only when no rollback dependency remains.
2. Keep source acquisition or domain projection pieces that remain useful; do not delete code merely because it is old.
3. Remove duplicate LLM prompts/normalizers superseded by KGIS/KGCS.
4. Close/supersede migrated backlog items and #58 according to evidence.
5. Update design authority, memory bank, BACKLOG and deployment docs.
6. Run a final dependency-boundary audit: agentic-kg domain logic here; reusable graph logic in KGIS/KGCS.
7. Produce final migration reconciliation with release SHAs and deferred work.

## PR decomposition

Recommended PR sequence against `integration/kgis-kgcs-adoption`:

1. preflight/status + dependency pinning/config seam;
2. mapping spec/ADR;
3. evaluation runner + corpus-readiness changes (split segmenter changes if large);
4. KGIS shadow adapter;
5. Neo4j kg_contracts adapter + contract tests;
6. KGCS profile/curation wiring + projection;
7. three-way comparison/evaluation tooling + #58 fix or disposition;
8. re-curation acceptance fixtures/tests;
9. application-agent compatibility;
10. GCP dev deployment/config/smoke;
11. cutover rehearsal/runbook;
12. final integration reconciliation.

Keep semantic changes independently reviewable. If a cross-repo defect is found, fix it in KGIS/KGCS under that repo's governance rather than patching around it here.

## Agent approval/merge model

For intermediate feature PRs targeting the integration branch, the orchestrator may use independent reviewer subagents to review and approve/recommend merge when current GitHub/governance rules permit. The authoring agent must not be its own reviewer. Required checks must be green and all review findings resolved.

Do not change branch protection to make automation easier. Do not bypass required checks. Do not manufacture GitHub identity independence when all agents share the owner's token; preserve independence through separate reviewer passes/artifacts as governance describes.

The orchestrator may merge intermediate PRs into the integration branch only if current governance grants that authority. If governance reserves merge authority to the owner, queue owner-ready PRs instead. In either case, **master/production cutover is always held for explicit owner approval by this plan.**

## Required final report

At the stopping point before production cutover, provide:

- PRs/commits and merge order;
- exact KGIS/KGCS pins;
- test/CI/governance status;
- gold completeness and evaluation metrics;
- legacy-vs-new semantic delta table;
- #58 disposition;
- GCP deployment/soak evidence;
- evidence-evolution/replay result;
- application-agent compatibility result;
- open human-review cases;
- rollback rehearsal result;
- unresolved risks/deferred work;
- explicit recommendation framed as evidence and go/no-go criteria, with the final production decision left to the owner.
