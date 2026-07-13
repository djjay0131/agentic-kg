# Governance Delta: agentic-kg

Status: Approved (bootstrap)
Last updated: 2026-07-13
Governance: agentic-governance v0.2

This file localizes [agentic-governance](https://github.com/djjay0131/agentic-governance)
for this project.

## Mission

agentic-kg ("Agentic Knowledge Graphs for Research Progression") is a
system that ingests scientific papers (arXiv, Semantic Scholar, OpenAlex)
into a Neo4j knowledge graph where research problems are first-class
entities with provenance-tracked extraction, and specialized LangGraph/AG2
agents (Ranking, Continuation, Evaluation, Synthesis) operate over the
graph with human-in-the-loop checkpoints to identify tractable open
problems and propose/execute/validate continuations. Built on the Denario
agent framework, deployed on GCP. agentic-kg is the parent system of the
research-ai family in this portfolio: it is a deployed application, not a
reusable library — that role belongs to its siblings `agentic-kgis` and
`agentic-kgcs`.

## Design-Authority Document

`llm/memory_bank/systemPatterns.md` (interim). This repo has no single
consolidated FDS like its siblings' `docs/superpowers/specs/` documents;
`systemPatterns.md` is the de-facto design authority today by virtue of
being the most current, most complete architecture record. A consolidated
standalone FDS is a recommended follow-up (see Related Repos and the
adoption PR body for tracking).

## Project Principles

These are SYNTHESIZED from statements scattered across the memory bank —
agentic-kg has never written an explicit principles list. Citations point
to where each principle is evidenced today; a future FDS should restate
them directly.

1. **Provenance everywhere.** Every fact/entity written to the graph
   carries DOI + quoted evidence span + confidence score
   (`productContext.md:20,26`).
2. **Research problems — and Topic/Concept/Model/Method entities — are
   first-class graph citizens, not text blobs**
   (`projectbrief.md:9`; `systemPatterns.md:9` dual-entity model).
3. **Confidence-based routing over hard-coded thresholds.** HIGH
   auto-link / MEDIUM single-agent review / LOW multi-agent consensus /
   human escalation (`systemPatterns.md:96-102`).
4. **Rejections and failures are recorded, never silent.**
   `ExtractionFailure` / `normalization_audit` / `IngestionResult`
   counters; conflicting extractions are both preserved rather than
   silently resolved (`systemPatterns.md:123,142-143`).
5. **No implementation without a spec** (Constellize design-first
   workflow; `CLAUDE.md`).
6. **Taxonomy/schema stability via hashing.** `Paper.taxonomy_hash` guards
   incremental re-ingestion against silent drift (`systemPatterns.md:153`).

## Domain Review Questions

- Is provenance preserved for every new fact/entity written to the graph?
- Is any new decision surface routed through confidence tiering rather
  than a hard-coded threshold?
- Is `taxonomy_hash` correctly preserved or invalidated (no silent
  re-ingestion skips, no silent drift)?
- Do failure paths stay visible — recorded, not swallowed?
- Is reproducibility and LLM-call cost accounted for?
- If this touches deploy or CI, does it respect the hard-won Deploy
  Master gate chain (see `llm/memory_bank/activeContext.md`)?

## Memory Bank

Path: `llm/memory_bank/` (all 6 core files present: `activeContext.md`,
`productContext.md`, `progress.md`, `projectbrief.md`, `systemPatterns.md`,
`techContext.md`; actively maintained by the Constellize memory workflow).

**Flagged:** `activeContext.md` is ~80KB / 555 lines and partially stale —
recommend a Constellize `memory:revise` follow-up to restructure it (it has
outgrown a single "current focus" file).

## Roadmap

Path: `llm/features/BACKLOG.md` (live feature catalog) and
`llm/memory_bank/progress.md` (M0-M14 milestone table).

## Governance Check Command

`node ~/code/agentic-governance/governance/scripts/governance-checks.mjs`
(canonical script from the agentic-governance checkout; CI wiring is a
later, separate change — this establish PR is docs-only and does not touch
`.github/workflows/**`.)

## L0 Path Allowlist

```l0-allowlist
allow llm/memory_bank/** path-only
allow docs/adr/README.md index-table-rows
allow docs/adr/[0-9][0-9][0-9][0-9]-*.md status-line-only
allow docs/** link-target-only
deny src/**
deny packages/**
deny scripts/**
deny .github/**
deny docs/_config.yml
deny docs/adr/0000-template.md
deny docs/governance-delta.md
```

## Platform Enforcement Reality

- **Branch protection on `master`: available, currently unset.**
  agentic-kg is a **public** repository (unlike its siblings, which are
  private/free-plan and structurally blocked). Verified 2026-07-13:
  `gh api repos/djjay0131/agentic-kg/branches/master/protection` returns
  `404 "Branch not protected"` — the platform-available/not-yet-configured
  signal, not the siblings' `403` plan-blocked signal.
- **Required status checks: available, currently unset.** `test.yml`,
  `integration-tests.yml`, and `smoke-ingest.yml` already exist and run in
  CI; none is yet marked required. **Recommended follow-up (owner action):**
  enable branch protection on `master` with these three as required status
  checks, plus a PR-review requirement. This establish PR does not enable
  branch protection itself — that is an owner-only settings action, and
  this PR does not touch `.github/workflows/**`.
- **Token/identity model:** agent sessions authenticate with the owner's
  token — steward/auditor/architect are procedural roles, not distinct
  identities; independence is temporal/artifactual, same as the siblings.
- **Hardening path:** enabling required status checks converts today's
  convention-only CI-green expectation into a platform-enforced merge
  gate; this is available now (no plan upgrade needed, unlike the private
  siblings) and only awaits the owner's configuration action.

## Steward Activation Status

Status: INACTIVE

Steward merge authority ships inert (agentic-governance
`docs/l0-fast-track.md` §Per-Repo Activation). No activation ADR or PR
exists; all merges are human-owner-only.

## Milestone Labels

Derived from `llm/memory_bank/progress.md`'s M0-M14 milestone table,
collapsed to a small phase set:

- `phase-ingestion` (data acquisition, PDF extraction, Cloud Run Job
  ingestion — roughly M0-M2, M8.5)
- `phase-extraction` (LLM-based entity/problem extraction, E-1..E-8 V2,
  E-7 cross-entity normalization — roughly M3, M9-M12)
- `phase-matching` (confidence-based routing, dual-entity canonical
  problem architecture, review queue — roughly M7-M8)
- `phase-agents` (Ranking, Continuation, Evaluation, Synthesis LangGraph
  agents — roughly M4)
- `phase-retrieval` (query-facing vector search, graph-RAG, R-series
  backlog — post-M13)
- `phase-production` (deploy/CI health, GCP staging/prod, smoke testing —
  roughly M5-M6, M13-M14)

## Special Labels

- `provenance` — highest-scrutiny changes to the evidence/provenance path
  (DOI + quoted-span + confidence-score writes)
- `deploy` — touches the fragile GCP deploy chain (see
  `llm/memory_bank/activeContext.md` for the current Deploy Master
  recovery state)

## Constitution Adjustments

None.

## Related Repos

- `agentic-kgis` — ships `kg_contracts` (domain-neutral ports layer) and
  `kgis` (ingestion implementations). agentic-kg is a **future consumer**,
  not a current one: the KGIS five-phase adoption plan
  (`agentic-kgis/docs/governance-delta.md` §Related Repos, Phase 3) names
  an "agentic-kg research-paper retrofit" as the migration acid test,
  gated on the six migration-minimum tools. As of this delta, agentic-kg
  has **zero references** to `kg_contracts` in its code — this is recorded
  here so the expected future retrofit stays traceable rather than
  discovered cold.
- `agentic-kgcs` — the curation service; not yet integrated with
  agentic-kg for the same reason.
- `ts-kg`, `vttsi-*` — earlier-generation knowledge-graph repos in the
  same portfolio; reference reading only, no direct dependency.
