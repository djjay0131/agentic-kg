---
title: "ADR-0003: KGIS/KGCS ontology mapping and the canonical/projection split"
---

# ADR-0003: KGIS/KGCS ontology mapping and the canonical/projection split

Status: Proposed
Date: 2026-09-18

## Context

agentic-kg is adopting two sibling libraries: `agentic-kgis` (reusable ingestion —
candidates, evidence, the candidate ledger) and `agentic-kgcs` (reusable curation —
entity resolution, curation plans, the executor, epochs, review). agentic-kg keeps the
research domain: the ontology, the four agents, retrieval, the API, the UI and the
deployment.

The legacy write path at `7108c7d` has **no separation between candidate and canonical
state**. Fifty-nine write surfaces write directly into the single live graph the API and
the agents read. `Neo4jRepository` is imported by the API routers, the CLI, the agents and
the extraction integrators alike. There is no ledger, no epoch, no plan/executor split and
no staging label.

Phase-0 discovery produced an inventory of that path and API studies of both libraries.
Phase 1 re-verified the inventory's classifications against the code and reconciled four
analyses — write classification, legacy→KGIS candidate mapping, KGCS→Neo4j projection, and
identity migration — into one specification. Verification changed six classifications; two
of the changes are load-bearing for this decision:

- **The V1 `KnowledgeGraphIntegrator` is unreachable.** `ingestion.py:527` constructs only
  `KGIntegratorV2` (the local variable is misleadingly named `v1_integrator`). The only
  holder of a `KnowledgeGraphIntegrator` is `BatchProcessor`, which has no caller.
- **No *pipeline* path writes `:Problem`.** `create_problem` is otherwise reachable only
  from `SynthesisAgent`, whose four write calls raise `TypeError` on every invocation and
  are swallowed as warnings, and from `scripts/load_sample_problems.py:580` — an
  operator-run demonstration-data loader that also writes `link_problem_to_paper` and
  `create_relation(EXTENDS)`. There is no `POST /problems` endpoint.
- **Three application write surfaces are non-functional**, not one: `/api/reviews/*`,
  `SynthesisAgent`, and `PUT /api/problems/{id}` — whose router passes two arguments
  positionally into `update_problem(problem: Problem, regenerate_embedding: bool = False)`
  and reaches an `AttributeError` on a `str`. The UI calls it. `DELETE` is correctly wired,
  which makes a soft delete the only human mutation a legacy `Problem` can carry.

Four of the twelve legacy labels — `ResearchConcept`, `Model`, `Method`, `ProblemConcept`
— are identified by a cosine-similarity threshold against whichever node happened to be
created first. That identity is not reproducible. For `ProblemConcept` it is worse:
`auto_linker.py:352` stores every new concept without its embedding, so those nodes are
invisible to the vector index they would have to be matched against, and every mention
becomes a new concept forever.

## Decision

Five decisions, recorded together because each depends on the others.

### 1. The candidate/canonical/projection separation is structural, and agentic-kg holds no canonical write

Three stores, three access paths (KGIS ADR-0006/0010):

| Store | Written by | Read by |
|---|---|---|
| Candidate ledger | applications, via `CandidateSink.submit()` — the only application-facing write | review tooling, via `LedgerReader` |
| Canonical graph | the KGCS `PlanExecutor` only, via `Neo4jGraphStore.apply()` | the projector |
| Projection graph | the projector | API routers, agents, UI — read-only |

The fifteen HTTP mutation endpoints that hold a canonical write today become
`CandidateSink.submit()` calls returning 202 with a candidate id. No application-facing
code and no LLM adviser holds a canonical write surface.

### 2. Identity is a reproducible key plus namespaced aliases — never a similarity threshold

No similarity threshold appears in any candidate. A candidate's `semantic_key` is derived
from the source and is hierarchical (`<type>/<namespace>/<key>`, never a UUID):

| Type | Key basis |
|---|---|
| Paper | DOI (or arXiv id) as a namespaced alias |
| Topic | the taxonomy's materialized path, not `(name, level, parent_id)` |
| Author | ORCID / S2 id, else an explicit `paper_local` per-paper identity |
| ResearchConcept / Model / Method | the normalized surface form |
| Problem | `(doi, section, sha(statement))` |

Deciding that two surface forms are the same entity becomes a KGCS entity-resolution
decision producing an audited, replayable, compensable `MERGE_IDENTITIES` operation. A
mention is the candidate; a concept is the resolved canonical identity; `INSTANCE_OF` is
merge lineage, not a stored relation.

### 3. The Neo4j compatibility layer is a projection adapter in agentic-kg, not a KGCS feature

