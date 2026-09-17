# agentic-kg — KGIS/KGCS Adoption & Migration Orchestration Plan

Date: 2026-09-17
Status: Proposed execution plan
Target: `djjay0131/agentic-kg`
Source platform: `djjay0131/agentic-kgis` + `djjay0131/agentic-kgcs`
Governance: obey the repository's current `llm/governance/governance-delta.md` and canonical agentic-governance pin at execution time.

## 1. Mission

Adopt KGIS and KGCS as the reusable ingestion/curation platform underneath agentic-kg without a big-bang rewrite.

The migration must prove that the new platform preserves or improves the existing research-paper graph on real evidence before any legacy path is retired. Development may deploy to a non-production GCP environment for integration and live-data validation. Production/master cutover remains a human-owner decision.

The target architecture is:

```
research sources / PDFs / structured metadata
        |
        v
      KGIS
  extraction/sync
        |
Candidate + Evidence
        |
 candidate ledger
        |
        v
      KGCS
 validation -> ER -> deterministic policy -> bounded LLM advice/review
        |
   CurationPlan
        |
    executor
        |
 canonical graph / curation epoch
        |
 agentic-kg application agents and retrieval
```

KGIS/KGCS own reusable knowledge admission and curation. agentic-kg retains domain-specific research semantics, application orchestration, research agents, retrieval, UI/API, and deployment.

## 2. Current baseline and why this is a migration, not a rewrite

agentic-kg currently has a mature bespoke research ingestion pipeline (`packages/core/src/agentic_kg/ingestion.py`) feeding Neo4j, including Topic, ResearchConcept, Model, Method, Problem and Citation behavior. Its governance delta explicitly records agentic-kg as a future consumer of KGIS/KGCS, with zero current `kg_contracts` references.

The repository also has an 8-paper connected ground-truth chain under:

`packages/core/tests/extraction/fixtures/ground_truth_chain/`

It was designed specifically to validate the live importer end-to-end and contains 10 verified citation edges plus recurring concepts across papers. Only reconciled fixtures are authoritative; human and Claude reviews remain evidence.

Important current constraints:

- the ground-truth program is incomplete: the repo README currently records reconciled Output B for only part of the 8-paper set;
- segmentation backlog (especially SEG-3/4/5/6/7) can confound extraction-vs-gold comparisons and must be dispositioned before interpreting recall gaps;
- open Issue #58 records a real legacy-path defect: `CITES` edges are currently absent in smoke ingestion while the other five graph-shape assertions pass;
- GCP deployment already exists and multiple environments are available, so a development/shadow environment may be used for migration tests without replacing production;
- agentic-kg, KGIS and KGCS are all on current portfolio governance and KGIS/KGCS v1 implementation is merged.

This migration therefore uses the existing path as a baseline/oracle where trustworthy, the reconciled ground truth as the semantic oracle, and the new KGIS/KGCS path as a shadow candidate until explicit cutover gates pass.

## 3. Non-negotiable migration rules

1. No big-bang replacement.
2. No direct application-facing canonical writes are introduced. New canonical mutation flows through KGCS `CurationPlan` + executor.
3. Preserve source provenance/evidence through KGIS candidates into KGCS audit/canonical assertions.
4. Do not make agentic-kg depend on KGIS/KGCS implementation internals where a public contract/adapter exists.
5. Domain-specific ontology mapping belongs in the adopter adapter/configuration, not in reusable KGIS/KGCS core.
6. Existing production remains authoritative until the cutover gate is explicitly approved.
7. Shadow results must not mutate production canonical state.
8. A green node-count comparison is insufficient: semantic quality, evidence, identity and edge correctness are required.
9. LLM nondeterminism must be controlled in CI with recorded/replay fixtures; live-model tests run only in designated integration environments.
10. Every migration PR follows current governance, independent review and required CI. No agent self-merges to production/master unless governance explicitly authorizes it.
11. GCP development deployments are permitted for migration validation. Production promotion requires owner approval.
12. Existing agent/retrieval behavior must remain functional against the chosen canonical graph before legacy removal.

