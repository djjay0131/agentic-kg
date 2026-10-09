---
title: "ADR-0005: Isolate the KGIS ledger and KGCS canonical graph inside the staging Neo4j"
nav_exclude: true
---

# ADR-0005: Isolate the KGIS ledger and KGCS canonical graph inside the staging Neo4j

Status: Accepted
Date: 2026-10-05

## Context

The adoption plan's Phase 8 (GCP development deployment) makes the
`KGIS -> KGCS -> canonical graph` path runnable in the staging stack so the
owner can exercise it end to end. That requires the canonical graph and the
KGIS ledger/evidence to live *somewhere*, and the non-negotiable rule is that
they never touch the legacy graph (`Paper`, `Topic`, `RESEARCHES`, the fifteen
relationship types, the denormalized counters, the six vector indexes) that the
API, the agents and the UI still read in production.

Two facts, both inspected rather than assumed, decide the shape:

1. **The staging Neo4j is Community edition.** `infra/main.tf` installs the
   community `neo4j` Debian package (`apt-get install -y neo4j ||
   apt-get install -y neo4j=1:5.15.0`) on a Compute Engine VM, and the CI
   adapter suites run `neo4j:5.26-community`. Community supports exactly **one
   user database**; `CREATE DATABASE` is an Enterprise/Aura feature. A separate
   physical database is therefore not available in staging without provisioning
   new infrastructure.
2. **The canonical adapter was already built for this fallback.** ADR-0003's
   rejected Alternative 3 records UNDETERMINED U-1 and names the fallback: "one
   database with a canonical label prefix and a distinct session factory".
   `agentic_kg.migration.neo4j.schema` resolves U-1 that way — every canonical
   label carries the `Canon__` prefix (`Canon__Identity`, `Canon__Assertion`,
   `Canon__Meta`, `Canon__Version`), every canonical node carries an `ns`
   property, and **every read and write in `neo4j/store.py` is scoped to the
   store's own namespace**. A canonical `DETACH DELETE` cannot match a legacy
   node, and two stores over one database cannot observe each other.

`agentic_kg.migration.neo4j.factory` already exposes `namespace` and `database`
as constructor arguments, so the stronger separation on Enterprise/Aura is one
argument away and requires no code change.

The brief's constraint is explicit: choose the minimal option that needs **no
new cloud resources**, and stop rather than provision if every option needs
infrastructure. Option 1 does.

## Decision

1. **One staging Neo4j database, two disjoint label spaces.** The canonical
   graph lives in the same Community database as the legacy graph, under the
   `Canon__*` labels, scoped to a dedicated namespace. The default namespace for
   a deployment is read from `KGCS_CANONICAL_NAMESPACE` (the CLI and Job default
   it to `canon`; staging is expected to set an explicit value such as
   `staging`). No canonical node, index or constraint can collide with a legacy
   one.

2. **The KGIS ledger and evidence registry stay SQLite, job-local, and
   explicitly ephemeral in staging.** `ShadowStores.at(dir)` under
   `KGIS_LEDGER_DIR` (the Cloud Run Job points it at `/tmp`), or
   `ShadowStores.in_memory()` when unset. The canonical epoch — the only thing
   the read-only API serves — persists in Neo4j, so a Job run is observable
   after it exits. The ledger's Cloud Run ephemerality is a **recorded
   limitation**, not a solved problem: ADR-0003 already defers the backend swap
   behind `CandidateSink`/`LedgerReader` (ADR-0012), and `ShadowStores` states
   the limitation where an operator will read it.

3. **No new cloud resources are provisioned by this decision.** No second VM,
   no Aura instance, no new database. The separation is enforced by the
   `Canon__` prefix and per-store `ns` scoping in existing code.

4. **The weakening is recorded, per ADR-0003's U-1 instruction.** This is
   separation by convention plus scoping, **not** by engine enforcement. A
   future Enterprise/Aura migration should pass a dedicated `database` to
   `canonical_store_from_config` and thereby upgrade convention to enforcement.

## Rationale

