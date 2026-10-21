# Feature: R-3 — Answer with provenance (`POST /api/query`) and the B0/B1/B2 evaluation

**Status:** SPECIFIED
**Date:** 2026-10-10
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** R-3 (LLM synthesis endpoint)
**Depends On:** KGIS/KGCS adoption seam (ADR-0004/0005, canonical graph in staging Neo4j);
agentic-kgps v0.5.0 (PA-AKG provenance service: ProvenanceService, ProvenanceRouter,
verify/correct, B0/B1/B2 harness, PROV-O export); ADR-0007 (machine-only secrets)
**Scope decision (owner, 2026-10-10):** option C — the live endpoint **and** the B0/B1/B2
numbers on the 8-paper ground-truth set.
**Evidence-path decision (owner, 2026-10-20):** published snapshot (C3/C4), not a copy of
evidence in Neo4j — it leaves the ingest write path unchanged and reuses KGPS
`ReadOnlyRegistry`.

**Build order (owner "go", 2026-10-20):** PR-1 contracts bump + canonical lookups (C1, C2);
PR-2 enforced API-key auth + key rotation (C5); PR-3 approved question set (C10); then PR-4
evidence snapshot + API read path (C3, C4); PR-5 generator/judge/endpoint + provenance router
(C6–C9); PR-6 eval runner + report + spot-check (C11, C12).

## Problem

agentic-kg can ingest papers into a curated canonical graph (KGIS → KGCS, namespace
`canon`), and agentic-kgps can explain, verify and correct answers against such a graph — but
nothing connects them. A researcher cannot ask research.ai a question and get an answer whose
every sentence is traceable to a canonical assertion and the exact source passage behind it.
R-3 has sat as "Partial Spec" since Sprint 01.

Repository analysis (2026-10-09) found four concrete blockers:

1. **No single-assertion lookup.** `Neo4jCanonicalGraphReader`
   (`packages/core/src/agentic_kg/migration/neo4j/reader.py`) has no `get_assertion` /
   `iter_assertions`, and the repo pins agentic-kgis 0.3.0 (`d5bab8f6`); KGPS needs
   `>=0.6.0` (kg_contracts 2.3.0: `get_assertion`, `supports_assertion_lookup`,
   `source_candidate_ids`, `superseded_by`, typed spans).
2. **Evidence is unreachable at request time.** The KGIS evidence registry
   (`ShadowStores.evidence`, `evidence.db`) lives on the ingest Job's GCS FUSE ledger mount
   (`/mnt/ledger/staging`) only. SQLite on FUSE is single-writer with no POSIX locks, so the API
   must not open the live file.
3. **No enforced auth.** `APIConfig.api_key` / `requires_auth` exist but nothing enforces them;
   a public endpoint that spends LLM calls cannot ship like that.
4. **No question-answering agent.** `SynthesisAgent` is problem synthesis, not QA.

## Goals

- `POST /api/query` live on **staging**, behind API-key auth: NL question → `GroundedAnswer`
  whose surviving sentences each cite canonical assertions that are ACTIVE, grounded, and whose
  cited evidence a **Claude** judge finds entailing (with the drop-the-evidence control).
- Generation by **OpenAI** (existing throttled client); verification by **Claude**
  (`AnthropicClient`) — a different model family, so the judge does not grade its own family.
- KGPS read endpoints mounted at `/api/provenance/*` (explain, chain, lineage, successors,
  impact, verify, PROV-O) over the same canonical graph + evidence snapshot.
- A committed **B0/B1/B2 report** on an **owner-approved, frozen** question set (~30
  questions over the 8-paper ground-truth chain), reported **as measured** — no target number.
- An owner **spot-check of 15 B2 answers** (stratified by verdict), giving the Claude judge's
  agreement with a human; the report states that agreement next to every judge-derived number.

## Non-Goals

- Production deployment (staging only this round).
- A durable KGCS semantic-audit sink (explanations will show `NO_CURATION_AUDIT`; separate spec).
- An NLI verifier or a learned router (KGPS wave 5 follow-ups).
- UI work beyond what the API returns.
- Persisting answers or user questions (KGPS ADR-0001: read-only; nothing stored).
- Nanopub packaging (needs owner keys/target).

## User Stories

- As a researcher, I want to ask "Which methods reduce hallucination in KG construction?" and
  get an answer where each sentence links to the assertion and the quoted passage behind it, so I
  can trust and cite it.
- As a researcher, I want the system to say "I can't support that from the graph" instead of
  inventing an answer.
- As the PI, I want B0/B1/B2 numbers on a fixed question set, with the judge's human agreement
  stated, so the PA-AKG claim is evidenced honestly in the paper.
- As an operator, I want every LLM call and verdict traceable (trace id, model, prompt version)
  without any evidence text or key reaching logs.

