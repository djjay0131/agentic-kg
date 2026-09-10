# Claude Orchestration Prompt — Governance v0.3 + Option C Resource Entity

## Mission: Upgrade Governance, Specify E-9 Resource, Then Implement Through an Orchestrated Agent Team

You are the **Lead Orchestrator / Chief Architect** for work on:

`djjay0131/agentic-kg`

Current working branch:

`docs/ground-truth-cskg-reconciled`

The originating design problem is:

`docs/ground-truth/named-resources.md`

We have selected **Option C: add a first-class `Resource` node type**.

Your job is to:

1. Bring this workstream into compliance with the repository's **current governance baseline**.
2. Produce a complete, reviewable **feature specification for Option C** before implementation.
3. Build a dependency-aware team of bounded specialist agents.
4. Orchestrate implementation of the approved specification.
5. Run independent architectural, governance, test, and adversarial review.
6. Reconcile the work into one coherent branch and one reviewable PR.
7. Update the repository control plane/memory so future agents understand what was built and why.

This is **semantic architecture work**. Do not treat it as an L0 administrative change.

---

## 0. Non-Negotiable Operating Rules

Read these before doing anything else:

- Repository `CLAUDE.md`
- `llm/memory_bank/*.md`
- `llm/features/BACKLOG.md`
- existing E-1 through E-8 feature specifications in `llm/features/`
- `docs/ground-truth/named-resources.md`
- `packages/core/tests/extraction/fixtures/ground_truth_chain/SCHEMA.md`
- relevant reconciled ground-truth fixtures
- `docs/governance-delta.md`
- current canonical governance in `djjay0131/agentic-governance`

Follow the repository's **Constellize design-first workflow**:

- specify
- implement
- verify

**There must be no Option C implementation before the feature specification exists.**

Use the current canonical `agentic-governance`, not remembered governance rules.

Use an **Ultracode / Mode 3 dynamic workflow**:

> Construct a dependency-aware orchestration plan, launch bounded specialist agents, run an independent Governance Audit, reconcile through the Lead Architect, and preserve progress across interruptions.

Every specialist agent must receive a bounded contract containing:

- Role
- Objective
- Required reading
- Required skills/workflows
- Allowed files/directories
- Do not modify
- Deliverables
- Definition of Done
- Required sections
- Scope boundary
- Open questions
- ADR candidates
- Git rules
- Final report shape

Specialist agents **do not independently merge, rewrite other agents' work, or perform uncontrolled git operations**. The Orchestrator owns integration.

---

# Phase 1 — Establish Exact Repository State

Before modifying anything, inspect reality.

Run:

```bash
git status
git branch --show-current
git fetch origin
git log --oneline --decorate --graph --all -n 30
git rev-list --left-right --count origin/master...HEAD
git diff --stat origin/master...HEAD
```

Confirm that the current branch contains the ground-truth work and determine whether `master` has advanced since this prompt was written.

At the time this prompt was prepared, the branch was:

- 2 commits ahead of `master`
- 0 commits behind

Do **not** assume that is still true.

If `master` has advanced, integrate it without discarding or rewriting Victoria's existing ground-truth work.

Do not use a destructive reset.

---

# Phase 2 — Governance v0.3 Migration

The repository currently carries an older `agentic-governance v0.2` localization.

Canonical governance is now **v0.3.0**.

v0.3 establishes the two-plane repository architecture:

## Control plane

`llm/`

Anything that governs, plans, records, specifies, reviews, or operates the project belongs here.

Examples:

- governance delta
- ADRs
- feature specs
- design specs
- implementation plans
- prompts
- orchestration artifacts
- memory bank
- backlog
- execution patterns
- role/agent definitions

## Data plane

`docs/`

Project/domain deliverables, external/reference artifacts, generated documentation, validation material, and similar artifacts belong here.

Therefore:

**DO NOT move `docs/ground-truth/**` into `llm/`.**

The ground-truth corpus, reconciliation notes, and `named-resources.md` are data-plane/domain artifacts and should remain under `docs/`.

However, governance and new design/implementation control artifacts must follow the v0.3 layout.

---

## Governance Migration Agent

Launch a bounded **Governance Migration Specialist** first.

Its job is to inspect the v0.2 → v0.3 migration instructions in `djjay0131/agentic-governance` and produce the exact repo-local migration required for `agentic-kg`.

Expected areas include, but are not limited to:

- move `docs/governance-delta.md` into the declared governance directory
- move `docs/adr/` into the declared ADR directory if applicable
- add `## Repository Layout` to the governance delta
- preserve and explicitly declare this repo's existing:
  - `llm/features/`
  - `llm/memory_bank/`
  - `docs/` artifact plane