`agentic_kg.projection` reads the canonical graph at the **published** epoch and writes the
legacy-shaped graph the application already reads: 10 live node labels, 15 relationship
type names, 10 `ORDER BY` keys and 6 named vector indexes. No domain concept — `Paper`,
`RESEARCHES`, `taxonomy_hash` — is added to KGIS or KGCS.

The projection is derived and disposable. Every denormalized counter is **recomputed from
edges** at projection time rather than incrementally mutated, which retires counter drift
by construction. Embeddings are recomputed from canonical text, never carried from the
legacy graph.

### 4. Migration re-derives identity from the source and binds legacy ids by evidence

The legacy graph is frozen and snapshotted, ingestion is re-run from the source corpus, and
KGCS decides identity afresh. Legacy ids are then bound to canonical identities by a
**deterministic join**, never by a vector comparison, on a single key:

```
K = ( entity_type , doi , surface , span )
```

where `surface` is the normalized statement or canonical name and `span` is a hash of the
normalized `quoted_text`. Evidence alone is not enough to key on: across the ground-truth
fixtures `(doi, quoted_text)` is unique for problems (40 over 8 papers) but **32 of 194
ResearchConcept/Model/Method entries share a span**, because one enumeration sentence
evidences many entities. `surface` is the discriminator, and it is the same normalized form
that serves as the candidate's semantic key, so the join key and the identity key agree by
construction.

Every legacy id resolves to exactly one of: a canonical identity, or an explicit `unmapped`
record naming its reason. Never two, and never silently a different entity than before.
Many-legacy-to-one-canonical is expected and is the duplicate population being correctly
merged; one-legacy-to-many is a conflation, marked ambiguous and routed to a human. An
`unmapped` id is served as `410 Gone`, never a `404` and never a redirect to a best guess.
After cutover the map is append-only.

### 5. Broken behaviour is not preserved, and changed behaviour is declared

Three named cases:

- **Topics.** Eight read paths traverse `(:Problem)-[:BELONGS_TO]->(:Topic)`, which no
  automated writer produces, and whose `:Problem` label is produced by no pipeline path
  either. The projection *derives* the edge from `Problem → Paper → RESEARCHES → Topic`, so
  those endpoints return non-empty results for the first time. That is a declared behaviour
  change, and any "preserves current behaviour" test over those paths is vacuous.
- **The three non-functional write surfaces.** `/api/reviews/*`, `PUT /api/problems/{id}`
  and `SynthesisAgent` are re-built rather than ported. Preserving an exception is not a
  migration goal. A legacy `Problem` carrying `status='deprecated'` — the value the working
  `DELETE` endpoint writes — is the one human decision in this area that *must* survive
  re-derivation, and it is carried forward as a retraction. The status alone cannot
  distinguish a human delete from a directly-set domain value, though `version > 1` does
  identify soft deletes positively — `version += 1` exists at one line, reachable only from
  the soft-delete path, since the other two callers crash before it. The marker names which
  retractions are certainly right, not which are wrong, so the migration retracts in both
  cases: retracting a merely-deprecated problem is recoverable by a later curation act,
  resurrecting a deleted one is a silent loss.
- **`ProblemConcept.paper_count`.** Legacy writes a constant `1`. The projection computes
  the true value.

## Rationale

**On the separation (1).** It is the only reason to adopt these libraries at all. The
legacy path's defects — counters that drift with no reconciler, a review queue that cannot
execute, an ER population invisible to its own index, agents whose writes silently fail —
are all symptoms of one cause: every writer writes canonical state directly and nothing
adjudicates. Adopting the libraries without the separation would import their APIs and
keep the disease.

**On identity (2).** Cosine-against-first-arriver is not an identity model; it is an
artifact of ingestion order. It cannot be reproduced, replayed or explained, and it is the
root of both the duplicate concept population and the unmigratable-id problem. Substituting
a reproducible key plus an audited merge decision is the single change with the most
downstream value, and it is what KGCS exists to provide.

**On the projection (3).** KGCS is domain-neutral by construction and proves it in its own
test suite by running the same pipeline over two unrelated domain shapes. Pushing `Paper`
and `RESEARCHES` into KGCS would destroy that property for every other adopter to save
agentic-kg one module. The projection also gives the migration its safety valve: the
application keeps reading the shape it already reads, so the cutover is not simultaneously
an API rewrite.

**On re-derivation (4).** Translating legacy nodes directly into candidates is cheaper and
was rejected. It would import the corrupted ER population, the never-stored embeddings and
a fabricated `INSTANCE_OF.confidence = 1.0` — a maximal certainty about an identity
decision that was never made — into the canonical graph permanently. The canonical graph is
the thing the adoption exists to make trustworthy; seeding it with known-bad identity is
self-defeating. The cost is a one-time re-extraction over the corpus, and it is real.

