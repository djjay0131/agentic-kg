# Feature Backlog

Last updated: 2026-03-28

Features that need detailed specification and/or implementation. Ordered by priority within each category. Status key:

- **Needs Spec** — no design doc exists; must go through `@construction-agent design` before implementation
- **Has Spec** — design doc exists but implementation not started
- **Partial Spec** — covered in a broader design doc but needs its own detailed spec
- **Implemented (Broken)** — code exists but is failing or incomplete

---

## Category 1: Stabilization & Test Fixes

~~These block everything else — the codebase must be green before new features.~~
Codebase is green: 1146 tests passing, 0 failures (verified 2026-03-28).

| # | Feature | Status | Priority | Notes |
|---|---------|--------|----------|-------|
| ~~S-1~~ | ~~Fix 33 failing extraction tests~~ | **Resolved** | ~~Critical~~ | All 264 extraction tests passing as of 2026-03-28 |
| ~~S-2~~ | ~~Fix E2E test import errors~~ | **Resolved** | ~~Critical~~ | 32 E2E tests collecting cleanly as of 2026-03-28 |
| S-3 | Sprint 10 integration tests (golden dataset) | Partial Spec | High | Task 10 in sprint-10 doc; golden dataset for accuracy benchmarks not yet run against live Neo4j |

---

## Category 2: Real Data Ingestion & Validation

The KG has only test data. These features prove the system works end-to-end.

| # | Feature | Status | Priority | Notes |
|---|---------|--------|----------|-------|
| ~~D-1~~ | ~~Ingest real papers into KG~~ | **Verified** | ~~High~~ | Implemented and verified 2026-03-28. See `llm/features/d1-ingest-real-papers.md` |
| D-1a | Cloud Run Jobs for async ingestion | **Specifying** | **High** | Decouple ingestion from API instance lifecycle; Terraform job resource + job_runner.py. See `llm/features/cloud-run-jobs-ingestion.md` |
| D-1b | GCS ingestion research log | Needs Spec | Medium | Write per-paper markdown progress files to GCS bucket during ingestion; durable provenance trail surviving job crashes. Follow-on to D-1a |
| D-2 | Extraction reliability metrics | Needs Spec | High | Measure F1 vs. inter-annotator agreement (success criterion from productContext.md) |
| D-3 | Retrieval quality benchmarks | Needs Spec | High | Measure MRR and nDCG vs. keyword/citation baselines |
| D-4 | Agent decision accuracy validation | Needs Spec | Medium | Validate EvaluatorAgent (>90% human agreement) and consensus workflow (>85%) against real data |

---

## Category 3: Entity Ecosystem Expansion

From `construction/design/kg-schema-enhancement-gap-analysis.md`. Currently we have 5 entity types (Problem, ProblemMention, ProblemConcept, Paper, Author). The gap analysis identifies 4 missing entity types needed to become a true research knowledge graph.

| # | Feature | Status | Priority | Proposed Sprint | Notes |
|---|---------|--------|----------|-----------------|-------|
| E-1 | Topic / Research Area entities | Partial Spec | **High** | Sprint 11 | First-class Topic nodes with hierarchy (domain → area → subtopic); replace `domain` string field; BELONGS_TO relationships. Gap analysis §4.1 Gap 1 |
| E-2 | ResearchConcept entities | Partial Spec | **High** | Sprint 11 | Generic concepts ("attention mechanism", "transfer learning") as nodes; INVOLVES_CONCEPT and DISCUSSES relationships; extend mention-to-concept canonicalization. Gap analysis §4.1 Gap 2 |
| E-3 | Model / Architecture entities | Partial Spec | Medium | Sprint 12 | ML models as first-class nodes (currently embedded in Baseline strings); USES_MODEL, VARIANT_OF relationships; model lineage chains. Gap analysis §4.1 Gap 3 |
| E-4 | Method / Methodology entities | Partial Spec | Medium | Sprint 12 | Research methods as nodes; APPLIES_METHOD, ADDRESSED_BY relationships. Gap analysis §4.1 Gap 4 |
| E-5 | Citation graph (Paper → Paper) | Partial Spec | **High** | Sprint 12 | CITES relationships from Semantic Scholar API data; enables influence chain analysis. Gap analysis §4.2 |
| E-6 | Entity descriptions for vector search | Partial Spec | Medium | Sprint 11-12 | Add `description` field to all entity types; use description+name for richer embeddings. Gap analysis §4.3 Cap 5 |
| E-7 | Cross-entity normalization | Partial Spec | Medium | Sprint 12+ | Extend mention-to-concept canonicalization pattern to ResearchConcept, Model, Method (currently only Problems are deduplicated). Gap analysis §4.3 Cap 4 |
| E-8 | Extraction prompt expansion | Needs Spec | **High** | Sprint 11 | Extend LLM extraction prompts to capture Topics, Concepts, Models, Methods from papers (currently only extracts Problems from Limitations/Future Work sections) |

