---
layout: default
title: Feature Backlog
---

# Feature Backlog

_Last updated: 2026-04-17_

Canonical source: [`llm/features/BACKLOG.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/BACKLOG.md). This page is a published snapshot.

## Status Key

| Status | Meaning |
|---|---|
| **Verified** | Implemented, tested, deployed to staging |
| **Has Spec** | Design doc exists; implementation not started |
| **Specifying** | Spec in progress |
| **Partial Spec** | Covered in a broader design; needs its own spec |
| **Needs Spec** | No design doc — must run `/constellize:feature:specify` first |
| **Resolved** | Bug/task complete |
| **Not Measured** | Success criterion defined but not yet evaluated |

---

## Category 1 — Stabilization & Test Fixes

| # | Feature | Status | Priority |
|---|---------|--------|----------|
| ~~S-1~~ | Fix 33 failing extraction tests | Resolved | — |
| ~~S-2~~ | Fix E2E test import errors | Resolved | — |
| S-3 | Sprint 10 integration tests (golden dataset) | Partial Spec | High |

## Category 2 — Real Data Ingestion & Validation

| # | Feature | Status | Priority |
|---|---------|--------|----------|
| ~~D-1~~ | Ingest real papers into KG | **Verified** | — |
| D-1a | Cloud Run Jobs for async ingestion | **Verified** | — |
| D-1b | GCS ingestion research log | Needs Spec | Medium |
| D-2 | Extraction reliability metrics (F1 vs IAA) | Needs Spec | High |
| D-3 | Retrieval quality benchmarks (MRR, nDCG) | Needs Spec | High |
| D-4 | Agent decision accuracy validation | Needs Spec | Medium |

## Category 3 — Entity Ecosystem Expansion

Source: `construction/design/kg-schema-enhancement-gap-analysis.md`.

| # | Feature | Status | Priority | Sprint |
|---|---------|--------|----------|--------|
| E-1 | Topic / Research Area entities | **Specified** | **High** | 11 |
| E-2 | ResearchConcept entities | **Specified** | **High** | 11 |
| E-3 | Model / Architecture entities | Partial Spec | Medium | 12 |
| E-4 | Method / Methodology entities | Partial Spec | Medium | 12 |
| E-5 | Citation graph (Paper → Paper) | Partial Spec | **High** | 12 |
| E-6 | Entity descriptions for vector search | Partial Spec | Medium | 11–12 |
| E-7 | Cross-entity normalization | Partial Spec | Medium | 12+ |
| E-8 | Extraction prompt expansion | Needs Spec | **High** | 11 |

## Category 4 — Community Detection & Hierarchical Summarization

| # | Feature | Status | Priority | Sprint |
|---|---------|--------|----------|--------|
| C-1 | Community detection (Leiden/Louvain) | Partial Spec | High | 13 |
| C-2 | Hierarchical graph summarization | Partial Spec | High | 13 |
| C-3 | Community browsing API & UI | Needs Spec | Medium | 13 |

## Category 5 — Graph-Based RAG Retrieval

| # | Feature | Status | Priority | Sprint |
|---|---------|--------|----------|--------|
| R-1 | Query-facing vector search | Partial Spec | **High** | 14 |
| R-2 | Graph neighbor expansion | Partial Spec | High | 14 |
| R-3 | LLM synthesis endpoint (`POST /api/query`) | Partial Spec | High | 14 |
| R-4 | Community-aware retrieval | Partial Spec | Medium | 14 |
| R-5 | RAG evaluation & benchmarks | Needs Spec | Medium | 14 |

## Category 6 — Production Readiness

| # | Feature | Status | Priority |
|---|---------|--------|----------|
| P-1 | Production deployment (Terraform prod apply) | Needs Spec | High |
| P-2 | Neo4j production hosting docs (Aura vs. self-managed) | Needs Spec | High |
| P-3 | Multi-hop graph traversal | Needs Spec | Medium |
| P-4 | Referential integrity on delete | Needs Spec | Low |
| P-5 | Scalability testing (100+ papers) | Needs Spec | Medium |
| T-1 | Taxonomy management at scale | Needs Spec | Medium |

## Category 7 — Unvalidated Success Criteria

| # | Criterion | Status |
|---|-----------|--------|
| V-1 | Extraction F1 within 10% of inter-annotator agreement | Not Measured |
| V-2 | MRR/nDCG improvement over baselines | Not Measured |
| V-3 | Faster time to actionable continuation | Not Measured |
| V-4 | Higher user-reported confidence vs. opaque AI | Not Measured |
| V-5 | Active use by research teams | Not Achieved |

---

## Summary Counts

- **Verified / Resolved:** 4 (D-1, D-1a, S-1, S-2)
- **Specified (ready to implement):** 3 (enhance-github-pages, E-1, E-2)
- **Specifying / Partial Spec:** 12
- **Needs Spec:** 13 (incl. T-1)
- **Unmeasured success criteria:** 5

## Open Design Questions (pre-Sprint 11)

1. Entity extraction scope — all 9 types or prioritize top 4 (Topic, Concept, Model, Method)?
2. Concept canonicalization — full mention-to-concept pipeline for ResearchConcepts, or simpler auto-merge?
3. Community algorithm — Leiden (hierarchical, GraphRAG-style) vs. Louvain (simpler)?
4. RAG service architecture — separate service or integrated into existing API?
5. Migration strategy for existing `domain` string fields → Topic nodes?

---

[← Back to Home](index.html)