## 4. Success criteria

The migration is ready for cutover only when all of the following are demonstrated:

- KGIS can ingest the target research-paper sources and emit typed Candidates + Evidence for the agentic-kg ontology mapping;
- KGCS can curate those candidates into the required canonical graph semantics;
- the 8-paper evaluation set is sufficiently reconciled to support a meaningful comparison, with incomplete gold explicitly excluded rather than treated as negatives;
- extraction/curation metrics are computed using `kg_eval` or an adapter over its metric-provider seam;
- no material regression in required entity/edge recall versus reconciled gold;
- false merges, false splits and provenance/evidence defects are within an owner-approved risk budget;
- citation semantics are tested independently of the legacy Issue #58 failure;
- the evidence-evolution scenario works: later paper evidence can corroborate, conflict with or supersede an existing assertion while retaining history;
- current agentic-kg query/retrieval/agent paths can consume the new canonical graph through a compatibility/projection layer;
- staging/development GCP deployment is stable through ingest + curate + query smoke tests;
- rollback to the legacy production path is documented and tested before production cutover;
- final owner decision explicitly authorizes cutover.

## 5. Orchestration model

A top-level Orchestrator coordinates specialist subagents in isolated worktrees. Parallelize discovery/evaluation work aggressively, but serialize changes that touch the same migration seam.

Recommended roles:

- Repository/Governance Steward
- Legacy Pipeline Archaeologist
- KGIS Integration Architect
- KGCS Integration Architect
- Ontology/Mapping Agent
- Ground-Truth & Evaluation Agent
- Segmenter/Corpus Readiness Agent
- Neo4j/Projection Adapter Agent
- GCP Deployment Agent
- Issue-58 Citation Investigator
- Research-Agent Compatibility Agent
- Security/Secrets/Config Reviewer
- Independent Architecture Reviewer
- Independent Test/Adversarial Reviewer
- Release/Cutover Steward

Required semantic task loop:

```
implementer -> targeted tests -> independent reviewer -> fix loop
            -> branch-wide tests -> PR -> CI -> review disposition
```

Subagents may approve/recommend downstream integration PRs when repository governance permits, but the top-level orchestrator must not weaken branch protection, bypass required checks, fabricate approvals, or merge production/master without the authority current governance grants. Human-owner approval remains the final cutover gate.

## 6. Branch and environment strategy

Create a long-lived integration branch, recommended:

`integration/kgis-kgcs-adoption`

Feature PRs should target this integration branch while the shadow migration is being assembled. Keep each PR independently reviewable. Periodically rebase/merge current `master` into the integration branch so the migration does not drift from active application development.

GCP:

- deploy the integration branch to a dedicated development/shadow environment;
- use a separate Neo4j database/instance or clearly isolated database namespace from production;
- use separate KGIS ledger/evidence persistence and KGCS review/audit persistence;
- do not point the migration executor at production canonical graph state during shadow validation;
- secrets come from the existing secret-management mechanism, never committed fixtures;
- record deployed commit SHAs for agentic-kg, KGIS and KGCS in every live test report.

The orchestrator must inspect the current deploy pipeline before deciding exact branch/environment names; do not assume old environment wiring still matches this plan.

## 7. Phase 0 — Preflight, authority and dependency pinning

### Goal

Establish a reproducible baseline before code changes.

### Tasks

1. Refresh `master`, open issues/PRs, governance delta, activeContext, systemPatterns, BACKLOG and deployment docs.
2. Record exact current KGIS and KGCS release/commit SHAs and their package-install mechanism.
3. Run existing agentic-kg unit/integration/governance checks and record known-red checks separately.
4. Reproduce Issue #58 or record the latest reproducible evidence if external dependencies prevent a deterministic reproduction.
5. Inventory existing direct Neo4j write surfaces in ingestion/extraction/normalization/citation code.
6. Inventory current domain entity/relationship schema and map which pieces are ingestion, curation, projection or application concerns.
7. Create the integration branch and a migration status document under `llm/` according to current governance.
8. Decide dependency pinning for KGIS/KGCS during migration: exact commit/tag pins for reproducibility, not floating branches.