- create/declare appropriate governance, ADR, spec and plan paths
- update `CLAUDE.md`
- create/update `AGENTS.md` if required by v0.3
- install the v0.3 routing rule so tools do not recreate control-plane artifacts under `docs/`
- re-express the L0 allowlist using declared paths
- repoint governance check invocation from the old location to:
  `plugin/scripts/governance-checks.mjs`
- include `--layout`
- preserve this repo's `--base origin/master` requirement
- update the governance version/pin
- repair affected references
- grep for stale control-plane paths that automated link checking may miss

Do not blindly copy the canonical default layout over established repository conventions.

This repository already uses `llm/features/` as its feature-spec catalog and `llm/memory_bank/` as its memory system. Preserve those established semantics and declare them explicitly.

The migration must be **minimal, intentional, and history-preserving**.

Run the new governance audit/checks after migration.

The Orchestrator must review this work before proceeding.

---

# Phase 3 — Create the Option C Feature Specification

Once governance is current, invoke the repository's specification workflow.

Use:

`/constellize:feature:specify`

The feature should provisionally be identified as:

**E-9 — Named Resource Entity**

unless repository analysis identifies a compelling sequencing/name conflict.

Create the canonical spec under the repository's declared **feature directory**, expected to remain:

`llm/features/`

Suggested filename:

`llm/features/resource-entity.md`

Update `llm/features/BACKLOG.md` appropriately.

Do not implement the feature during this phase.

---

# Required Specification Problem Statement

The existing entity model has:

- Topic
- ResearchConcept
- Model
- Method

It has no semantically correct home for named scholarly resources such as:

- CS-KG
- AI-KG
- ORKG
- Wikidata
- DBpedia
- KG-EmpiRE
- CSO
- SKOS
- PROV-O
- VerbNet
- MAG
- OpenAlex
- Semantic Scholar
- WordNet
- SentenceTransformers
- Stanford CoreNLP
- Nanopublications

These are named artifacts/resources, not generic research concepts.

Treating them as `ResearchConcept` contaminates concept accumulation and downstream evidence reasoning.

The selected target architecture is therefore **Option C: first-class Resource entities**.

---

# Specification Research Team

The Orchestrator should create several parallel read-only research agents before drafting the final spec.

## Agent A — Domain Model / Ontology Architect

Investigate:

- E-1 through E-8 entity semantics
- current Neo4j node and relationship model
- cross-entity normalization
- entity descriptions
- embedding/dedup behavior
- taxonomy hashing
- domain/reference documentation

Answer:

- What precisely is a `Resource`?
- What is explicitly **not** a Resource?
- How is Resource distinct from:
  - Topic
  - ResearchConcept
  - Model
  - Method?
- Should Resource have subtypes/classification?
- Should classification be a property, controlled enum, relationship, or separate taxonomy?
- What relationships should connect Paper → Resource?
- Are relationships like `USES_RESOURCE`, `MENTIONS_RESOURCE`, `BUILDS_ON`, or another vocabulary appropriate?
- Is there a genuine need for Resource ↔ Resource relations?
- How are ambiguous cases handled?
- What does `CSO` being both a resource and a topic/taxonomy source mean semantically?

Produce recommendations, alternatives, risks, and ADR candidates.

Do not modify implementation code.

---

## Agent B — Extraction Architecture Specialist

Trace the complete E-8 extraction path.

Investigate:

- Pydantic/extraction models
- prompts
- section extraction
- LLM client
- entity extractors
- orchestration
- cross-entity routing
- counters/metrics
- error behavior
- provenance fields

Determine exactly what Option C requires to extract named resources independently without stealing true Concepts, Models, or Methods.

Specify:

- prompt changes
- Resource extraction schema
- quoted evidence/provenance requirements
- confidence behavior
- duplicate handling
- ambiguous classification behavior
- negative instructions for the other entity extractors
- failure visibility
- token/cost implications

No code changes yet.

---

## Agent C — Integration / Persistence Specialist

Trace the full write path from extracted entity to Neo4j.

Identify:

- entity models
- repositories
- merge/dedup logic
- embedding generation
- graph constraints/indexes
- ingestion orchestration
- counters
- taxonomy hash/re-ingestion behavior
- APIs or DTOs affected downstream

Determine the smallest coherent implementation that makes Resource first-class end to end.

No code changes yet.

---

## Agent D — Gold Dataset / Validation Specialist

Own the implications for:

`packages/core/tests/extraction/fixtures/ground_truth_chain/`

Inspect:

- `SCHEMA.md`
- human/Claude/reconciled fixtures
- `acceptable_extras.named_resources`
- validation scripts
- segmentation scripts
- diff/scoring machinery

Specify the contract for a new:

`expected_resources`

key.

Determine:

