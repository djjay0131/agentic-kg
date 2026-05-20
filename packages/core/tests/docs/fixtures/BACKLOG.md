# Feature Backlog

Last updated: 2026-04-16

Features that need detailed specification and/or implementation.

---

## Category 1: Stabilization & Test Fixes

Codebase is green: 1146 tests passing, 0 failures (verified 2026-03-28).

| # | Feature | Status | Priority | Notes |
|---|---------|--------|----------|-------|
| ~~S-1~~ | ~~Fix 33 failing extraction tests~~ | **Resolved** | ~~Critical~~ | All 264 extraction tests passing |
| S-3 | Sprint 10 integration tests (golden dataset) | Partial Spec | High | Task 10 in sprint-10 doc |

---

## Category 2: Real Data Ingestion & Validation

| # | Feature | Status | Priority | Notes |
|---|---------|--------|----------|-------|
| ~~D-1~~ | ~~Ingest real papers into KG~~ | **Verified** | ~~High~~ | Implemented and verified 2026-03-28 |
| D-2 | Extraction reliability metrics | Needs Spec | High | Measure F1 vs. inter-annotator agreement |

---

## Category 7: Unvalidated Success Criteria

These aren't features per se — they require specification of how we measure.

| # | Criterion | Source | Status |
|---|-----------|--------|--------|
| V-1 | Extraction F1 within 10% of inter-annotator agreement | productContext.md | Not measured |
| V-2 | MRR/nDCG improvement over keyword/citation baselines | productContext.md | Not measured |