### Exit

- reproducible baseline report;
- known-red list separated from migration regressions;
- exact dependency SHAs;
- integration branch established;
- no production behavior changed.

## 8. Phase 1 — Compatibility inventory and mapping specification

### Goal

Define the adapter contract between agentic-kg domain semantics and generic KGIS/KGCS contracts before implementation.

### Parallel workstreams

#### 1A — Legacy write-path inventory

Trace every path that creates/updates:

- Paper
- Topic / Research Area
- ResearchConcept
- Model
- Method
- Problem
- CITES and other domain relationships
- taxonomy hashes
- descriptions/embeddings
- normalization/audit records

Classify each as:

`source acquisition | extraction | candidate construction | curation | projection | application-only`

#### 1B — Candidate mapping

Specify how legacy outputs map to KGIS candidate variants and evidence:

- stable semantic keys and external aliases;
- candidate kind;
- evidence span/source coordinates;
- extraction/source-reliability scores;
- valid/transaction time where applicable;
- model/extractor/prompt versions;
- ontology term requirements.

#### 1C — Canonical graph mapping

Specify how KGCS canonical identities/assertions project to the Neo4j shape current application agents expect. Prefer a projection/compatibility adapter over contaminating KGCS with agentic-kg labels.

#### 1D — Migration identity strategy

Define equivalence between legacy Neo4j IDs and new canonical IDs. No silent identity fork. Produce deterministic mapping and collision tests.

### Deliverable

A reviewed migration mapping spec in the repository control plane. If it makes durable architecture decisions, create an agentic-kg ADR.

### Exit

No code implementation until the mapping spec is reviewed.

## 9. Phase 2 — Evaluation corpus readiness

### Goal

Make semantic comparison trustworthy before judging the new pipeline.

### Tasks

1. Audit all 8 ground-truth papers: human, Claude and reconciled fixture completeness.
2. Do not fabricate missing reconciliation. Incomplete papers are marked not-scoreable.
3. Resolve the open `named-resources` ground-truth question that currently blocks importer diffing.
4. Disposition SEG-3/4/5/6/7 for the migration evaluation:
   - implement prerequisites that materially affect the corpus before scoring; or
   - freeze a clearly-versioned segmentation input and state which recall questions cannot be answered.
5. Build/finish a loader/runner over reconciled fixtures. Reuse `kg_eval` metrics where compatible; write a narrow provider/adapter rather than duplicate metric definitions.
6. Establish metrics:
   - entity precision/recall by type;
   - relation precision/recall;
   - citation edge recall/precision;
   - evidence-span validity;
   - provenance completeness;
   - false merge / false split;
   - abstention/review rate;
   - calibration where gold supports it;
   - LLM calls/tokens/cost and latency.
7. Explicitly exclude `acceptable_extras` from the precision denominator as the fixture schema requires.

### Exit

A deterministic evaluation command produces an honest report with sample counts and nulls for insufficient evidence.

## 10. Phase 3 — KGIS shadow ingestion adapter

### Goal

Run the existing research sources through KGIS without changing production graph state.

### Tasks

1. Add KGIS/KGCS dependencies behind an opt-in migration feature/config flag.
2. Build agentic-kg source/document adapters around existing acquisition/PDF extraction where reuse is cheaper than replacing it.
3. Configure KGIS per-entity-type extractors/mappings for Paper, Topic, ResearchConcept, Model, Method, Problem and Citation/relationship candidates as justified by the mapping spec.
4. Register Evidence for quoted passages/source records with stable coordinates.
5. Persist candidates to an isolated ledger/evidence store.
6. Add recorded/replay LLM fixtures for deterministic CI.
7. Add live-model integration tests only in the GCP dev environment.
8. Compare KGIS candidate output with legacy extractor output and reconciled gold before KGCS is allowed to write a shadow canonical graph.