## Design Approach

### Components

| # | Component | Where | Notes |
|---|---|---|---|
| C1 | Contracts bump | root + `packages/core` `pyproject.toml` `[migration]` extra | agentic-kgis `@<v0.6.0 sha>`, add `agentic-kgps @<v0.5.0 sha>`; ADR-0004 CI import job asserts `import kgps` |
| C2 | Canonical reader lookups | `migration/neo4j/reader.py`, `store.py` | `get_assertion(id, options)` (namespace-scoped, history switches honoured), `iter_assertions()` (namespace-scoped stream), `capabilities().supports_assertion_lookup = True`; kg_contracts contract suite for both |
| C3 | Evidence snapshot | ingest `job_runner` + `ShadowStores` | after each run, `sqlite3.backup()` `evidence.db` → `evidence.snapshot.<run>.db` (temp name), then publish by writing a small pointer object `evidence.snapshot.json` `{path, run_id, sha256, created_at}`; keep last 3 snapshots |
| C4 | API read path | Terraform (`api` service) + `dependencies.py` | GCS FUSE **read-only** volume on the API (same bucket); `get_provenance_service()` reads the pointer, opens the pointed snapshot with KGPS `ReadOnlyRegistry` (`mode=ro`, per-thread); re-reads the pointer at most every 60 s and swaps the registry atomically |
| C5 | Auth | `dependencies.require_api_key` | constant-time compare of `X-API-Key` against `API_KEY` from Secret Manager; applied to `/api/query` and `/api/provenance/*`; `API_KEY` generated and rotated by a workflow (ADR-0007 rule 2), never seen |
| C6 | Generator | `agentic_kg/answering/generator.py` | OpenAI `structured_extract` → `GroundedAnswer`; prompt carries `[assertion_id] statement + quote` blocks; citations outside the supplied set are dropped before verification; `rewrite_sentence` for the correction loop |
| C7 | Judge | `agentic_kg/answering/judge.py` | `kgps.LLMJudgeVerifier` over `AnthropicClient` (strict single-JSON output, fails closed); model + prompt version in every verdict |
| C8 | Endpoint | `packages/api/.../routers/query.py` | route → generate → verify/correct → respond (see sample); per-request budget (max LLM calls, timeout); trace id propagated |
| C9 | Provenance router | `main.py` | `kgps.http.create_router(...)` at `/api/provenance` |
| C10 | Eval set | `packages/core/tests/extraction/fixtures/ground_truth_chain/questions.v1.yml` | drafted by Claude from gold quotes, **approved by owner**, then frozen (sha recorded) |
| C11 | Eval runner | `scripts/eval_answering.py` + workflow `eval-answering.yml` (manual) | runs `kgps.run_harness` with the C6 generator and C7 judge against staging graph+snapshot; writes `docs/evaluation/answering-b0-b1-b2-<date>.md` (derived view naming this spec) |
| C12 | Spot-check | same workflow, `sample` step | 15 B2 answers stratified by outcome (supported / corrected / abstained) exported for owner labelling; agreement computed from the labels file |

### Request flow

1. `require_api_key` (C5) → 401 without a valid key.
2. `ProvenanceRouter.retrieve(question, k)` over sparse + dense + graph retrievers built on
   `AssertionDocuments` (cached, rebuilt when the snapshot pointer or canonical epoch changes).
   Gate: ACTIVE + grounded, supersession followed. Empty → abstain response (200, `abstained: true`).
3. Generator (C6) produces a draft `GroundedAnswer` citing only routed ids.
4. `correct_answer(draft, svc, judge, retrieve=…, regenerate=…, max_rounds=2)` (KGPS ADR-0007):
   contradiction vetoes support; leaky (prior-knowledge) support is not credited; unsupported
   sentences are re-cited, rewritten, or dropped; nothing left → abstain.
5. Response: sentences with citations and per-sentence verdicts, routing decision and dropped
   ids, `trace_id`, model ids and prompt versions, links to `/api/provenance/assertions/{id}/explain`.
6. Nothing persisted. Telemetry: KGPS `kgps.*` spans (ids/counts only).

## Sample Implementation

See the sketch approved by the owner on 2026-10-10 (reproduced):