- exact fixture schema
- scoring rules
- precision/recall behavior
- migration of existing `acceptable_extras.named_resources`
- how all eight papers should be reconciled
- how Resource evaluation remains separate from ResearchConcept evaluation

Do not invent labels unsupported by source evidence.

---

## Agent E — Test / Compatibility Specialist

Map the regression surface.

Identify:

- unit tests
- integration tests
- schema tests
- extraction prompt tests
- ingest orchestration tests
- Neo4j tests
- smoke tests
- fixture validation tests
- API compatibility risks

Produce a red/green TDD plan.

The plan must specifically protect against:

1. Named resources being emitted as `ResearchConcept`.
2. True concepts being incorrectly pulled into Resource.
3. Existing E-1 through E-8 behavior regressing.
4. Resource entities losing DOI / quoted evidence / confidence provenance.
5. Duplicate resources fragmenting because of spelling/casing/aliases.
6. Re-ingestion silently skipping Resource changes.
7. Gold metrics conflating Resource and Concept scores.

---

# Phase 4 — Lead Architect Reconciliation

The Orchestrator now reconciles Agents A-E.

Create one coherent feature spec.

The final spec must contain at least:

- Context
- Problem
- Goals
- Non-goals
- Terminology
- Current architecture
- Proposed architecture
- Resource semantic definition
- Boundary rules against Topic/Concept/Model/Method
- Data model
- Resource properties
- Relationship model
- Extraction behavior
- Prompt behavior
- Provenance requirements
- Normalization/dedup behavior
- Merge/integration behavior
- Gold-schema changes
- Validation/scoring behavior
- Migration/backfill behavior
- Compatibility considerations
- Observability/metrics
- Failure behavior
- Security/cost considerations where relevant
- Test strategy
- Rollout sequence
- Acceptance criteria
- Alternatives considered
- Risks
- Open questions
- ADR candidates
- Explicit files/subsystems expected to change

Acceptance criteria must be objectively testable.

Avoid "implement Resource support" as an AC.

Describe observable behavior instead.

---

# Important Design Constraint

Do **not** automatically assume every named software package, ontology, KG, corpus, library, catalogue, or publishing format belongs in one undifferentiated bucket.

The research agents must decide whether a lightweight Resource classification is necessary.

At the same time, **do not turn E-9 into a giant universal ontology project**.

Solve the named-resource hole needed by this research KG while leaving room for future refinement.

Prefer the smallest architecture that is:

- semantically correct
- extensible
- provenance-preserving
- measurable
- compatible with existing entity pipelines

---

# Phase 5 — Specification Review Gate

Before implementation, launch two fresh agents that did **not** author the spec.

## Independent Feature Reviewer

Review for:

- completeness
- internal consistency
- fit with E-1 through E-8
- hidden coupling
- unnecessary scope
- missing migration behavior
- unclear ACs
- ambiguous Resource boundaries

## Governance Auditor

Review against:

- current agentic-governance
- governance level
- design authority
- repository layout
- ADR requirements
- Definition of Done
- workflow-selection policy
- required human-review boundary

The Lead Orchestrator reconciles both reviews into the spec.

Do not let the implementation team silently change architectural decisions later.

If implementation discovers a real spec defect, return it to the Lead Architect and amend the spec visibly before proceeding.

---

# Phase 6 — Implementation Agent DAG

After the spec passes specification review, construct a dependency-aware implementation DAG.

At minimum use these roles.

## Implementation Agent 1 — Core Resource Domain Model

Own only the domain/schema foundation identified by the spec.

Likely concerns:

- Resource data model
- schema constraints/indexes
- shared DTO/result objects
- Resource repository/integration abstractions

Must use TDD.

Do not modify extraction prompts or gold fixtures.

---

## Implementation Agent 2 — Resource Extraction

Depends on the domain contract from Agent 1.

Own:

- Resource extraction model
- Resource extractor
- prompt changes
- negative boundary instructions where required
- provenance fields
- extraction tests

Do not modify gold reconciliation files.

---

## Implementation Agent 3 — Graph Integration & Orchestration

Depends on Agent 1 and the stable extraction contract.

Own:

- merge/dedup integration
- Paper → Resource relationships
- ingestion wiring
- counters/results
- audit/failure reporting
- re-ingestion behavior

Do not redesign the Resource taxonomy.

---

## Implementation Agent 4 — Gold Schema & Evaluation

Can begin once the Resource schema contract is frozen.

Own:

- `expected_resources`
- `SCHEMA.md`
- validation/scoring code
- conversion away from `acceptable_extras.named_resources`
- evaluation tests

Preserve independent Resource versus Concept metrics.

---

## Implementation Agent 5 — Eight-Paper Reconciliation

Depends on Agent 4.

Own only the ground-truth corpus.