### Exit

Same corpus -> reproducible candidate/evidence set in CI replay mode; live dev run completes with no production graph writes.

## 11. Phase 4 — KGCS shadow curation + Neo4j adapter/projection

### Goal

Materialize a separate canonical graph using KGCS and make it query-compatible with agentic-kg.

### Tasks

1. Implement/configure the Neo4j `GraphReader` / `GraphMutationStore` adapter required by current `kg_contracts`, or reuse an existing conforming adapter if one now exists.
2. Run the shared reusable contract suites against the adapter.
3. Configure agentic-kg curation profile(s): research-paper evidence is not client-authoritative identity by default; deterministic metadata sources may have stronger authority.
4. Configure ER blocking/features/calibration with conservative initial automation; prefer review/abstention over false merge.
5. Wire bounded KGCS advisers using recorded completion in CI and the approved live provider in dev.
6. Materialize only into the isolated shadow graph.
7. Build a projection/compatibility layer so current application queries can consume canonical state at a published curation epoch.
8. Prove no agentic-kg code obtains a raw KGCS graph-write surface.

### Exit

KGIS -> ledger/evidence -> KGCS -> shadow Neo4j -> compatibility query works end-to-end.

## 12. Phase 5 — Shadow comparison and Issue #58 treatment

### Goal

Compare legacy and new pipelines without letting legacy defects become false requirements.

Run the same frozen corpus through:

- legacy agentic-kg pipeline;
- KGIS + KGCS shadow pipeline.

Produce a three-way comparison:

`legacy vs gold | new vs gold | legacy vs new`

Issue #58 rule:

- investigate the legacy citation failure in parallel;
- do not encode `CITES=0` as expected behavior merely because legacy currently does it;
- use reconciled citation gold/source verification as the semantic oracle;
- if the new path fixes citations, record it as an improvement;
- if both fail, block cutover until citation semantics are understood.

Classify every material difference:

`new regression | legacy defect | gold ambiguity | expected architecture difference | improvement | insufficient evidence`

### Exit

No unexplained material differences.

## 13. Phase 6 — Evidence-evolution / re-curation acceptance test

### Goal

Prove the capability that motivated KGCS rather than merely reproducing static ingestion.

Use at least two papers from the connected ground-truth chain where later evidence touches the same concept/assertion.

Required scenario:

1. ingest Paper A;
2. curate to epoch N;
3. capture canonical identity/assertion/evidence/audit state;
4. ingest Paper B with corroborating, conflicting or superseding evidence;
5. trigger targeted re-curation;
6. deterministic baseline runs first;
7. if routed, adviser reasons only over cited evidence;
8. deterministic policy gates the recommendation;
9. execute plan to epoch N+1;
10. assert old assertion/history remains queryable;
11. assert evidence from both papers is traceable;
12. replay the semantic decision from recorded inputs and detect divergence if altered.

Also run one synthetic/non-paper fixture through the same curation machinery to guard against accidental research-paper hard-coding in the reusable layer.

### Exit

The evolution test passes in deterministic CI replay and live dev GCP.

## 14. Phase 7 — Application-agent compatibility

### Goal

Prove Ranking/Continuation/Evaluation/Synthesis and retrieval surfaces work against the new canonical projection.

### Tasks

1. Inventory Cypher/query assumptions in agents and API/retrieval code.
2. Run read-only compatibility tests against both legacy and shadow graphs.
3. Add projection fields/aliases only where required by the mapping spec.
4. Do not leak ledger/provisional state into application reads.
5. Compare agent inputs/results for a frozen set of research queries. Exact text equality is not required for live LLM outputs; structural inputs, retrieved entities/evidence and deterministic pre-LLM context must be comparable.

### Exit

No blocking application read-path regression.

## 15. Phase 8 — GCP development deployment and soak

### Goal

Exercise the integrated branch under realistic infrastructure without production cutover.

### Tasks

1. Deploy `integration/kgis-kgcs-adoption` to a dedicated development environment using the