---

## Category 4: Community Detection & Hierarchical Summarization

From gap analysis §4.3 Capabilities 1-2. Depends on Category 3 for a richer entity graph.

| # | Feature | Status | Priority | Proposed Sprint | Notes |
|---|---------|--------|----------|-----------------|-------|
| C-1 | Community detection | Partial Spec | High | Sprint 13 | Leiden/Louvain algorithm on full graph; Community nodes at multiple hierarchy levels; incremental updates. Gap analysis §4.3 Cap 1 |
| C-2 | Hierarchical graph summarization | Partial Spec | High | Sprint 13 | LLM-generated summaries per community at multiple resolutions (domain → topic → subtopic → concept); answers global questions. Gap analysis §4.3 Cap 2 |
| C-3 | Community browsing API & UI | Needs Spec | Medium | Sprint 13 | API endpoints and frontend for exploring research landscape by community |

---

## Category 5: Graph-Based RAG Retrieval

The "killer feature" — makes the KG useful for researcher queries. Depends on Categories 3-4.

| # | Feature | Status | Priority | Proposed Sprint | Notes |
|---|---------|--------|----------|-----------------|-------|
| R-1 | Query-facing vector search | Partial Spec | **High** | Sprint 14 | Expose vector search across all entity types for user queries (currently internal to matching pipeline only). Gap analysis §4.3 Cap 3 |
| R-2 | Graph neighbor expansion | Partial Spec | High | Sprint 14 | Configurable-depth graph traversal from vector search hits; context assembly from graph paths |
| R-3 | LLM synthesis endpoint | Partial Spec | High | Sprint 14 | POST /api/query — natural language question → answer with provenance from graph |
| R-4 | Community-aware retrieval | Partial Spec | Medium | Sprint 14 | Include community summaries in retrieval context for global/thematic questions |
| R-5 | RAG evaluation & benchmarks | Needs Spec | Medium | Sprint 14 | Measure improvement vs. vector-only retrieval for multi-hop questions |

---

## Category 6: Production Readiness

| # | Feature | Status | Priority | Notes |
|---|---------|--------|----------|-------|
| P-1 | Production deployment | Needs Spec | High | `terraform apply -var-file=envs/prod.tfvars`; needs runbook, monitoring, alerting |
| P-2 | Neo4j production hosting docs | Needs Spec | High | Aura vs. self-managed decision; connection strings, backup procedures, failover (from sprint-01 backlog) |
| P-3 | Multi-hop graph traversal | Needs Spec | Medium | FR-2.3.4 deferred from Sprint 01; add `max_depth` to `get_related_problems()`; useful for agent exploration and R-2 |
| P-4 | Referential integrity on delete | Needs Spec | Low | Check EXTRACTED_FROM relations before paper delete; prefer soft delete (from sprint-01 backlog) |
| P-5 | Scalability testing | Needs Spec | Medium | Current system untested beyond small datasets; paper's system tested on only 10 papers; need to validate at 100+ papers |

---

## Category 7: Unvalidated Success Criteria

These aren't features per se, but require specification of how we'll measure and validate them.

| # | Criterion | Source | Status |
|---|-----------|--------|--------|
| V-1 | Extraction F1 within 10% of inter-annotator agreement | productContext.md | Not measured |
| V-2 | MRR/nDCG improvement over keyword/citation baselines | productContext.md | Not measured |
| V-3 | Faster time to actionable continuation | productContext.md | Not measured |
| V-4 | Higher user-reported confidence vs. opaque AI | productContext.md | Not measured |
| V-5 | Active use by research teams | productContext.md | Not achieved |

---

## Dependency Graph

```
S-1, S-2 (test fixes)
    └──► D-1 (real data ingestion)
            └──► D-2, D-3, D-4 (metrics & validation)
            └──► E-8 (extraction prompt expansion)
                    └──► E-1, E-2 (Topics & Concepts — Sprint 11)
                            └──► E-3, E-4, E-5 (Models, Methods, Citations — Sprint 12)
                                    └──► C-1, C-2 (Community detection — Sprint 13)
                                            └──► R-1, R-2, R-3 (RAG retrieval — Sprint 14)

P-1, P-2 (production) — can run in parallel after S-1/S-2
P-3 (multi-hop) — prerequisite for R-2
```

---

## Open Design Questions

From the gap analysis, these need answers before Sprint 11 spec:

1. **Entity extraction scope** — Extract all 9 entity types from the paper, or prioritize top 4 (Topic, Concept, Model, Method)?
2. **Concept canonicalization** — Full mention-to-concept pipeline (with agents + human review) for ResearchConcepts, or simpler auto-merge?
3. **Community algorithm** — Leiden (hierarchical, GraphRAG-style) vs. Louvain (simpler, paper-style)?
4. **RAG service architecture** — Separate service or integrated into existing API?
5. **Migration strategy** — How to convert existing `domain` string fields to Topic nodes without breaking functionality?