**On honesty (5).** Two of the five named hazards are cases where "preserve current
behaviour" is a trap: the behaviour is an empty result or an exception. A parity test
against those paths passes trivially and proves nothing. Naming each change is cheaper than
discovering it in production, and it is what project principle 4 — *rejections and failures
are recorded, never silent* — requires.

## Alternatives Considered

### Alternative 1 — extend KGCS with the research ontology

Add `Paper`, `Topic`, `RESEARCHES` and the projection rules to KGCS, so agentic-kg needs no
adapter.

*Benefits:* less adopter code; one less module to own.
*Drawbacks:* destroys KGCS's domain-neutrality, which its own test suite is parametrized to
prove and which its release notes name as a deliberate scope boundary; makes every future
agentic-kg ontology change a cross-repo PR under another repo's governance; makes every
other adopter carry agentic-kg's vocabulary. **Rejected.**

### Alternative 2 — translate legacy nodes into candidates (lift-and-shift identity)

Emit one candidate per existing legacy node, carrying its UUID as an alias, so ids map 1:1
and no re-extraction is needed.

*Benefits:* no LLM cost; a trivially total identity map; fastest cutover.
*Drawbacks:* imports the duplicate `ProblemConcept` population, the missing embeddings and
the fabricated identity confidence into the canonical graph, permanently and with a
provenance record that says they were curated. It also makes the migration itself
unreproducible, because the legacy identities it copies are unreproducible. **Rejected.**

### Alternative 3 — one Neo4j graph for both canonical and projection state

Keep a single graph, adding `CanonicalEntity`/`Assertion` labels alongside the legacy ones.

*Benefits:* simplest deployment; no second database; no projection step.
*Drawbacks:* violates KGIS ADR-0006's "may share one physical database, never one access
path"; a projection rebuild's `DETACH DELETE` could reach canonical data; and it recreates
exactly the condition that produced the legacy defects — one access path, many writers.
**Rejected**, with the caveat recorded as UNDETERMINED U-1: if the deployed Neo4j edition
supports only one user database, the fallback is one database with a canonical label prefix
and a distinct session factory, and the weakening from *enforced* to *conventional*
separation must be recorded rather than glossed.

### Alternative 4 — keep the legacy write path and adopt KGIS for ingestion only

Adopt the candidate/evidence/ledger leg; leave curation and the canonical graph as they are.

*Benefits:* much smaller change; keeps the API contract untouched.
*Drawbacks:* the ledger becomes write-only decoration, because nothing adjudicates its
contents; every hazard except evidence capture survives; and the "no application-facing
canonical write" property — the one that actually fixes the class of defect — is never
achieved. **Rejected** as a stopping point, though it is a legitimate *intermediate* state
during the rollout.

## Consequences

### Positive

- Counter drift becomes unrepresentable rather than merely reconciled; three `ORDER BY`
  keys on paginated endpoints stop lying.
- Entity resolution becomes reproducible, replayable, audited and compensable, with
  thresholds as configuration rather than module constants.
- Every canonical fact carries evidence coordinates and a multi-axis score, satisfying
  project principle 1 (*provenance everywhere*) at the contract level rather than by
  convention.
- Eight topic-scoped endpoints and both the Ranking and Continuation agents start returning
  results.
- Four dead write surfaces, three non-functional ones, and two pieces of phantom vocabulary
  (`SOLVED_BY`, `ProblemMention.workflow_state`) are identified — the phantom vocabulary for
  deletion, with the existing `HAS_TOPIC` regression guard explicitly preserved.
- Re-ingestion stops being a destructive purge and becomes a new curation epoch.

### Negative / Tradeoffs

- **A one-time re-extraction over the corpus**, with real LLM cost and latency.
- **Author nodes multiply.** Replacing name-matching with an honest `paper_local` identity
  turns a silent false merge into an honest under-merge plus review workload.
- **The API's mutation semantics change** from 200-with-object to 202-with-ticket, because a
  change is not visible until an epoch is published.
- agentic-kg owns three things the libraries do not ship: the Neo4j `GraphMutationStore`,
  the `ErDecision → CurationPlan` bridge, and the projection adapter.
- Structured observations (metric/dataset/value/unit) stay flattened, because
  `ObservationCandidate` is defined but not implemented in v1. This is not a regression —
  the legacy graph stores them as JSON strings — but it caps what the adoption can later
  unlock.

### Risks

- **The re-derivation produces a materially different graph and nobody notices which
  differences are fixes and which are regressions.** Mitigated by the 8-paper CS-KG
  ground-truth set as the parity fixture, by the anti-vacuity rule on parity tests, and by
  publishing the collision count as a migration metric.