Apply the Resource decision consistently across all eight papers.

Each Resource entry must be grounded in the source material according to the fixture contract.

Do not manufacture expected entities merely because the importer emits them.

---

## Implementation Agent 6 — Documentation / Design Surface

Own user-facing/domain documentation affected by the new entity type.

Examples may include:

- entity catalog
- entity relationships
- architecture/design surface
- ground-truth documentation

Do not place governance/spec/plan artifacts in `docs/`.

---

# Phase 7 — Continuous Orchestration Requirements

The Orchestrator must:

- maintain a dependency graph
- launch independent work in parallel where safe
- prevent overlapping write ownership
- integrate completed agent work in dependency order
- run targeted tests after each integration
- preserve agent findings
- commit coherent checkpoints
- update the orchestration record after each wave
- spawn follow-up agents when findings reveal bounded new work
- avoid one giant "do everything" agent

Keep implementation agents focused on their bounded slice.

If two agents need the same file, schedule them sequentially or assign final ownership to one agent.

---

# Phase 8 — Adversarial Verification

After implementation integration, launch fresh reviewers.

## Adversarial Architecture Reviewer

Attempt to prove the implementation violates the spec.

Look especially for:

- Resource/Concept semantic leakage
- hidden coupling with Model/Method
- duplicated normalization logic
- incorrect graph directionality
- alias/dedup failures
- provenance loss
- unhandled ambiguous resource types

## Test Integrity Reviewer

Verify tests are capable of failing.

Look for:

- mocks hiding interface defects
- tests that merely mirror implementation
- fixture assertions with no meaningful negatives
- metrics that can pass despite Resource/Concept contamination
- skipped integration paths

## Governance Auditor

Run an independent final governance review.

Verify:

- correct governance level
- current v0.3 layout
- feature spec exists
- ADRs handled
- control/data plane respected
- required checks pass
- PR workflow respected
- no unauthorized fast-track assumption

The original implementation agents may fix findings, but the reviewers must verify the fixes independently.

---

# Phase 9 — Required Test Gate

Run the narrowest relevant tests continuously, then the repository's full required gate before declaring completion.

At minimum validate:

- Resource extractor tests
- Resource integration tests
- cross-entity normalization regression tests
- ingestion orchestration tests
- Neo4j/schema tests
- ground-truth schema validator
- gold scoring tests
- all E-1–E-8 regressions
- repository lint/type checks
- repository unit suite
- required CI-equivalent checks
- governance checks including `--layout`

Do not declare success because only new Resource tests pass.

---

# Phase 10 — Memory and Backlog Closeout

Launch the appropriate Knowledge Steward / memory workflow.

Use:

`/constellize:memory:update --full`

Reconcile at least:

- `llm/features/BACKLOG.md`
- `llm/memory_bank/activeContext.md`
- `llm/memory_bank/progress.md`
- relevant architecture/system pattern records
- ADR index if an ADR was created
- feature status

Record E-9 according to actual state:

- SPECIFIED
- IMPLEMENTED
- VERIFIED

Do not claim VERIFIED until the verification gates actually pass.

---

# Phase 11 — PR

Prepare one coherent reviewable PR from this workstream.

The PR must clearly separate:

1. governance v0.3 migration
2. E-9 Resource specification
3. E-9 implementation
4. ground-truth/evaluation migration
5. documentation/memory updates

Include:

- governance level
- execution mode: Mode 3 / Ultracode
- problem being solved
- link/path to feature spec
- architectural summary
- agent/workstream summary
- significant design decisions
- ADRs
- tests run and results
- governance-check result
- known limitations
- follow-up issues
- explicit confirmation that `docs/ground-truth/**` remains the data plane while feature/governance artifacts reside under `llm/`

Do not merge the PR yourself unless current governance explicitly authorizes it.

Semantic work requires the appropriate human review.

---

# Completion Report

When finished, report to me using exactly these sections:

## Repository State
- Starting branch state
- Master synchronization performed
- Final branch state

## Governance Migration
- v0.2 → v0.3 changes
- Layout declared
- Governance audit result
- Remaining governance gaps

## E-9 Specification
- Spec path
- Key architectural decisions
- Resource definition
- Boundary rules
- ADRs

## Agent Execution
For each agent:
- role
- scope
- result
- important findings

## Implementation
- major code changes
- graph/schema changes
- extraction changes
- gold/evaluation changes

## Verification
- test suites
- results
- adversarial findings
- governance findings

## Ground Truth
- papers reconciled
- Resource count by paper
- remaining ambiguities

## PR
- PR number/link
- governance level
- review status

## Follow-ups
- intentionally deferred work
- unresolved risks
- recommended next work

Do not give me a generic progress summary.

Give me evidence: paths, commits, tests, counts, and decisions.
