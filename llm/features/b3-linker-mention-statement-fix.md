# Feature: Fix B3 Linker Crash — `MentionIntegrationResult` Lacks `.statement`

**Status:** IMPLEMENTED
**Date:** 2026-07-27
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SM-8

## Problem

Immediately after SM-6 (`v2-integration-mention-attr-fix`, `.mentions` →
`.mention_results`) and SM-7 (rate-limit resilience) landed on `master`, the
smoke run on master (`30294856017`) showed real progress — 429s gone,
`ResearchConcept` nodes = 17 — but a **third** latent `AttributeError` now
blocks the rest of V2:

```
Per-paper failure ...aaai...: 'MentionIntegrationResult' object has no attribute 'statement'
```

Result: `topic_edges=0, models=0, methods=0, cites=0, taxonomy_hash_papers=0`
(4 smoke checks still fail), while `concepts=17` passes.

## Root cause

`integrate_paper_entities` (`extraction/kg_integration_v2.py:918`) calls the B3
linker:

```python
edges = link_problems_to_concepts(mentions=mentions, paper_extractions=paper_extractions)
```

`b3_linker.py:link_problems_to_concepts` documents (and requires) **`ProblemMention`-like
objects with `.statement`, `.quoted_text`, `.concept_id`, `.id`**:

```python
haystack = f"{mention.statement or ''} {mention.quoted_text or ''}"   # line 72
... mention.concept_id ... mention.id                                 # lines 70, 84
```

But `mentions` is the list built in `ingestion.py` from
`v1_integration.mention_results` — i.e. **`MentionIntegrationResult`** objects,
which carry only `mention_id` + `concept_id` (and integration metadata). They
have **no `statement`, no `quoted_text`, and expose `mention_id` not `id`**.

So SM-6's `.mentions → .mention_results` fix was necessary but **not
sufficient**: it stopped the crash at the *filter* line (`if m.concept_id`) but
`MentionIntegrationResult` is still the wrong shape for the B3 linker one line
deeper. The B3-link block runs *before* the Model / Method / Topic / taxonomy-hash
writers in `integrate_paper_entities`, so this single crash skips all of them
(caught per-paper into `extraction_errors`), explaining the exact smoke pattern.

### Why it was hidden
Same double mask as SM-6: extraction never ran in CI/Docker until SM-4, and the
per-paper `try/except` swallows the `AttributeError` into `extraction_errors`.
SM-6's regression test mocked `integrate_paper_entities`, so it never exercised
the real B3 linker with a real `MentionIntegrationResult`.

## Fix

Give `MentionIntegrationResult` the shape the B3 linker documents it needs,
rather than reaching for a different object:

1. Add `statement: Optional[str]` and `quoted_text: Optional[str]` fields to
   `MentionIntegrationResult`.
2. Populate them **once** in the caller loop of `integrate_extracted_problems`
   (`_process_extracted_problem` has 9 return sites; setting the fields on the
   returned object in the single loop body covers every path uniformly and
   carries the source problem's text).
3. Expose `.id` as a property aliasing `mention_id` (the B3 linker uses
   `mention.id` for logging; `MentionIntegrationResult`-as-`ProblemMention`-like
   should answer to `.id`).

No change to the B3 linker itself; `MentionIntegrationResult` becomes a valid
`ProblemMention`-like input.

## Sample Implementation

```python
# kg_integration_v2.py — schema
class MentionIntegrationResult(BaseModel):
    mention_id: str = Field(..., description="ProblemMention ID")
    concept_id: Optional[str] = Field(None, description="Linked ProblemConcept ID")
    # SM-8: the B3 linker (link_problems_to_concepts) matches on the problem text,
    # so carry it here — this object IS the ProblemMention-like input.
    statement: Optional[str] = Field(None, description="Source problem statement")
    quoted_text: Optional[str] = Field(None, description="Source problem quoted text")
    ...

    @property
    def id(self) -> str:
        """Alias for mention_id — B3 linker expects a ProblemMention-like `.id`."""
        return self.mention_id


# integrate_extracted_problems — caller loop (single site)
mention_result = self._process_extracted_problem(extracted_problem=extracted_problem, ...)
mention_result.statement = extracted_problem.statement
mention_result.quoted_text = extracted_problem.quoted_text
result.mention_results.append(mention_result)
```

## Acceptance Criteria

### AC-1: `MentionIntegrationResult` carries problem text + `.id`
- **Given** a `MentionIntegrationResult`
- **When** constructed / inspected
- **Then** it has `statement` and `quoted_text` fields and an `.id` property equal
  to `mention_id`.

### AC-2: B3 linker accepts `MentionIntegrationResult` without crashing (regression)
- **Given** a `MentionIntegrationResult` with `statement`, `quoted_text`,
  `concept_id`, `mention_id`
- **When** passed to `link_problems_to_concepts` alongside matching
  `paper_extractions`
- **Then** it returns edges without `AttributeError`. Must be **red** against the
  pre-fix schema (no `.statement`) and green after.

### AC-3: The integrator populates the text
- **Given** `integrate_extracted_problems` processes an extracted problem
- **When** it appends a `MentionIntegrationResult`
- **Then** that result's `statement` / `quoted_text` equal the source
  `extracted_problem`'s.

### AC-4: Smoke unblocks (live confirmation)
- **Given** the fix is on `master` and deployed
- **When** the smoke `Ingest + Assert` runs
- **Then** `topic_edges`, `models`/`methods`, and `taxonomy_hash` are ≥ 1 (no
  `has no attribute` errors). Non-required for the unit PR to merge.

## Test Plan

1. Unit (AC-1/AC-2/AC-3): extend the V2 integration tests — a real
   `MentionIntegrationResult` through `link_problems_to_concepts`, and assert the
   `_process_extracted_problem` caller populates the text. AC-2 test must be red
   on the pre-fix schema.
2. Full suite + ruff clean.
3. Live: smoke re-run after deploy.

## Notes

- **Scope:** the schema fields + one caller-loop assignment + an `id` property.
  Resist widening into a rename of the confusingly-named `IntegrationResultV2`
  (returned by the *V1* integrator) — tracked as tech debt, out of scope.
- **Follow-up already noted in SM-6:** the per-paper `try/except` swallows
  *programming* errors (`AttributeError`) as if they were data/LLM failures,
  which is why three of these hid in sequence. A narrow change to let
  `AttributeError`/`TypeError` propagate (or log at ERROR with a distinct
  "integration wiring bug" prefix) would surface the next such defect loudly.
  Filed as SM-9 (below), still out of scope here.