**Why not a second database.** Community cannot create one. Provisioning
Enterprise or Aura would satisfy the brief's "stop and report" condition, not
its "no new cloud resources" instruction, and would cost money and an owner
decision for a first slice that can be safely scoped instead.

**Why not a second Neo4j instance.** It is the same class of answer as Aura:
new infrastructure, new credentials, new secret plumbing, new Terraform. It buys
physical separation the fallback's label prefix plus namespace scoping already
gives at the access-path level ADR-0003 actually cares about ("may share one
physical database, never one access path"). The canonical path has its own
store, its own namespace, its own labels and its own session usage; the legacy
`Neo4jRepository` never names a `Canon__` label and the canonical store never
names a legacy one.

**Why the ledger is allowed to stay SQLite.** The canonical graph is the
durable, queryable artifact this slice exists to make observable. The ledger is
the proposal record: valuable across runs, but the first runnable slice does not
claim durable ledger persistence. Keeping it job-local is honest and reverses
cleanly behind the KGIS port when ADR-0012's swap lands.

## Alternatives Considered

### Alternative 1 — separate Neo4j database (Enterprise/Aura)

*Benefits:* physical separation; a projection rebuild physically cannot reach
canonical data.
*Drawbacks:* not available on Community; needs new infrastructure, credentials
and Terraform. **Rejected** — violates the no-new-cloud-resources constraint.

### Alternative 2 — second Community Neo4j instance

*Benefits:* physical separation on community software.
*Drawbacks:* a new VM and secret; more surface for the owner to operate; and the
namespace scoping already provides the access-path separation. **Rejected.**

### Alternative 3 — one graph, canonical and projection labels side by side

*Benefits:* simplest.
*Drawbacks:* the exact condition that produced the legacy defects — one access
path, many writers — and ADR-0003 Alternative 3. **Rejected**, with the `Canon__`
prefix and `ns` scoping as the fix that makes side-by-side labels safe.

## Consequences

### Positive

- The path is runnable in staging with zero new infrastructure.
- Canonical and legacy data are separable by label and namespace, so the API's
  read-only canonical surface and the legacy read paths are disjoint.
- The Enterprise/Aura upgrade is a constructor argument, not a rewrite.

### Negative / Tradeoffs

- Separation is enforced by the adapter's own scoping, not by the engine. A
  canonical read or write that forgot its `ns` filter would see across
  namespaces; the adapter centralises every read and write in `store.py` and
  `reader.py` precisely so that filter has one implementation.
- The KGIS ledger/evidence is lost when a Cloud Run Job's instance scales in.
  Canonical state survives; provenance for a *future* run's candidate ids does
  not. Documented in the runbook and in `ShadowStores.deployment_warning()`.

### Risks

- **A future editor adds a canonical label without the `Canon__` prefix or a
  query without the `ns` scope.** Mitigated by the schema constants being the
  only source of labels and by the adapter tests, and named here so the upgrade
  to a dedicated database is the obvious remedy.
- **The staging namespace is left at the default `canon` and someone later
  reuses the same Neo4j for a second canonical graph.** Use a distinct
  `KGCS_CANONICAL_NAMESPACE` per canonical graph; the runbook says so.

## Impacted Areas

- [x] Data architecture
- [x] Integrations
- [x] Implementation
- [x] Documentation
- [ ] Product
- [ ] Domain model
- [ ] AI architecture
- [ ] UX
- [ ] Security/privacy

## Related Documents

- `llm/plans/2026-09-17-kgis-kgcs-adoption-migration.md` — Phase 8
- ADR-0003 — canonical/projection split and UNDETERMINED U-1
- ADR-0004 — opt-in, commit-pinned migration seam
- `packages/core/src/agentic_kg/migration/neo4j/schema.py` — the label prefix
  and namespace scoping
- `docs/operations/kgis-kgcs-staging-runbook.md` — how to deploy and execute

## Related Issues / PRs

- Phase 8, first slice, targeting `integration/kgis-kgcs-adoption`

## Supersedes

None.

## Superseded By

None.