- **An upstream defect defeats seed-model authority.** `select_survivor` ranks an absent
  `source_reliability` lowest, and `DefaultNormalizer` never populates it for a
  `CanonicalEntity`, so a fresh candidate always outranks an established identity as merge
  survivor — unconditionally, since `source_reliability` is required on a candidate, so even
  `0.0` wins. The fix is split: `CanonicalEntity` has no reliability field at all and lives
  in `kg_contracts`, which ships from **KGIS**, so giving it one is a contract change there;
  independently, **KGCS** can make `select_survivor` prefer an established identity when
  reliability is incomparable. Until either lands, the adopter's bridge refuses such a merge
  and routes it to review rather than reimplementing survivorship.
- **The reconciled ground-truth set is 2 papers, not 8.** Only `reconciled/` is an answer
  key, and it holds `cskg` and `cskg2`; the 8-paper framing describes the citation chain,
  whose 10 verified edges are separately usable for `CITES` parity. Entity-level parity
  claims are therefore narrow until the reconciled set is widened, and the acceptance
  criteria say so rather than implying coverage that does not exist.
- **Checks that verify nothing are this work's characteristic failure.** Five instances so
  far: a parity test over two empty results, a criterion quantified over a status value that
  does not exist, a normalization function defined twice and 13% apart, a cited fixture that
  does not hold what was claimed, and a criterion asserting a local re-implementation of an
  upstream rule. Each read as rigorous. The spec now carries a standing rule (§9.0) that a
  test which can pass without exercising the thing it names is a defect; the risk is that the
  rule is applied to the criteria that exist and not to the next ones written. A sixth
  instance was then found *after* the rule existed and *permitted by it* — a criterion
  comparing the projector against its own defining formula — which is why the rule now carries
  a fifth obligation: **a criterion must be able to fail for the reason it names.** The rule is
  backed by review discipline alone; no mechanism enforces it, and that is disclosed rather
  than papered over.
- **Five UNDETERMINED items remain genuinely open.** The cheapest and most gating is a
  read-only node/edge count against the live database: it settles the review-queue
  disposition, the `Problem` population, and the duplicate rate at once, and needs no code.
  Committing to an implementation sequence before running it is the main way this decision
  could be built on sand.
- **The canonical/projection split is untestable in CI as designed.** CI runs
  `neo4j:5.26-community`, which supports one user database. The separation mechanism must be
  swappable — two databases, or one database with a canonical label prefix and a distinct
  session factory — and CI exercises the fallback.
- **The SQLite ledger does not fit Cloud Run.** One file, WAL, single-writer, ephemeral
  filesystem, horizontal autoscaling. A backend decision is deferred to the deployment PR;
  ADR-0012 permits a swap behind the same ports.
- **The Phase-3 adopter gate may not be open.** KGIS governance places agentic-kg at Phase 3
  and requires six migration-minimum tools first. Unconfirmed.

## Impacted Areas

- [x] Product
- [x] Domain model
- [x] Data architecture
- [x] AI architecture
- [x] Domain-specific systems (see governance delta)
- [x] Integrations
- [x] UX
- [ ] Security/privacy
- [x] Implementation
- [x] Documentation

> Security/privacy is unticked deliberately. Subject-scoped erasure is absent across the
> whole stack — `kg_contracts.security` ships the `DeletionBehavior`/`PolicyContext` shapes
> but no executable purge — and KGCS's own release note calls it *required before ingesting
> regulated personal data*. For a corpus of published papers and published author names
> this is tolerable; it becomes a hard blocker the moment any personal data enters, and it
> is not agentic-kg's to close.

## Related Documents

- [KGIS/KGCS Adoption — Ontology & Mapping Specification](../../features/kgis-kgcs-ontology-mapping.md)
- `llm/governance/governance-delta.md` — project principles and Domain Review Questions
- Upstream governance: KGIS ADR-0004 (dual ingestion modes, `CandidateScores` replaces a
  single confidence), ADR-0006 (three-store separation), ADR-0008 (identity model),
  ADR-0010 (write-path mechanism), ADR-0011 (canonical reads are canonical-only),
  ADR-0012 (ledger persistence), ADR-0013 (revoke and erasure), ADR-0021 (fail-closed
  narrowings binding external `GraphMutationStore` adapters)

## Related Issues / PRs

- Phase 1, PR 2 of the KGIS/KGCS adoption, targeting `integration/kgis-kgcs-adoption`
- Upstream pins: `agentic-kgis` @ `baf0b67e34bd2e7921b5da4492860f741d165464`;
  `agentic-kgcs` read at `39203ad`

## Supersedes

None.

## Superseded By

None.