```python
router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])

@router.post("/query")
def query(req: QueryRequest, svc: ProvenanceService = Depends(get_provenance_service)) -> QueryResponse:
    routed = get_router(svc).retrieve(req.question, k=req.k or 8)       # ACTIVE + grounded only
    if not routed.items:
        return QueryResponse.abstain(reason="no grounded assertions for this question", routed=routed)
    passages = [(i.assertion_id, evidence_text_for(svc, i.assertion_id)) for i in routed.items]
    draft = generator.generate(req.question, passages, trace_id=req.trace_id)     # OpenAI
    draft = drop_unknown_citations(draft, allowed={i.assertion_id for i in routed.items})
    judge = LLMJudgeVerifier(anthropic_complete, model_id=settings.judge_model)    # Claude
    fixed = correct_answer(draft, svc, judge,
                           retrieve=lambda s: [i.assertion_id for i in get_router(svc).retrieve(s, 4).items],
                           regenerate=generator.rewrite_sentence, max_rounds=2)
    return QueryResponse.from_correction(fixed, routed)

def get_provenance_service() -> ProvenanceService:
    reader = get_canonical_reader()                                  # + get_assertion (C2)
    evidence = snapshot_registry()                                   # pointer → ReadOnlyRegistry (C3/C4)
    return ProvenanceService(catalog_for(reader), evidence)
```

## Edge Cases & Error Handling

### No grounded assertions for the question
- **Scenario**: the gate drops everything (new topic, or all hits ungrounded/revoked).
- **Behavior**: 200, `abstained: true`, reason, routing decision and dropped ids; no LLM call.
- **Test**: question over an empty/ungrounded fixture graph → abstain with zero generator calls.

### Generator cites ids it was not given / no citations
- **Behavior**: unknown citations removed before verification; an uncited sentence is treated as
  unsupported (`drop_uncited=True`).
- **Test**: stub generator returns a foreign id → it never reaches the response.

### Judge unavailable / malformed output
- **Behavior**: `UNVERIFIABLE` (fails closed) → sentence unsupported → corrected or dropped; the
  response marks `judge_degraded: true`; never "verified" by default.
- **Test**: judge raising / returning prose → no sentence marked supported.

### Evidence snapshot missing, stale or mid-publish
- **Behavior**: pointer absent → `/api/query` returns 503 `evidence_unavailable`; pointer names a
  file whose sha256 does not match → keep the previous registry and log; snapshot older than
  48 h → response flag `evidence_stale: true`.
- **Test**: pointer swap during concurrent requests; corrupted snapshot; no pointer.

### Canonical store failure
- **Behavior**: KGPS turns store errors into `STORE_ERROR` gaps (ADR-0003); the gate drops such
  items; if routing itself fails → 503 with trace id.

### Oversized / adversarial question
- **Behavior**: 2,000-char limit (422); question text is sent to the LLMs only inside fenced
  blocks; judge output must be exactly one JSON object (prompt-injection resistance).

### Cost / latency runaway
- **Behavior**: per-request budget (default ≤ 1 generation + 2 rewrites + 2×(sentences×citations)
  judge calls, hard cap 40 LLM calls, 60 s); budget exhaustion → remaining sentences
  `UNVERIFIABLE` → dropped; response flags `budget_exhausted`.

## Acceptance Criteria

### AC-1: Contracts bump without regressions
- **Given** agentic-kgis pinned at v0.6.0 and agentic-kgps at v0.5.0 in the `[migration]` extra
- **When** CI runs
- **Then** the canonical-adapter contract suite, shadow-ingestion suite and unit tests pass, and
  the ADR-0004 import job imports `kgps`.

### AC-2: Canonical lookups
- **Given** the Neo4j canonical store with assertions in namespaces `canon` and `other`
- **When** `get_assertion(id)` / `iter_assertions()` are called on a `canon` reader
- **Then** only `canon` records are returned, superseded/revoked ones honour the read options,
  `capabilities().supports_assertion_lookup` is True, and the kg_contracts contract tests pass.

### AC-3: Evidence snapshot publication
- **Given** an ingest run
- **When** it finishes
- **Then** a consistent snapshot (backup API) and a pointer with a matching sha256 exist; at most
  3 snapshots are kept; a crash mid-publish leaves the previous pointer valid.

### AC-4: API reads only the snapshot, read-only
- **Given** the API with the ledger bucket mounted read-only
- **When** the pointer changes
- **Then** within 60 s new requests use the new snapshot, in-flight requests finish on the old
  one, and no code path opens `evidence.db` or any file for writing.

### AC-5: Auth enforced
- **Given** `API_KEY` in Secret Manager
- **When** `/api/query` or `/api/provenance/*` is called without / with a wrong / with the right
  `X-API-Key`
- **Then** 401 / 401 / 200; existing public read endpoints are unchanged.

### AC-6: Key generated and rotated without exposure (ADR-0007)
- **Given** the new `rotate-api-key` workflow (or a Cloud Run Job it triggers)
- **When** it runs
- **Then** a Cloud Run Job generates the new key inside GCP and stores it as a Secret Manager
  version; the workflow rolls the API and then triggers an in-GCP check Job (which reads the key
  from Secret Manager itself) that calls `/api/query` and reports only pass/fail; superseded
  versions are disabled only after that check passes; no value appears in any log, output,
  summary or runner.

