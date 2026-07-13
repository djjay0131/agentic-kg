# Contributing to agentic-kg

Status: Active
Last updated: 2026-07-13

This project follows [agentic-governance](https://github.com/djjay0131/agentic-governance)
(see `docs/governance-delta.md` for project specifics).

## Before You Start

1. `llm/memory_bank/activeContext.md`
2. `llm/memory_bank/systemPatterns.md` (interim design authority — see
   ADR-0001)
3. `docs/governance-delta.md`
4. agentic-governance: `docs/architecture-governance.md`,
   `docs/project-operating-system.md`

## Contribution Rules

- No direct commits to `master`. Issue → Branch → Draft PR → Review →
  Merge.
- Branch prefixes: `docs/`, `architecture/`, `feature/`, `research/`,
  `spike/`, `governance/`, `fix/`, ...
- ADRs for durable decisions (`docs/adr/`, use `0000-template.md`).
- Update `llm/memory_bank/` when project context changes (Constellize
  `memory:update` / `memory:revise`).
- Feature work follows the Constellize spec → implement → verify cycle
  (`.claude/skills/constellize:feature:*`; specs live in `llm/features/`,
  master index `llm/features/BACKLOG.md`).
- AI agents: follow assigned scope, identify ADR candidates, never merge
  your own PR.

## Definition of Done

See agentic-governance `docs/definition-of-done.md`. For this repo
additionally: `make test` (core + API suites), `make lint` (`ruff check`
+ `ruff format --check` on `packages/core/src` and `packages/api/src`)
green. Changes touching ingestion or deploy additionally require
`make smoke-local` where practical (see `llm/memory_bank/activeContext.md`
for the current Deploy Master recovery state before touching deploy/CI).
