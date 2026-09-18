---
title: "ADR-0004: Adopt KGIS/KGCS behind an opt-in, commit-pinned migration seam"
nav_exclude: true
---

# ADR-0004: Adopt KGIS/KGCS behind an opt-in, commit-pinned migration seam

Status: Proposed
Date: 2026-09-18

## Context

We are adopting two external sibling projects — `agentic-kgis` (ingestion;
ships three packages: `kg_contracts`, `kgis`, `kg_eval`) and `agentic-kgcs`
(canonicalisation / entity resolution) — into this repo's pipeline. The
adoption spans several PRs against `integration/kgis-kgcs-adoption`.

Three constraints shape the decision:

1. **Neither project is on PyPI.** `agentic-kgis` carries zero git tags, and
   its version string sat at `0.2.0` across 122 commits. A version specifier
   such as `>=0.2.1` therefore pins nothing reproducible.
2. **The existing pipeline is in production use.** Adoption must not change
   any current behaviour until explicitly switched on.
3. **Both dependencies are optional at first**, because no call sites exist
   yet. Optional dependencies invite unguarded imports — and the bug fixed by
   `agentic-kgis` PR #39, the very commit we pin, *was* an unguarded optional
   import (`import kgis` failed whenever `pytest` was absent).

## Decision

1. Pin both projects to **exact 40-hex commit SHAs**, inside an **opt-in
   `migration` extra** that neither the default install nor CI resolves.
2. Introduce a single config seam, `agentic_kg.migration.config`, with two
   independent env-var flags (`KGIS_INGESTION_ENABLED`,
   `KGCS_RESOLUTION_ENABLED`), **both defaulting to off**.
3. Route every access to the optional packages through a guard,
   `agentic_kg.migration.imports`, that discriminates "extra not installed"
   from "installed but broken" by asking the import system
   (`importlib.util.find_spec`), never by inspecting the raised exception.
4. **PR 2 and onward take an injected `MigrationConfig`** rather than calling
   the `get_migration_config()` singleton inline.

## Rationale

**Commit pins over version specifiers** is forced by constraint 1: nothing
else is reproducible. A SHA is content-addressed, so a force-push cannot
silently swap what we resolve.

**Opt-in extra** satisfies constraint 2 exactly: `pip install ./packages/core`
resolves only the pre-existing dependencies, so the default path is provably
unchanged.

**Guarding on `find_spec`, not on the exception,** is the correction of a real
defect found in review. Comparing `exc.name`'s first segment against the root
package mislabels every *intra-package* failure: on a genuinely installed
kgis, `require_migration_module("kgis.does_not_exist")` reported "not part of
the default install" and the availability check returned `False`. A caller
branching on that would have silently taken the legacy path on a broken
install — reproducing the #39 failure mode in the very module written to
prevent it. Only the import system can answer "is this installed?".

**Injected config (decision 4)** is recorded now, while there are zero call
sites, because `get_migration_config()` is a cached process-global. That is
adequate for a per-deployment legacy/KGIS split, but it cannot express running
both paths in one process to diff them — which the planned three-way
comparison phase requires. The escape hatch already exists (an explicit
`MigrationConfig(...)` overrides the environment); this ADR commits to using
it rather than letting inline singleton calls calcify.

## Alternatives Considered

### Pin by tag or branch

Rejected. `agentic-kgis` has no tags at all. Tags and branches are mutable
refs, so neither is reproducible; `agentic-kgcs` v1.0.0 is pinned by its
commit SHA rather than by the tag name for this reason.

### Make KGIS/KGCS mandatory dependencies

Rejected. It would change the default install and CI immediately, violating
constraint 2, and would make a non-PyPI git dependency compulsory for every
consumer.

### Hold `agentic-kgcs` unpinned until PR #30 merged

Considered and initially chosen, then superseded: #30 merged at
2026-09-18T22:01:37Z and we re-pinned to its merge commit `4eafa83`. Holding
it unpinned would have meant the `migration` extra did not actually install
KGCS, forcing the next PR to redo dependency work before writing any
integration code.

### One master migration flag instead of two

Rejected. KGIS and KGCS are adopted in separate PRs; an operator must be able
to roll one back without reverting the other.

## Consequences

### Positive

- Default install, default runtime behaviour, and CI are provably unchanged.
- Later PRs inherit a resolvable, reproducible target and need no dependency
  work of their own.
- A broken optional install fails loudly and actionably instead of silently
  routing to the legacy path.

### Negative / Tradeoffs

- `[tool.hatch.metadata] allow-direct-references = true` is required for the
  extra (hatchling rejects PEP 508 direct references at *metadata* time, so
  without it even the plain `pip install ./packages/core` that CI runs fails).
  This setting is **project-wide, not extra-scoped**: it also permits a `git+`
  URL in mandatory `project.dependencies`. Mitigated by a CI assertion that
  `project.dependencies` stays free of direct references, plus assertions that
  every migration pin is a 40-hex SHA and that the duplicated extra stays in
  sync between the two pyprojects.
- The extra is declared in two pyprojects (root and `packages/core`), which
  must be re-pinned together — enforced by the sync assertion.

### Risks

- **Availability, not integrity.** A SHA pin cannot be swapped underneath us,
  but a GC'd commit or a renamed/private repo would break resolution. There is
  also no lockfile, so kgis/kgcs *transitive* dependencies remain unpinned.
  This is a pre-existing property of the repo's dependency management and is
  not addressed here.
- After `agentic-kgcs#30`, **`orcid` is no longer a default strong
  namespace**: Author entity resolution must pass it explicitly. Recorded so
  the integration PR does not rediscover it.

## Impacted Areas

- [x] Data architecture
- [x] AI architecture
- [x] Domain-specific systems (see governance delta)
- [x] Integrations
- [x] Implementation
- [x] Documentation

## Related Documents

- `llm/features/BACKLOG.md`

## Related Issues / PRs

- djjay0131/agentic-kg#71 — PR 1: dependency pinning + config seam
- djjay0131/agentic-kgis#39 — unguarded optional import (the pinned fix)
- djjay0131/agentic-kgcs#30 — identifier strength is entity-type relative

## Supersedes

None.

## Superseded By

None.