### AC-7: Grounded answer end to end
- **Given** a fixture graph + snapshot with known assertions and stub OpenAI/Claude clients
- **When** `POST /api/query` is called
- **Then** every returned sentence cites only routed ids, each has a supporting check that is not
  leaky and no contradicting check, and `trace_id`, model ids and prompt versions are present.

### AC-8: Abstention and correction
- **Given** stubs that make one sentence unsupported and one contradicted
- **When** the endpoint runs
- **Then** the unsupported one is re-cited or rewritten or dropped, the contradicted one never
  survives, and an all-unsupported draft yields `abstained: true`.

### AC-9: Fail-closed judge and budgets
- **Given** a raising / malformed judge, or a budget of N calls
- **When** the endpoint runs
- **Then** no sentence is reported supported without a successful judge call, and call counts
  never exceed the budget.

### AC-10: Provenance endpoints mounted
- **Given** the API
- **When** `/api/provenance/assertions/{id}/explain`, `/chain`, `/prov` and `/answers/verify`
  are called with a valid key
- **Then** they return KGPS payloads over the canonical graph and snapshot (unknown ids →
  `UNKNOWN_ASSERTION` gap, 200).

### AC-11: Approved, frozen question set
- **Given** ~30 drafted questions, each with gold assertion/quote references
- **When** the owner approves (PR review)
- **Then** `questions.v1.yml` is committed with its sha recorded in the eval report; editing it
  requires a v2 file.

### AC-12: B0/B1/B2 report, as measured
- **Given** the approved set, staging graph + snapshot, OpenAI generator, Claude judge
- **When** `eval-answering.yml` runs
- **Then** `docs/evaluation/answering-b0-b1-b2-<date>.md` is committed with the KGPS harness
  table (incl. perturbation rows), model ids, prompt versions, question-set sha, run id — and
  **no** pass/fail threshold.

### AC-13: Human agreement
- **Given** 15 B2 answers sampled stratified by outcome and labelled by the owner
- **When** agreement is computed
- **Then** the report states per-sentence judge-vs-human agreement (and Cohen's κ) beside every
  judge-derived number; until the labels exist, those numbers are labelled "uncalibrated".

### AC-14: Staging smoke
- **Given** a deploy to staging
- **When** the smoke step asks one approved question with the key from Secret Manager
- **Then** it gets 200 with either a grounded answer or a well-formed abstention, within 60 s.

## Technical Notes

- Affected components: `packages/core` (`migration/neo4j`, `migration/ingestion/stores.py`,
  `job_runner.py`, new `answering/`), `packages/api` (`dependencies.py`, `main.py`, new
  `routers/query.py`), `infra/main.tf` (API read-only ledger volume, `API_KEY` secret +
  rotation job identity), workflows (`eval-answering.yml`, `rotate-api-key.yml`, smoke step).
- Patterns to follow: `canonical_read.open_canonical_reader` + `require_canonical_enabled`
  dependency gating; ADR-0006/0007 secret handling; ledger FUSE caveats in `stores.py`; KGPS
  ADR-0001/0003/0007/0008.
- Data model changes: none in Neo4j; new GCS objects (`evidence.snapshot.*.db`, pointer json).
- Feature flag: `QUERY_API_ENABLED` (default off; on in staging) alongside `CANONICAL_API_ENABLED`.

## Dependencies

- agentic-kgis v0.6.0, agentic-kgps v0.5.0 (released 2026-10-09).
- ADR-0007 rollout (#132/#133 + apply identity grant G1) for the key-handling pattern.
- Staging canonical graph populated by the KGIS→KGCS path (#134 live acquisition helps but is
  not required: the ground-truth papers can be ingested by DOI).

## Open Questions

Dual-persona review: the owner directed implementation to start after the evidence-path
decision; the remaining review questions are carried here and answered in the PR that touches
each area.

- (Tech lead) Is the 60 s pointer re-read on every request path cheap enough, or should the
  snapshot registry swap be driven by a background refresher? — decide in PR-4.
- (QA) How is the AC-4 "in-flight requests finish on the old snapshot" property tested without
  a real FUSE mount? — PR-4 uses a tmp-dir pointer swap under concurrent requests.
- (Tech lead) Per-request LLM budget defaults (40 calls / 60 s) — tune from PR-5 measurements;
  record the numbers, do not guess.
- (QA) How will a reviewer reproduce one `/api/query` answer from logs alone? — PR-5 logs the
  trace id, model ids, prompt versions and routed assertion ids (never evidence text).

- Should the ground-truth papers be (re)ingested into the staging canonical namespace before the
  eval, or into a dedicated `eval` namespace to keep runs reproducible? (proposal: dedicated
  `eval-gt-v1` namespace)
- Judge model id and pricing ceiling per eval run.
