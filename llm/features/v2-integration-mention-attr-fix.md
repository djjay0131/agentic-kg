# Feature: Fix V2 Entity Integration Blocked by Wrong Attribute (`.mentions` → `.mention_results`)

**Status:** SPECIFIED
**Date:** 2026-07-23
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SM-6

## Problem

With SM-4 landed, entity extraction finally **runs** end-to-end (the daily smoke
test now reaches graph assertions instead of failing on a masked instructor
import). But the smoke test is still **red**, and the artifact shows why: every
paper that has extracted problems throws during V2 integration, so **all V2
entity counters are zero**.

Latest smoke run (`bb083ba`, run `29905094749`) `ingest_result.json`:

```json
"papers_extracted": 3,
"total_problems": 20, "concepts_created": 20, "concepts_linked": 20,   // V1 OK
"topics_linked": 0, "concepts_v2_linked": 0, "models_linked": 0, "methods_linked": 0,  // V2 all zero
"extraction_errors": {
  "10.48550/arxiv.2312.10997": "'IntegrationResultV2' object has no attribute 'mentions'",
  "10.18653/v1/2023.emnlp-main.495": "'IntegrationResultV2' object has no attribute 'mentions'"
}
```

V1 (ProblemMention / ProblemConcept) is unaffected — problems and concepts
populate correctly (20/20/20). Only the V2 arc (Topic / ResearchConcept / Model /
Method) is blocked.

## Root cause

`packages/core/src/agentic_kg/ingestion.py:550` builds the mention list handed to
the V2 integrator from the wrong attribute:

```python
# --- V2: Topic + Concept + Model + Method writers. ---
...
mentions = (
    [
        m for m in v1_integration.mentions   # ← AttributeError
        if m.concept_id
    ]
    if v1_integration else []
)
v2_integration = integrate_paper_entities(..., mentions=mentions, ...)
```

`v1_integration` is the return of `v1_integrator.integrate_extracted_problems(...)`,
whose type is **`IntegrationResultV2`** (`extraction/kg_integration_v2.py:76`).
That class has **no `mentions` attribute**. Its per-mention list is
**`mention_results: list[MentionIntegrationResult]`**, and
`MentionIntegrationResult` (`kg_integration_v2.py:58`) carries the `concept_id`
the filter on the next line needs. The int counters used just above
(`mentions_created`, `mentions_linked`, `mentions_new_concepts`) *do* exist,
which is why lines 518–528 work and only line 550 blows up.

### Why it was hidden until now
Introduced in the loop-closure commit `7289588` (entity-pipeline-orchestration).
Double-masked:
1. **Extraction never ran in CI/Docker** — that was SM-4 (broken instructor
   import). The code path at line 550 was never reached with real data.
2. **The per-paper `try/except` at `ingestion.py:529`** absorbs the
   `AttributeError` into `result.extraction_errors[doi]` and `continue`s to the
   next paper, so nothing crashes — the paper is silently dropped from V2.

Now that SM-4 lets extraction execute, the path is reached and the latent
`AttributeError` surfaces on the first real-data run. See
[[extraction-dep-pinning]] (SM-4) for the unblock that exposed this.

## Fix

One-line correction in `packages/core/src/agentic_kg/ingestion.py:550`:

```python
m for m in v1_integration.mention_results
```

No other change to the filter or the `integrate_paper_entities` call is required —
`MentionIntegrationResult.concept_id` is the correct field.

## Acceptance criteria

- **AC-1** — `ingestion.py` builds the V2 mention list from
  `v1_integration.mention_results` (not `.mentions`).
- **AC-2** — A regression test asserts that when `ingest_papers` processes a paper
  whose V1 integration returns an `IntegrationResultV2` with populated
  `mention_results`, the V2 integrator (`integrate_paper_entities`) is called with
  a `mentions` list derived from `mention_results` filtered by `concept_id` — and
  that **no** `extraction_error` containing `has no attribute` is recorded. This
  test must **fail against the current `.mentions` code** and pass after the fix
  (guards against silent regression, since the outer `try/except` hides the
  crash).
- **AC-3** — No V1 behavior change: `total_problems` / `concepts_created` /
  `concepts_linked` counters remain as-is.
- **AC-4** — The smoke test (`smoke-ingest.yml`) `Assert graph shape` step passes:
  `topics_linked`, `concepts_v2_linked`, and at least one of `models_linked` /
  `methods_linked` are ≥ 1 on the standard `retrieval augmented generation` query
  (this is the live confirmation; runs in CI, not required for the unit PR to
  merge).

## Test plan

1. Unit (AC-2): extend `packages/core/tests/test_ingestion_v2_orchestration.py`.
   The existing suite mocks the V1 integrator — update/add a case where the mocked
   `integrate_extracted_problems` returns a real `IntegrationResultV2` populated
   with `mention_results` (each a `MentionIntegrationResult` with a `concept_id`),
   then assert the captured `mentions=` kwarg passed to `integrate_paper_entities`
   equals the `concept_id`-filtered list. **Pin the negative:** assert
   `doi not in result.extraction_errors` (or that no error mentions
   `has no attribute`). Confirm this test is red on the pre-fix line.
2. Full suite: `ruff` clean + no regressions.
3. Live confirmation (AC-4): after merge to `master`, the next `Deploy Master` (any
   `packages/core` change) redeploys the ingest Job, and the daily / dispatched
   smoke run should go green. The importer being live is tracked separately in
   [[deploy-pipeline-fix]].

## Notes

- **Scope:** genuinely one line + one test. Resist widening into a refactor of the
  V1/V2 result-type naming (`IntegrationResultV2` returned by the *V1* integrator
  is confusing, but renaming is out of scope here).
- **Consider (follow-up, not this spec):** the per-paper `try/except` at
  `ingestion.py:529`/outer handler swallowed a *programming* error
  (`AttributeError`) as if it were a data/LLM failure. A narrow follow-up could
  let `AttributeError`/`TypeError` propagate (or log them at ERROR with a distinct
  "integration wiring bug" prefix) so future wiring defects fail loudly instead of
  hiding in `extraction_errors`. File separately if wanted.
