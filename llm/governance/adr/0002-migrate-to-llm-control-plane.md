---
title: "ADR-0002: Migrate governance content to the llm/ control plane"
---

# ADR-0002: Migrate governance content to the `llm/` control plane

Status: Accepted
Date: 2026-08-31

## Context

This repo adopted agentic-governance v0.2 (ADR-0001), which placed the
governance delta and ADRs under `docs/`. agentic-governance ADR-0001 later
reversed that split: `llm/` is the control plane — what the project and its
agents run *on* — and `docs/` is the data plane, holding artifacts and
published views.

This repo was left half-migrated. `llm/features/` and `llm/memory_bank/` were
already on the control-plane side while the delta and ADRs stayed under `docs/`.

`docs/` here is a published Jekyll site. That would normally make moving
anything out of it risky — but `docs/_config.yml` already carried:

```yaml
exclude:
  - adr/
  - governance-delta.md
```

Both paths were **already excluded from the site build**. The split this ADR
implements had in effect been decided months ago; it was expressed as an
exclude rule rather than as directory structure. Moving the files changes
nothing about the published site and lets the exclude rule go away.

## Decision

Move the governance delta and the ADR directory into the declared control
plane, and pin the current governance version.

| From | To |
|---|---|
| `docs/governance-delta.md` | `llm/governance/governance-delta.md` |
| `docs/adr/` | `llm/governance/adr/` |

Every move is `git mv`, so history follows. All inbound references are
rewritten except dated historical records in the memory bank and in ADR-0001,
which describe where things *were* and must not be falsified.

`construction/sprints/` is **out of scope**. It is control plane by nature but
has no canonical v0.3 slot, and it is read by two workflows and a site-data
generator. Moving it is a separate decision with its own blast radius.

## Consequences

- The `adr/` and `governance-delta.md` exclusions are removed from
  `docs/_config.yml` — the files are no longer there to exclude.
- `packages/core/tests/docs/test_site_structure.py` globs `docs/**/*.md` for its
  frontmatter assertion. The moved files simply drop out of that set. This is a
  correction, not a regression: the test was asserting Jekyll frontmatter on
  files deliberately excluded from the Jekyll build.
- The L0 allowlist inside the delta referenced its own old paths. Those four
  lines are rewritten; had they been missed, the fast track would have silently
  stopped matching anything.
- The memory bank keeps 6 of the 9 canonical files. ADR-0001 names
  `systemPatterns.md` the interim design authority, so `architecturalDecisions.md`
  may be deliberately absent. Left as-is pending an owner decision rather than
  filled with stubs.

## References

- agentic-governance ADR-0001 (`llm/governance/adr/0001-llm-control-plane-docs-data-plane.md`)
- ADR-0001 in this repo (adoption of v0.2)
