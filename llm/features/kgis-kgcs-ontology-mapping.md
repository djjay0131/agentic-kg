# Feature: KGIS/KGCS Adoption — Ontology & Mapping Specification

**Status:** SPECIFIED
**Date:** 2026-09-18
**Author:** Ontology / Mapping Architect (AI-assisted)
**Backlog ID:** KGX-2 (Phase 1, PR 2 of the KGIS/KGCS adoption)
**Target branch:** `integration/kgis-kgcs-adoption`
**Baseline commit:** `7108c7d`
**KGIS pin:** `baf0b67e34bd2e7921b5da4492860f741d165464` (merge of upstream PR #39)
**KGCS read at:** `39203ad`

> **This spec contains no implementation and prescribes none in this PR.** It is the
> reconciled output of the four Phase-1 analyses (write classification, legacy→KGIS
> candidate mapping, KGCS→Neo4j projection, identity migration). Every downstream PR in
> the adoption must trace to a decision here. The durable decisions are recorded
> separately in [ADR-0003](../governance/adr/0003-kgis-kgcs-ontology-mapping.md).

---

## 0. Scope, non-goals, and what this spec is built on

### 0.1 Scope

The mapping between three things that do not currently exist together:

1. the legacy agentic-kg Neo4j write path (59 write surfaces, 12 node labels,
   16 relationship-type names) at `7108c7d`;
2. the KGIS candidate/evidence contract (`kg_contracts` 2.0.0) at the pinned commit;
3. the KGCS curation core and executor at `39203ad`.

### 0.2 Non-goals

- **Not a rewrite.** agentic-kg keeps research-domain semantics, the ontology, the four
  agents, retrieval, the API, the UI and the deployment. KGIS owns reusable ingestion;
  KGCS owns reusable curation. Nothing in this spec proposes copying KGIS or KGCS
  functionality into agentic-kg.
- **No code.** No file under `packages/`, `scripts/` or `.github/` is touched by this PR.
- **Not a schedule.** Sequencing belongs to the adoption plan, not to the mapping.

### 0.3 Prior work this builds on, and how it was checked

This spec builds on the Phase-0 discovery reports (legacy inventory, KGIS API, KGCS API).
Their classifications were **re-verified against the code at `7108c7d`**, and §1.2 records
the six corrections that verification produced. Two discovery findings were re-checked
against upstream and are now **closed**:

| Discovery finding | Status at the pin | Evidence |
|---|---|---|
| KGIS B1 — `import kgis` fails without `pytest` | **FIXED** | `baf0b67e:src/kgis/evidence/__init__.py` replaces the eager `EvidenceRegistryContract` import with a PEP 562 `__getattr__`. `import kgis` no longer drags `pytest`. |
| KGCS D-6 — `issn`/`isbn` in `DEFAULT_STRONG_NAMESPACES` auto-merges distinct papers | **FIXED** | `39203ad:src/kgcs/er/normalize.py:365` is now `frozenset({"doi", "vin"})`. **Note the consequence: `orcid` is no longer a default either**, so Author ER must pass `strong_namespaces={"doi", "orcid"}` explicitly (§3.3). |

**Independent architecture review, 2026-09-18.** A reviewer re-ran the verification and
found three defects in the first draft of this spec. All three are corrected in place and
each correction is marked where it lands: a false generalisation about the `Problem` label
(§1.2 C-2, §3.6, §6.3), a migration join key that was both non-unique and stated three
different ways (§6.4.1), and acceptance criteria pointed at a fixture that does not exist
(§5.2.1). Correcting the first turned up a fourth, previously unrecorded defect in the
application (§1.2 C-7). The corrections are recorded as corrections rather than silently
folded in, because "the spec once claimed X" is itself evidence about how far the code
resists being understood.

**Pass 2, same day.** The repair introduced two further defects, both now fixed: an
acceptance criterion bound to a status value that does not exist (§6.3, AC-12b) and a
normalization function stated twice with two different definitions (§3.1). Both are the same
shape — a check that reads as rigorous and verifies nothing — and a self-audit prompted by
that observation found two more (AC-4, AC-9). §9.0 names the pattern and sets a standing rule
against it, because at five occurrences it is a property of the work, not an accident.
Pass 2 also confirmed, by independent re-implementation over 271 fixture records, that the
§6.4.1 join key yields **0 residual collisions**.

---

## 1. Analysis A — write classification

### 1.1 Method and the one change to the taxonomy

Every write surface touching `Paper`, `Topic`, `ResearchConcept`, `Model`, `Method`,
`Problem`/`ProblemMention`/`ProblemConcept`, or `CITES` was traced from its Cypher to its
call sites at `7108c7d`.

**Taxonomy change: classify the *act*, not the *function*.** The discovery inventory
assigned one class per writer. Several writers perform two acts in one statement — e.g.
`assign_entity_to_topic` (`repository.py:1365-1375`) both records a relation *and*
increments a denormalized counter. A single label forces a wrong answer about where the
code goes. Each row below therefore carries a **primary** class and, where it applies, a
**secondary** one. A row whose primary class is `dead` has no production call path.

Classes: `source-acquisition` · `extraction` · `candidate-construction` · `curation` ·
`projection` · `application-only` · `dead`.

### 1.2 The six corrections to the discovery classification

| # | Discovery said | Verified finding | Correction |
|---|---|---|---|
| C-1 | `KnowledgeGraphIntegrator` (V1) writes `Problem` concurrently with V2 (`ingestion.py:721`); classified **candidate-construction** | `ingestion.py:527` constructs **only** `KGIntegratorV2` — the local variable is misleadingly named `v1_integrator` ("phase 1 of integration", not "the V1 class"). The only holder of a `KnowledgeGraphIntegrator` is `BatchProcessor` (`extraction/batch.py:358`), and `BatchProcessor` **has no caller**: `cli.py:30` imports `BatchConfig` only, and `/api/extract/batch` (`routers/extract.py:91`) never touches it. | **`dead`.** Rows 1, 42, 59 and the V1 leg of row 41 are unreachable code, not a live write path. |
| C-2 | `Problem` is a live candidate-construction label | Three narrower claims hold: (a) there is **no `POST /problems` endpoint** — `routers/problems.py` has only `GET`, `PUT` (`:114`) and `DELETE` (`:138`); (b) **no pipeline path writes `:Problem`** (C-1); (c) `SynthesisAgent`'s four calls genuinely raise `TypeError` — `create_problem(id=, statement=, status=)` against `create_problem(problem: Problem, …)` — and swallow it at `:180`. **But `scripts/load_sample_problems.py:580` calls `repo.create_problem(problem)` and, in the same loop, `link_problem_to_paper` and `create_relation(EXTENDS)`.** It is an import-valid, operator-runnable script. | **`Problem` is written — by an operator-run sample-data loader, not by the pipeline.** The class is `application-only (operator script)`, not `dead`. See §3.6 and §6.3 for the two sections that depend on this. |
| C-3 | `merge_topic` / `load_taxonomy` / `load_seed_models` are **curation/ER** | `merge_topic` (`repository.py:972-1012`) is a deterministic `MERGE` on the natural key `(name, level, parent_id)`. No similarity, no threshold, no resolution. The source is a YAML file. | **`source-acquisition` (reference data).** Routing a deterministic reference-data load through probabilistic ER would make Topic identity threshold-dependent, which it is not today and must not become. |
| C-4 | `assign_entity_to_topic` and `_link_entity_to_node` are **curation/ER** | The pipeline caller (`kg_integration_v2.py:888-914`) resolves a topic by exact name (`get_topic_by_name`) and writes the edge unconditionally above a code-constant threshold. No resolution step exists. | **`candidate-construction`** (primary) + **`projection`** (secondary, the counter delta). The *auto-accept* is the part that becomes a KGCS policy decision. Only `create_or_merge_research_concept` / `_model` / `_method` (the embedding dedup at `repository.py:1730`, `:2384`, `:2835`) are genuinely `curation`. |
| C-5 | `_set_paper_extraction_metadata` / `_set_paper_normalization_audit` are **projection** | `taxonomy_hash`, `extraction_incomplete`, `extraction_failed_extractors` and `normalization_audit` describe a *pipeline run*, not a fact about the paper. Their only consumer is `ingestion.py:376-388` (the re-ingest cost guard) plus `queries/completeness.py`, which has no caller. **No API router reads them.** | **`extraction` (run-state).** They belong in the KGIS run ledger, projected onto `Paper` only if the cost guard is kept. |
| C-6 | `purge_paper_extraction` is **projection** | `re_ingestion.py:140-225` issues seven raw destructive statements against live canonical state from inside the ingestion path. | **`curation` (destructive).** It disappears entirely under epoch re-publication (§4.6); calling it projection understates what it is doing today. |
| C-7 | *(new, found while correcting C-2)* `PUT /api/problems/{id}` is a working application write | `routers/problems.py:133` calls `repo.update_problem(problem_id, problem)` **positionally** against `update_problem(self, problem: Problem, regenerate_embedding: bool = False)`. So `problem` binds to a `str` and `regenerate_embedding` binds to a truthy `Problem`. Execution reaches `repository.py:374` `problem.updated_at = …` on a `str` and raises `AttributeError`, uncaught → HTTP 500. The UI calls it (`packages/ui/src/lib/api.ts:194`). | **Non-functional — a third broken application write surface**, alongside `/api/reviews/*` (§5.3) and `SynthesisAgent`. `DELETE` (`:144`, `repo.delete_problem(problem_id, soft=True)`) **is** correctly wired, so a soft delete is the *only* human mutation a legacy `Problem` can have received. §6.3 depends on this. |

### 1.3 The classification table

Row numbers reference the discovery inventory §1. `→` names the post-migration home.

#### Paper

| # | Writer | file:line | Primary | Secondary | → |
|---|---|---|---|---|---|
| 4 | `create_paper` | `repository.py:552` | source-acquisition | — | KGIS `EntityCandidate` + `AttributeAssertionCandidate` |
| 5 | `update_paper` | `repository.py:615` | source-acquisition | — | KGIS re-submission (same semantic key ⇒ idempotent) |
| 6 | `delete_paper` | `repository.py:651` | application-only | — | KGCS `RETRACT_ASSERTION` / ledger revoke; CLI loses its direct write |
| 32 | `create_or_promote_paper_stub` | `repository.py:3142` | source-acquisition | — | KGIS — a stub is an `EntityCandidate` with an alias and **no** attribute assertions |
| 33 | `_promote_paper_stub` | `repository.py:3182` | dead | — | no caller; delete |
| 30 | `link_paper_cites_paper` (`CITES` edge) | `repository.py:3086` | source-acquisition | projection (the two counters) | KGIS `RelationCandidate`; counters → projection |
| 31 | `unlink_paper_cites_paper` | `repository.py:3119` | dead (tests only) | — | delete |
| 35 | `_set_paper_extraction_metadata` | `kg_integration_v2.py:818` | extraction (run-state) | — | KGIS run ledger |
| 36 | `_set_paper_normalization_audit` | `kg_integration_v2.py:838` | extraction (run-state) | — | KGIS evidence record |
| 48 | purge `SET p.extraction_incomplete=false` | `re_ingestion.py:221` | curation (destructive) | — | disappears |
| 57 | `PaperImporter.import_paper` | `importer.py:224,239` | source-acquisition | — | KGIS `IngestPipeline` |
| 58 | `populate_citations` | `citation_graph.py:135,149` | source-acquisition | — | KGIS |

#### Author (carried because Paper identity depends on it)

| # | Writer | file:line | Primary | → |
|---|---|---|---|---|
| 7 | `create_author` | `repository.py:701` | source-acquisition | KGIS `EntityCandidate` |
| 8 | `link_paper_to_author` | `repository.py:734` | source-acquisition | KGIS `RelationCandidate(AUTHORED_BY)` |
| 9 | `update_author` | `repository.py:793` | application-only | KGCS curation request |
| 43 | `relations.link_paper_to_author` | `relations.py:450` | dead (duplicate writer) | delete — it is the source of the `position` vs `author_position` collision |

#### Topic

| # | Writer | file:line | Primary | Secondary | → |
|---|---|---|---|---|---|
| 10 | `create_topic` | `repository.py:911` | source-acquisition (ref data) | — | KGIS |
| 11 | `merge_topic` | `repository.py:972` | source-acquisition (ref data) | — | KGIS `IngestPipeline` over the taxonomy YAML |
| 12 | `update_topic` | `repository.py:1139` | application-only | — | KGCS curation request |
| 13 | `delete_topic` | `repository.py:1166` | application-only | — | KGCS |
| 14 | `link_topic_parent` | `repository.py:1194` | source-acquisition (ref data) | — | KGIS `RelationCandidate(SUBTOPIC_OF)` |
| 15 | `assign_entity_to_topic` | `repository.py:1365` | candidate-construction | projection (counter) | KGIS `RelationCandidate(RESEARCHES` / `BELONGS_TO)`; counter → projection |
| 16 | `unassign_entity_from_topic` | `repository.py:1424` | application-only | projection | KGCS `RETRACT_ASSERTION` |
| 17 | `reconcile_topic_counts` | `repository.py:1466` | projection | — | **disappears** — the projection recomputes, so there is nothing to reconcile |
| 52-54 | `v3_topic_migration` (3 writers) | `v3_topic_migration.py:56,202,249` | curation (one-shot) | — | historical; retire (do **not** replay) |
| 56 | `taxonomy._load_subtree` | `taxonomy.py:227` | source-acquisition (ref data) | — | KGIS |

#### ResearchConcept

| # | Writer | file:line | Primary | Secondary | → |
|---|---|---|---|---|---|
| 18 | `create_research_concept` | `repository.py:1557` | curation | — | KGCS `CREATE_IDENTITY` |
| 19 | `update_research_concept` | `repository.py:1634` | curation | — | KGCS `ATTACH_ASSERTION` |
| 20 | `delete_research_concept` | `repository.py:1668` | application-only | — | KGCS |
| — | `create_or_merge_research_concept` | `repository.py:1730` | **curation (ER)** | — | KGCS ER — this is the entity resolution |
| 21 | `_link_entity_to_node` (`DISCUSSES`, `INVOLVES_CONCEPT`) | `repository.py:1852` | candidate-construction | projection (counter) | KGIS `RelationCandidate`; counter → projection |
| 22 | `_unlink_entity_from_node` | `repository.py:1893` | application-only | projection | KGCS |
| 23 | `reconcile_research_concept_counts` | `repository.py:2018` | projection | — | disappears |

#### Model / Method

| # | Writer | file:line | Primary | → |
|---|---|---|---|---|
| 24, 27 | `create_model` / `create_method` | `repository.py:2113`, `:2596` | curation | KGCS `CREATE_IDENTITY` |
| 25, 28 | `update_model` / `update_method` | `repository.py:2231`, `:2695` | curation | KGCS `ATTACH_ASSERTION` |
| 26, 29 | `delete_model` / `delete_method` | `repository.py:2287`, `:2738` | application-only | KGCS |
| — | `create_or_merge_model` / `_method` | `repository.py:2384`, `:2835` | **curation (ER)** | KGCS ER |
| 21 | `USES_MODEL` / `APPLIES_METHOD` links | `repository.py:1852` | candidate-construction | KGIS `RelationCandidate` |
| 55 | `load_seed_models` | `seed_models.py:124` | source-acquisition (ref data) | KGIS, under a distinct `producer` and a client-authoritative curation profile (§3.3) |
| — | `Model.usage_count` / `Method.usage_count` deltas | `repository.py:1856` | projection | projection — **no reconciler exists today** |

#### Problem / ProblemMention / ProblemConcept

| # | Writer | file:line | Primary | Secondary | → |
|---|---|---|---|---|---|
| 1 | `create_problem` | `repository.py:278` | **application-only (operator script)** (C-2) | — | `scripts/load_sample_problems.py:580`; becomes a `CandidateSink.submit()` under a demonstration-data producer, or is retired with the script |
| 2 | `update_problem` | `repository.py:352` | **non-functional** (C-7) | — | its only caller passes arguments positionally and crashes; re-built as a KGCS curation request |
| 3 | `delete_problem` | `repository.py:419` | application-only (**works**) | — | KGCS — and the *only* human mutation a legacy `Problem` can carry (§6.3) |
| 34 | `_store_mention_node` (`ProblemMention` + `EXTRACTED_FROM`) | `kg_integration_v2.py:742` | **candidate-construction** | — | KGIS — a mention **is** the candidate + its evidence (§3.6) |
| 37 | `_create_instance_of_relationship` | `auto_linker.py:260` | curation (ER) | projection (counter) | KGCS ER decision + `MERGE_IDENTITIES` |
| 38-39 | `_create_concept_and_link` | `auto_linker.py:347,356` | curation (ER) | — | KGCS — **and the source of hazard 4** (§5.4) |
| 40 | `_update_concept_after_refinement` | `concept_refinement.py:230` | curation | — | KGCS re-canonicalization |
| 41 | `create_relation` (`EXTENDS`/`CONTRADICTS`/`DEPENDS_ON`/`REFRAMES`) | `relations.py:130` | dead via V1 (C-1); **application-only via `load_sample_problems.py`**; non-functional via synthesis | — | vocabulary declared; data disposition in §3.6 |
| 42 | `link_problem_to_paper` | `relations.py:375` | dead via V1 (C-1); **application-only via `load_sample_problems.py:585`** | — | KGIS `RelationCandidate(EXTRACTED_FROM)` if the script is kept; otherwise retired with it |
| 44-47 | `ReviewQueueService` (4 writers) | `review_queue.py:172,315,351,399` | **non-functional** | — | scoped out as data; re-built on KGCS `ReviewQueue` (§5.3) |
| 48 | `purge_paper_extraction` | `re_ingestion.py:140-225` | curation (destructive) | — | **disappears** — replaced by epoch re-publication |
| 59 | `KnowledgeGraphIntegrator` (V1) | `kg_integration.py:257,268,396` | **dead** (C-1) | — | delete |

#### Infrastructure

| # | Writer | file:line | Primary | → |
|---|---|---|---|---|
| 49 | `persist_ingestion_run` | `job_runner.py:91` | source-acquisition (provenance) | KGIS run ledger; projected as `IngestionRun` for `/api/ingest` |
| 50-51 | `SchemaManager` DDL | `schema.py:374-410` | projection (DDL) | **adopter's projector**, not KGCS |

### 1.4 Class summary

| Class | Writers | Post-migration home |
|---|---|---|
| source-acquisition | 14 | KGIS (`IngestPipeline`) |
| extraction | 2 (+9 pure extractors that write nothing) | agentic-kg (domain), emitting to KGIS |
| candidate-construction | 3 (rows 15, 21, 34) | KGIS (`ExtractionPipeline` → `CandidateSink`) |
| curation | 12 | KGCS |
| projection | 6 | adopter's projector |
| application-only | 12 repository methods behind **15 HTTP endpoints**, plus 3 reached only from `scripts/load_sample_problems.py` | lose their canonical write; become `CandidateSink.submit()` |
| non-functional | 3 surfaces: `/api/reviews/*` (§5.3), `PUT /api/problems/{id}` (C-7), `SynthesisAgent` | re-built, not ported — "current behaviour" is an exception |
| dead | 4 (rows 33, 43, 59, and the V1 leg of 41) | delete |

The 15 HTTP mutation endpoints that hold a canonical write surface today —
`routers/problems.py:114,138`; `topics.py:263`; `concepts.py:135,218,242`;
`models.py:133,193,214`; `methods.py:123,180,201`; `reviews.py:179,206,233` — plus
`agents/synthesis.py:153,168,187,205`, are the complete set that must be re-pointed. That
is the whole of the "no application-facing canonical write surface" cutover.

---

## 2. Target architecture in one picture

```
 sources (arXiv / OpenAlex / S2)   taxonomy.yaml   seed_models.yaml   PDFs
        │                              │                │              │
        └──────────── KGIS IngestPipeline / ExtractionPipeline ────────┘
                                   │ Candidate + Evidence
                                   ▼
                     SQLite candidate ledger  ── LedgerReader ──► review UI
                                   │
                                   ▼
                       KGCS CurationEngine → CurationPlan
                                   │
                                   ▼
                  KGCS PlanExecutor ──apply──► Neo4jGraphStore   [CANONICAL]
                                   │                 identities + assertions
                              EpochPublisher               (epoch-stamped)
                                   │
                                   ▼  published epoch only
                       agentic_kg.projection (adopter-owned)
                                   │
                                   ▼
                     Neo4j projection graph   [DERIVED, DISPOSABLE]
                       legacy labels/edges/counters/vector indexes
                                   │
                                   ▼
                        API routers · agents · UI  (read-only)
```

Three rules hold this together and every downstream PR is checked against them:

1. **Applications hold `CandidateSink` (write) and a projection reader (read). Nothing else.**
   `GraphMutationStore` is reachable only from the KGCS executor (KGIS ADR-0010,
   KGCS §9 law 2).
2. **The canonical store and the projection graph are never the same access path**
   (KGIS ADR-0006). See §4.2 for how that is made structural.
3. **No LLM holds a write surface.** Advisers return inert value objects clamped by a
   deterministic profile predicate, verified upstream by four independent mechanisms.

---

## 3. Analysis B — legacy → KGIS candidate mapping

### 3.1 Global declarations

| Declaration | Value | Constraint satisfied |
|---|---|---|
| `graph_id` | `research` | `^[a-z][a-z0-9-]*$` (`kg_contracts/identity.py:28`). **Not** `agentic_kg` — underscores are illegal. |
| `ontology_version` | `2026-09-18.1`, bumped on any term-set change | rides every `CandidateEnvelope` |
| `contract_version` | `2.0.0` | supplied by `kg_contracts` |
| Semantic-key grammar | `<type>/<namespace>/<key>`, hierarchical, never a UUID | KGCS `UniqueSourceConstraint` and `SourceKeyChannel` `rpartition` on `/`; a flat key disables both silently (KGCS D-14) |
| Embedding representation key | `text-embedding-3-small@1536` | `Representation(kind="vector")`; cross-producer embeddings are incomparable unless the key is standardized (KGCS ADR candidate 0006) |
| **`NORM(s)`** — the one normalization | `NFKC` -> rejoin a hyphen broken across a line -> collapse whitespace -> `casefold()`. **Punctuation is preserved.** | §3.3, §6.4.1 and the identity map all call `NORM`; see the box below |

> **`NORM` is defined here and nowhere else, and that is a correctness requirement, not
> tidiness.** An earlier draft stated it twice — §3.3 said "punctuation-stripped", §6.4.1 did
> not — and the two disagreed on **26 of 194 (13%)** of the fixtures' Concept/Model/Method
> names: `fine-tuning`, `part-of-speech tagging`, `DeLong's test`, and §6.4.1's own headline
> example `paraphrase-distilroberta-base-v2`. Since §3.3 mints the candidate's key and §6.4.1
> computes the migration join, a 13% disagreement means 13% of legacy ids find **zero**
> matches and are silently filed `unmapped / no_canonical_counterpart` — a silent mapping loss
> inside the mechanism meant to prevent one. "The join key and the identity key agree by
> construction" is true **only** while there is exactly one definition. A future section
> needing normalization cites `NORM`; it does not restate it.
>
> **Punctuation is preserved deliberately.** Hyphens and apostrophes are semantic here —
> `paraphrase-distilroberta-base-v2`, `part-of-speech tagging`, `DeLong's test` — and
> stripping them merges distinct entities. The line-break rule is narrower and is the one
> corruption the gold files actually document; the ligature case is handled by NFKC. This
> exact definition was re-implemented by an independent reviewer and measured over 271 fixture
> records from four sources — `reconciled/` (59), importer output (40), `human/` (89),
> `claude/` (83) — yielding **0 residual collisions** against a 10-16% baseline. Changing
> `NORM` invalidates that measurement and obliges re-running it.

**Ontology term sets must all three be non-empty.** `kgis/ontology.py:67,70,73` reads
`return not self.entity_types or term in self.entity_types` — an **empty set means
unconstrained, not forbidden**. Declaring entity types but leaving `relation_types` empty
would silently admit any relation.

```
RESEARCH_ONTOLOGY = Ontology(
  version        = "2026-09-18.1",
  entity_types   = {Paper, Author, Topic, ResearchConcept, Model, Method, Problem},
  relation_types = {AUTHORED_BY, CITES, RESEARCHES, BELONGS_TO, SUBTOPIC_OF,
                    DISCUSSES, INVOLVES_CONCEPT, USES_MODEL, APPLIES_METHOD,
                    EXTRACTED_FROM, EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES},
  attributes     = {title, abstract, year, venue, pdf_url, arxiv_id,
                    source_citation_count, level, description, architecture,
                    model_type, year_introduced, introducing_paper_doi,
                    method_type, statement, quoted_text, section,
                    assumption, constraint, dataset, metric, baseline},
)
```

`SOLVED_BY` and `HAS_TOPIC` are **deliberately absent** (§5.5). `INSTANCE_OF` is absent
because it ceases to be a relation and becomes merge lineage (§3.6).

**The extraction path is ontology-unchecked by default and must be told otherwise.**
`ExtractionPipeline` takes no `ontology` argument and defaults to
`OntologyCandidateValidator(None)`, which admits every term, while `IngestPipeline`
defaults to `ontology_strict=True`. agentic-kg MUST pass
`candidate_validator=OntologyCandidateValidator(RESEARCH_ONTOLOGY, strict=True)` to the
extraction pipeline explicitly. This asymmetry is the easiest thing in the adoption to get
wrong.

### 3.2 Are the four implemented candidate kinds sufficient?

`IMPLEMENTED_KINDS = {entity, relation, attribute_assertion, artifact}`
(`kg_contracts/candidates.py:384`, unchanged at the pin). The other five are well-formed
contracts that admission rejects as "defined but not implemented in v1".

**Verdict: sufficient, with two named losses and one that is not a loss.**

| Kind | Needed? | Verdict |
|---|---|---|
| `EntityCandidate` | Paper, Author, Topic, ResearchConcept, Model, Method, Problem | fits |
| `RelationCandidate` | all 14 declared relation types | fits |
| `AttributeAssertionCandidate` | every scalar property | fits |
| `ArtifactCandidate` | the PDF itself (`emit_document_artifacts=True`) | fits — and per KGCS ADR candidate 0001 an artifact **contributes no graph operation**, so the PDF stays in ledger + evidence and never reaches the graph. The legacy graph has no PDF node either, so this is not a regression. |

**Loss 1 — `ObservationCandidate` is not implemented.** A measurement — "model M scores
92.1 on dataset D under metric μ" — is a four-part observation. It must be flattened into
an `AttributeAssertionCandidate` whose `value` is a composite object, which KGCS cannot
reason over as structure. **This is not a regression**: the legacy graph already stores
`baselines`/`metrics`/`datasets` as JSON strings inside a node property
(`entities.py:to_neo4j_properties`). It is a ceiling on what the adoption can later
unlock, and it is the reason the migration cannot promise structured benchmark queries.

**Loss 2 — `IdentityLinkCandidate` is not implemented.** An explicit "these two are the
same" assertion cannot be submitted as a candidate. Two sub-cases:

- *Source-asserted equivalence* (an arXiv id and a DOI on the same source record): express
  as **two aliases on one `EntityCandidate`**. Fully covered.
- *Later-discovered equivalence* (an operator merges two concepts; a new paper reveals two
  concepts are one): **must** go through KGCS ER → `MERGE_IDENTITIES`. This is arguably the
  correct design — a discovered identity link is a curation decision, not an ingestion
  fact — but it means there is **no ingestion-time path for a human-asserted merge**. The
  human path is the KGCS `ReviewQueue` → `ReviewRouter` → `ConceptEvolutionPlanner`, which
  is the same planner the automatic path uses (KGCS §9 law 14).

### 3.3 Per-type mapping

Common to every candidate: `graph_id="research"`, `ontology_version` as above,
`producer_run_id` = the ingestion run id, `trace_id` carried from the legacy `trace_id`
(the only cross-run correlation id that exists today, so it is the natural join key
between a KGIS ledger entry and a legacy edge).

---

#### Paper

| Slot | Value |
|---|---|
| Kind | `EntityCandidate`, `entity_type="Paper"` |
| Aliases (**non-empty is mandatory**) | `Paper:doi:<lowercased DOI>` primary; plus `Paper:arxiv:<id>`, `Paper:openalex:<id>`, `Paper:semantic_scholar:<id>` when the source supplies them |
| `semantic_key` | `paper/doi/<doi>`; when no DOI exists, `paper/arxiv/<id>` — **never a UUID** |
| `display_name` | title |
| `source_coordinates` | `source_type` ∈ {`arxiv`, `openalex`, `semantic_scholar`}; `locator` = the record URI; `fragment` = `None` |
| Evidence | `present_evidence(...)` with `payload_hash` over the normalized source record; `evidence_id` **always explicit**, derived via `kgis.ids.stable_suffix` (the default is a random ULID, which destroys re-collection idempotency) |
| Scores | `extraction_confidence=1.0` (a deterministic field read, not a judgment), `source_reliability` per source from `SourceScoring` |
| Attributes | `title`, `abstract`, `year`, `venue`, `pdf_url`, `arxiv_id` as `AttributeAssertionCandidate`s |
| Validity | attributes are untimed except `source_citation_count`, which carries a `ValidPeriod` because it is time-varying |
| Versions | `producer = "kgis.structured"`; extractor/prompt versions do not apply |

Two legacy properties are deliberately re-modelled:

- **`is_stub` disappears as a flag.** A stub is an `EntityCandidate` carrying only its
  alias and no attribute assertions. "Stub" becomes derivable ("has no `title`
  assertion"), which removes the legacy tri-state (`true` / `false` / absent).
- **Issue #58 poisons CITES parity.** The current smoke ingestion writes **zero** `CITES`
  edges while the other five graph-shape assertions pass. A "does the projection preserve
  CITES?" comparison against today's output is therefore vacuous in exactly the way §5.2
  describes for topics. CITES parity must be asserted against the 10 verified citation
  edges tabulated in the ground-truth chain's README (§5.2.1), never against legacy output.
- **`citation_count` / `reference_count` stop sharing one name with two meanings.** Today
  `repository.py:3092` writes the in-graph inbound degree and `importer.py:224` overwrites
  it with the source API's global count. Under the mapping: the source API's value is an
  `AttributeAssertionCandidate` named `source_citation_count` with a `ValidPeriod`; the
  in-graph degree is computed by the projector. §4.4 states how both reach the API.

---

#### Author

| Slot | Value |
|---|---|
| Kind | `EntityCandidate`, `entity_type="Author"` |
| Aliases | `Author:orcid:<orcid>` and/or `Author:semantic_scholar:<id>` when present |
| Aliases when neither exists | `Author:paper_local:<doi>#<position>` |
| `semantic_key` | `author/orcid/<id>` ▸ `author/semantic_scholar/<id>` ▸ `author/paper_local/<doi>#<pos>` |
| Scores | as Paper |

**Decision: a name is not an identity.** `importer.py:342` falls back to name matching
when ORCID and S2 id are both null — which is the common case — so two researchers with
the same name silently collapse today. The `paper_local` alias replaces that with an
honest per-paper identity, and leaves the *global* person identity to KGCS ER under a
HIGH false-merge cost class.

**This is a deliberate behaviour change.** Author nodes will multiply at first. The old
behaviour was a silent false merge; the new behaviour is an honest under-merge plus a
review queue. Because `DEFAULT_STRONG_NAMESPACES` no longer contains `orcid`
(`39203ad`), Author ER MUST be configured with
`SharedStrongIdentifierRule(strong_namespaces=frozenset({"doi", "orcid"}))`, threaded into
**both** the identity rule and `default_cluster_validator(identity_rules=...)`.

---

#### Topic

Topics have two completely different origins and must not share a mapping.

**(a) The taxonomy — reference data, deterministic.**

| Slot | Value |
|---|---|
| Kind | `EntityCandidate`, `entity_type="Topic"` |
| Aliases | `Topic:taxonomy:<materialized path>`, e.g. `Topic:taxonomy:cs/nlp/question-answering` |
| `semantic_key` | `topic/taxonomy/<path>` |
| Producer | `kgis.structured` over the taxonomy YAML, at a pinned file revision |
| Attributes | `level`, `description` |
| Relations | `RelationCandidate(SUBTOPIC_OF)`, subject = child `EntityRef`, object = parent `EntityRef` |
| Scores | `extraction_confidence=1.0`, `source_reliability=1.0` (curated reference data) |

**Decision: identity is the materialized path, not `(name, level, parent_id)`.** The
legacy MERGE (`repository.py:972`, `:993`) uses a *different pattern* for roots
(`parent_id IS NULL`) than for non-roots, so a topic that gains a parent becomes a
different node, and `parent_id` is simultaneously a denormalized mirror of `SUBTOPIC_OF`
and part of the identity. A path key is stable, reproducible from the YAML alone, and
removes the dual-truth problem. `parent_id` survives only as a projected property (§4.4).

**(b) The extractor's topic assignment — a claim, not reference data.**

| Slot | Value |
|---|---|
| Kind | `RelationCandidate`, `relation_type="RESEARCHES"` |
| Subject / object | Paper `EntityRef` → Topic `EntityRef` (`taxonomy` namespace) |
| `source_coordinates` | `source_type="pdf"`, `locator=<pdf uri>`, `fragment=<span grammar, §3.4>` |
| Scores | `extraction_confidence` = the LLM's per-assignment confidence; `source_reliability` per the extraction arm |
| Versions | `producer = "kgis.extraction:topic@<extractor_version>"` |

`MIN_TOPIC_CONFIDENCE` — today a module constant that silently drops assignments — becomes
a `ConfidencePolicy` field. Thresholds are config, not code (project principle 3; KGCS
governance principle 6). A dropped assignment becomes a *routed* assignment with a
recorded reason instead of a `continue`.

---

#### ResearchConcept / Model / Method

The single most consequential mapping in the spec.

| Slot | Value |
|---|---|
| Kind | `EntityCandidate`, `entity_type` ∈ {`ResearchConcept`, `Model`, `Method`} |
| Aliases | `<Type>:surface:NORM(<name>)` for the extracted name **and every alias the extractor emitted**. `NORM` is defined once in §3.1 and is deliberately not restated here. |
| `semantic_key` | `researchconcept/surface/<normalized form>` (KGIS lowercases the type segment; see the note below) |
| `display_name` | the surface form as written |
| Attributes | `description`; Model additionally `architecture`, `model_type`, `year_introduced`, `introducing_paper_doi`; Method additionally `method_type` |
| Representations | `Representation(kind="vector", key="text-embedding-3-small@1536", model=<model id>)` — **never** in `properties` |
| Scores | `extraction_confidence` from the extractor; `source_reliability` per arm; `identity_confidence` **absent** |

**Decision: no similarity threshold appears anywhere in a candidate.** Legacy identity for
these three labels is "cosine ≥ 0.90 against whichever node happened to arrive first"
(`repository.py:1522`, `:2434`, `:2534`) — dependent on ingestion order, embedding model
version and the threshold of the day, and therefore not reproducible. Under the mapping a
candidate's identity is its **normalized surface form**, which *is* reproducible from the
source text. Deciding that "attention mechanism" and "self-attention" are the same entity
becomes a KGCS ER decision producing a `MERGE_IDENTITIES` operation that is audited,
replayable and compensable. **That substitution is the point of the whole adoption**; if
it is compromised for convenience, nothing else in this spec is worth doing.

*KGIS note (upstream M6, still present at `baf0b67e`):* `entity_semantic_key()`
(`builders.py:483`) lowercases the entity type, so `ResearchConcept` and `researchconcept`
collapse to one semantic key while remaining distinct ontology terms. Harmless here — no
two agentic-kg entity types differ only in case — but the ontology must never gain a pair
that does.

**Seed models and `is_canonical`.** `seed_models.py` loads authoritative models with
`is_canonical=True`, which today drives merge direction. Under the mapping `is_canonical`
is **not** a data property: seed models are ingested under a distinct `producer` and
matched by a `CurationProfile` whose `scope` pins `entity_type="Model"` and whose
`identity_authority_mode=CLIENT_AUTHORITATIVE`. **See §6 D-KGCS-1: a KGCS defect currently
defeats this**, and §6 names the adopter-side gate that must compensate until it is fixed
upstream.

---

#### Problem, ProblemMention, ProblemConcept

**Decision: the mention is the candidate; the concept is the canonical identity;
`INSTANCE_OF` is merge lineage.**

| Legacy | Maps to |
|---|---|
| `ProblemMention` node | one `EntityCandidate(entity_type="Problem")` in the ledger — a *proposed* identity |
| `ProblemMention -[:EXTRACTED_FROM]-> Paper` | the candidate's `evidence_refs` + `source_coordinates`; additionally a `RelationCandidate(EXTRACTED_FROM)` so the edge survives projection with its `section` property |
| `ProblemConcept` node | a **canonical identity** in the KGCS graph — the resolved result, not an input |
| `ProblemMention -[:INSTANCE_OF]-> ProblemConcept` | the `MERGE_IDENTITIES` lineage in `reversal_data`, joined to the ledger row by `candidate_id` |
| `ProblemConcept.canonical_statement` | an `AttributeAssertionCandidate`; re-canonicalization is an `ATTACH_ASSERTION` + `RETRACT_ASSERTION(→SUPERSEDED)` pair, never an in-place overwrite |
| `Problem` (V1 label) | **nothing.** Dead path (C-1/C-2); no data is migrated from it |

| Slot | Value |
|---|---|
| Aliases | `Problem:paper_span:<doi>#<surface>#<span>` — the canonical join key K of §6.4.1, rendered |
| `semantic_key` | `problem/paper_span/<doi>#<surface>#<span>` |
| `source_coordinates` | `source_type="pdf"`, `locator=<pdf uri>`, `fragment` per §3.4 |
| Evidence | `present_evidence(content=quoted_text, ...)` — `quoted_text` is already mandatory (`schemas.py:150`, `min_length=10`), so evidence citation is achievable without new extraction work |
| Note | `section` is **not** part of the key. It is a segmenter output, and the segmenter is under active change (SEG-1/3/4/6), so keying identity on it would churn every problem's identity each time segmentation improves. It survives as an attribute. |
| Attributes | `statement`, `quoted_text`, `section`, and one assertion **per item** for `assumption`, `constraint`, `dataset`, `metric`, `baseline` — each carrying its own `extraction_confidence` from the per-item legacy `confidence` field (`schemas.py:41,54,...`) |

**`EXTENDS` / `CONTRADICTS` / `DEPENDS_ON` / `REFRAMES` — corrected disposition.** An
earlier draft said "no data is migrated" on the grounds that their only writers were the
dead V1 path and the broken `SynthesisAgent`. That was wrong: `scripts/load_sample_problems.py`
writes `create_relation(EXTENDS)` alongside `create_problem` and `link_problem_to_paper`,
and it is an import-valid script an operator can run. A legacy graph therefore **may** hold
these edges, together with the `:Problem` nodes they connect.

The disposition is decided by *what that data is*, not by whether it exists:

| Origin of a `:Problem` / Problem→Problem edge | Disposition |
|---|---|
| `scripts/load_sample_problems.py` — hand-written demonstration statements with fabricated `EXTENDS` links | **Not migrated.** It is sample data, not research evidence; promoting it into the canonical graph would mint curated-looking facts with no source. Counted, reported, and recorded as `unmapped` with reason `demonstration_data`. |
| `v3_topic_migration` era, or any `:Problem` whose `evidence.source_doi` is a paper in the corpus | **Migrated** by the evidence join (§6.4), like any other legacy node. |
| Any `:Problem` with `status='deprecated'` | **Migrated, and the soft delete preserved** — see §6.3. |

The four relation types stay declared in `RESEARCH_ONTOLOGY` so the vocabulary is reserved
and so a future synthesis producer has a legal term to emit. **U-3's node count decides how
much of this matters**: if the sample loader was never run against the target database, the
population is empty and the table above costs nothing.

### 3.4 Evidence coordinates and the fragment grammar

`kg_contracts.Evidence` has **no span, offset or page field** — coordinates are two
free-form strings (`source_type`, `source_locator`) and spans travel by string convention,
flattened into the locator. `SourceCoordinates.fragment` is the parallel slot on the
candidate; its docstring mentions "page/span" but page is a comment, not a field.

**agentic-kg owns the fragment grammar and its parser.** Declared here so every producer
and the projector agree:

```
fragment := "sec:" <section-slug> "#chars:" <start> "-" <end> [ "#page:" <n> ]
example  := sec:introduction#chars:1024-1180#page:2
```

Rules: `start`/`end` are character offsets into the **extracted text** of the PDF, not the
PDF byte stream; `section-slug` is the `SectionType` value from the segmenter; `page` is
optional and present only when PyMuPDF supplied it. Round-tripping is agentic-kg's job —
nothing in KGIS will parse this back.

Evidence ids are **always explicit**, derived with `kgis.ids.stable_suffix` or the KGIS
builders (`chunk_evidence_id`, `document_evidence_id`). The `Evidence` default is a random
ULID; relying on it breaks re-collection idempotency. The docstring's promised
`source:key@window` scheme is not implemented anywhere (upstream M2) — do not follow it.

### 3.5 Confidence mapping — explicit, because a single float is banned

`CandidateScores` is `extra="forbid"`, so a stray `confidence=` kwarg is a
`ValidationError`, not a silent pass-through.

| Legacy value | Where it lives today | Maps to |
|---|---|---|
| `ExtractedProblem.confidence`, `ExtractedTopicAssignment.confidence`, and the concept / model / method `.confidence` fields (all `ge=0, le=1, default=0.8`, LLM self-report) | `extraction/schemas.py:41,54,155,313,330,349,366` | `CandidateScores.extraction_confidence` — 1:1, same semantics, no re-scaling |
| per-item `ExtractedAssumption/Constraint/Dataset/Metric.confidence` | same file | `extraction_confidence` on the corresponding `AttributeAssertionCandidate` |
| *(nothing)* | — | `CandidateScores.source_reliability` — **required and new**. Declared per source in `SourceScoring`; **not derivable from legacy data.** Initial values are a policy choice (§8 U-2). |
| `INSTANCE_OF.confidence` = `candidate.final_score` (cosine + citation boost) | `auto_linker.py:290` | **not a candidate score.** It is an ER output → KGCS `MatchResult.probability`. `identity_confidence` is filled by KGCS resolution, never by a producer. |
| `INSTANCE_OF.confidence = 1.0` hardcoded on the create-new-concept path | `auto_linker.py:344` | **discarded.** It is fabricated certainty about an identity decision that was never made. A fresh KGIS identity has `identity_confidence=None`, and `require_identity_confidence_for_auto=True` means a missing value **blocks** AUTO rather than defaulting to pass. That is the correct behaviour and it is a change. |
| `ProblemMention.match_confidence` (`high`/`medium`/`low` enum) | `auto_linker.py:274` | a derived view of the risk route; not carried as data |
| `MIN_TOPIC_CONFIDENCE` / `MIN_CONCEPT_CONFIDENCE` / `MIN_MODEL_*` / `MIN_METHOD_*` code constants | `kg_integration_v2.py` | `ConfidencePolicy` fields — config, reviewable, per-scope |
| *(nothing)* | — | `policy_risk` = 0.0; `assertion_confidence` / `corroboration_score` = `None` at ingestion, filled by KGCS |

**The existing prompts survive unchanged.** KGIS's `JsonItemsParser`
(`kgis/extraction/parse.py:65-118`) accepts `[{...}]` or `{"items": [...]}` and lifts a
`confidence` (or `extraction_confidence`) key from the LLM's JSON onto the score axis. The
ban is on the *contract field*, not on the prompt's output shape.

### 3.6 Where the four agents land

`SynthesisAgent` is the only agent that attempts a graph write, and it has never
succeeded: all four calls (`synthesis.py:153,168,187,205`) use wrong kwarg names against
the real signatures and raise `TypeError`, downgraded to `logger.warning`. **This is good
news** — there is nothing to unwind for the "no application-facing canonical write" goal.

**Decision:** a synthesis-proposed problem becomes an `EntityCandidate` with
`producer="agentic-kg.synthesis@<version>"` and a distinctly lower `source_reliability`
than an extracted one, submitted through `CandidateSink` like any other candidate. It is
never auto-accepted: its curation profile allowlists no auto action. The Ranking,
Continuation and Evaluation agents are read-only and are unaffected except that they read
the projection graph.

---

## 4. Analysis C — KGCS canonical → Neo4j projection

### 4.1 The decision

**A projection/compatibility adapter in the adopter. No domain concept enters KGCS.**

KGCS hardcodes no entity type, predicate or vocabulary — its own flagship E2E test is
parametrized over two unrelated domain shapes specifically to prove domain-neutrality.
Adding `Paper`, `Topic` or `RESEARCHES` to KGCS would destroy that property for every
other adopter, and KGCS's release notes already name "all domain reasoning" as
deliberately out of scope. The projection is agentic-kg's own module,
`agentic_kg.projection`, and it is the **only** place that knows both the canonical shape
and the legacy shape.

### 4.2 Two Neo4j surfaces, never one access path

| Surface | Contents | Written by | Read by |
|---|---|---|---|
| **Canonical** | `CanonicalEntity` + `Assertion`, epoch-stamped | KGCS `PlanExecutor` **only**, via `Neo4jGraphStore.apply()` | the projector; KGCS's own `graph_reader` |
| **Projection** | legacy labels, edges, counters, vector indexes | the projector | API routers, agents, UI — all read-only |

KGIS ADR-0006 permits one physical database but **never one access path**. The cheapest
way to make that structural rather than conventional is **two Neo4j databases in one
instance** (`canon` and `projection`), each with its own driver/session factory, so a
`DETACH DELETE` rebuild of the projection cannot reach canonical data.

> **UNDETERMINED (U-1).** Neo4j Community Edition supports only one user database. Which
> edition/tier this project deploys is not established from the repo. *Resolved by:*
> reading the Terraform/AuraDB tier in the deployment config, or a `SHOW DATABASES`
> against the deployed instance. If Community, the fallback is one database with a
> canonical label prefix (`Canon__Identity`, `Canon__Assertion`) plus a distinct session
> factory — separation by convention, not by enforcement, and that weakening must be
> recorded.

`Neo4jGraphStore` implements `GraphMutationStore` + `TemporalGraphReader` +
`CapabilityDeclaring` on one class, with `GraphWriter`/`TransactionalGraphWriter`
primitives internal. Non-negotiables the shared conformance suite enforces: epoch is
`previous + 1` on commit; a failed precondition is an atomic no-op; `committed=False`
**must** name a reason (ADR-0021 — an idempotent re-apply must return `error="no-op"`, not
a bare `False`); `SUPERSEDED` hidden by default while **`REVOKED` is not**; half-open
bitemporal windows; `UnsupportedCapabilityError` for any undeclared capability.

The executor's `DEFAULT_SUPPORTED_OPERATIONS` is only
`{CREATE_IDENTITY, ATTACH_ASSERTION}`. **`Neo4jGraphStore` must additionally implement
`RETRACT_ASSERTION`** or supersession — and therefore concept re-canonicalization and the
"old interpretation stays historically queryable" property — cannot execute at all.
`MERGE_IDENTITIES` / `SPLIT_IDENTITY` / `REASSIGN_ASSERTION` are required the moment ER
merges execute, which for this ontology is immediately.

### 4.3 Projection semantics

- **Epoch-pinned.** The projector reads at the **published** epoch only
  (`GraphReadOptions(curation_epoch=None)` means "latest published", not "whatever is
  present"), so no reader ever observes a partially promoted batch. KGCS governance
  principle 8: derived projections are built only from canonical data at a published epoch.
- **`REVOKED` must be filtered by the projector.** There is no `include_revoked` option
  and the `SUPERSEDED`-hidden/`REVOKED`-visible asymmetry is deliberately pinned upstream.
  A projector that does not filter will serve revoked facts. This is a required assertion
  in the projector's test suite.
- **Idempotent upsert keyed on `identity_id`**, plus a sweep that removes projected nodes
  whose identity is absent or revoked at the published epoch. Neo4j has no atomic database
  swap, so a full-rebuild-then-switch is not available; the upsert+sweep is the honest
  alternative.
- **Watermark.** One additive node `(:ProjectionWatermark {curation_epoch, run_id,
  built_at})`. Additive, so no existing reader breaks.
- **Reproducible.** Projecting the same published epoch into two **independently empty**
  graphs produces equal content. Note the shape: re-running the projector against a graph it
  already built proves nothing, because the upsert above makes the second run a no-op — it is
  identical because nothing happened. Equality is computed over projected labels, properties
  and edges, ignoring Neo4j internal ids and `ProjectionWatermark.built_at`. AC-9.

### 4.4 The compatibility contract

**Node labels (10 live).** All ten are projected in the first cut; nothing is dropped
before its consumers are gone.

`Paper`, `Author`, `Problem`, `ProblemMention`, `ProblemConcept`, `Topic`,
`ResearchConcept`, `Model`, `Method`, `IngestionRun`. (`SchemaVersion` is DDL state owned
by the projector; `PendingReview` is dead — §5.3.)

Notes on three of them:

- **`Problem`** is projected from the canonical `Problem` identity (which the legacy graph
  called `ProblemConcept`). `ProblemConcept` is projected as a **second view of the same
  identity** so both legacy read paths keep resolving. This is the one place the projection
  deliberately duplicates: the legacy model has two labels for one thing, and unifying them
  is an API change, not a projection change.
- **`ProblemMention`** is projected from ledger rows for the pipeline-internal readers
  (`concept_matcher.py`, `concept_refinement.py`, `ingestion.py`). **No API router reads
  it** — once those readers are replaced, it can be dropped from the projection. That is a
  later PR's decision, recorded here so it is not forgotten.
- **`IngestionRun`** gains the uniqueness it never had: today `job_runner.py:91` uses a
  bare `CREATE` with no constraint, so re-running a trace duplicates provenance and
  `routers/ingest.py:152` takes whichever row comes back. The projection keys on `trace_id`.

**Relationship types.** Fifteen distinct type names must be projected — the discovery
brief's "14" is one short:

`AUTHORED_BY`, `CITES`, `EXTRACTED_FROM`, `INSTANCE_OF`, `BELONGS_TO`, `RESEARCHES`,
`SUBTOPIC_OF`, `DISCUSSES`, `INVOLVES_CONCEPT`, `USES_MODEL`, `APPLIES_METHOD`, `EXTENDS`,
`CONTRADICTS`, `DEPENDS_ON`, `REFRAMES`. (`REVIEWS` is dead; `SOLVED_BY` and `HAS_TOPIC`
were never written.)

Three are not straight projections of a canonical relation:

| Edge | Derivation |
|---|---|
| `INSTANCE_OF` (`ProblemMention → ProblemConcept`) | from `MERGE_IDENTITIES` lineage joined to the ledger, **not** from a canonical relation. Edge props: `confidence` ← the recorded `MatchResult.probability`; `match_method` ← the `ErAction`; `matched_at`/`matched_by`/`trace_id` ← the audit record. |
| `EXTRACTED_FROM` | projected in **two shapes**, exactly as today: `Problem → Paper` with `section` + `extraction_date` (read by `graph.py:127`, `search.py:184`) and `ProblemMention → Paper` with no props. A single canonical relation cannot be projected as one edge type without breaking one of the two readers. |
| `BELONGS_TO` (`Problem → Topic`) | **derived** — see §5.2. Nothing writes it today. |

**`AUTHORED_BY` property collision.** `repository.py:735` writes `r.position`;
`relations.py:451` writes `r.author_position`; both `MERGE` the same edge, so whichever ran
last wins, while the reader (`relations.py:483-486`) sorts on `author_position`. The
projection writes **`author_position` only**, and the duplicate writer (row 43) is deleted.

**Counters — recomputed, never mutated.** Every denormalized counter is computed from the
projected edges at projection time:

| Property | Computed as |
|---|---|
| `Topic.problem_count` | `count((:Problem\|:ProblemMention\|:ProblemConcept)-[:BELONGS_TO]->(t))` |
| `Topic.paper_count` | `count((:Paper)-[:RESEARCHES]->(t))` |
| `ResearchConcept.mention_count` | `count(()-[:INVOLVES_CONCEPT]->(rc))` |
| `ResearchConcept.paper_count` | `count((:Paper)-[:DISCUSSES]->(rc))` |
| `Model.usage_count` / `Method.usage_count` | in-degree of `USES_MODEL` / `APPLIES_METHOD` |
| `ProblemConcept.mention_count` | `count((:ProblemMention)-[:INSTANCE_OF]->(pc))` |
| `ProblemConcept.paper_count` | distinct papers behind those mentions — **legacy always wrote `1`** (`auto_linker.py:199`, never updated). The projection computes the true value: a silent behaviour change that fixes a bug, and it is declared here rather than discovered later. |
| `Paper.citation_count` | the source-asserted `source_citation_count` at the published epoch — i.e. the global count the API's consumers actually want |
| `Paper.in_graph_citation_count` / `in_graph_reference_count` | **new, additive**: `CITES` degree. Resolves the two-meanings-one-name collision without breaking a reader. |

**Ordering keys — all ten stay sortable**, because every counter still exists as a
property; it is merely derived. `rc.mention_count DESC, rc.name` · `pc.mention_count DESC`
· `m.is_canonical DESC, m.usage_count DESC, m.name` · `m.usage_count DESC, m.name` ·
`p.year DESC` · `p.created_at DESC` · `t.name` · `CASE t.level` then `t.id` ·
`r.author_position` · (`r.priority ASC, r.sla_deadline ASC` — dead, §5.3).

`m.is_canonical` is projected from the Model's curation profile scope, not from a data
property (§3.3).

**Vector indexes — six, by name.** `problem_embedding_idx`, `topic_embedding_idx`,
`research_concept_embedding_idx`, `model_embedding_idx`, `method_embedding_idx`,
`concept_embedding_idx`. The code names these directly, so the projector's DDL must
recreate them under exactly these names. `mention_embedding_idx` is declared and never
queried — **dropped** (7 declared → 6 live).

**Embeddings are recomputed by the projector, never carried from the legacy graph** — see
§5.4. Where the canonical entity carries a `Representation(kind="vector")` whose key
matches the projector's configured model, it is reused; otherwise the projector re-embeds
from canonical text. A stale vector is silently wrong, which is exactly the failure mode
hazard 4 describes.

### 4.5 What happens to the 15 mutation endpoints

Each becomes a **curation request**: the router builds a `Candidate` and calls
`CandidateSink.submit()`. The HTTP contract changes from *200 with the mutated object* to
*202 with a candidate id*, because the change is not visible until a curation epoch is
published. That is a real, user-visible API change and it belongs to a later PR; it is
recorded here so the projection is not asked to fake synchronous writes.

### 4.6 What disappears

`purge_paper_extraction` and both `reconcile_*_counts` methods have no post-migration
equivalent. Re-ingestion becomes: submit new candidates → curate → publish a new epoch →
re-project. Nothing is destructively deleted from canonical state, so there is nothing to
purge and no counter to reconcile. Deleting `re_ingestion.py` also retires the `SOLVED_BY`
guardrail (§5.5).

---

## 5. Hazard dispositions

### 5.1 Hazard 1 — denormalized counters have no reconciler, and they are `ORDER BY` keys

**Verified.** Only two reconcilers exist (`repository.py:1454`, `:2008`).
`Model.usage_count`, `Method.usage_count`, `Paper.citation_count`, `Paper.reference_count`
and every `ProblemConcept` counter have none. `re_ingestion.py:140-225` deletes
`RESEARCHES`, `BELONGS_TO`, `DISCUSSES`, `INVOLVES_CONCEPT` and `EXTRACTED_FROM` with raw
Cypher and never decrements — verified: the only `count` tokens in that file are result
aliases. PR #66 made the deletes *effective*, so drift at `7108c7d` is strictly worse than
before: the edges are now gone and the counts are not.

**Disposition — retired by construction.** §4.4 recomputes every counter from projected
edges each run, so drift is not merely reconciled, it is unrepresentable. Consequences:

1. **Legacy counter values are not migratable data.** They are not read during migration,
   not compared against, and not used to validate anything.
2. Pagination is preserved because the properties still exist and are still sortable.
3. **Acceptance criterion:** for every projected counter, `property == degree(edge)` at the
   published epoch. This one is a **structural invariant, not a fixture claim** — it holds
   over whatever the projection contains, so it is asserted over the whole projected graph
   and does not depend on §5.2.1's two reconciled papers.
4. `ProblemConcept.paper_count` changes from a constant `1` to the true value. Declared as
   a deliberate fix, not a regression.

### 5.2 Hazard 2 — read/write disagreement on topics

**Verified, and worse than reported.** Eight read paths traverse
`(:Problem)-[:BELONGS_TO]->(:Topic)` — `/api/stats` (`main.py:190`), `/api/graph` ×3
(`graph.py:39,77,173`), `/api/topics/{id}/problems` (`topics.py:213`), hybrid search
(`search.py:277`), `structured_search` (`search.py:169`, also the Ranking agent's path),
and `ContinuationAgent._lookup_topic_name` (`continuation.py:76`). The pipeline writes
`(:Paper)-[:RESEARCHES]->(:Topic)` (`kg_integration_v2.py:913` passes
`entity_label="Paper"`, which `_ASSIGN_RELATIONSHIPS` maps to `RESEARCHES`).

**The verified correction makes it sharper: no pipeline path writes `:Problem` either**
(C-2). So those eight endpoints are not merely missing an edge — they match a node label
that ingestion never produces. Against a graph built only by the pipeline they return
empty, every time. And the one writer that does produce `:Problem` nodes —
`scripts/load_sample_problems.py` — writes **no topic edge at all** (grepped: the script
contains no `BELONGS_TO`, no `assign_entity_to_topic`, no topic handling), so it does not
rescue these endpoints either. The only producers of `Problem-[:BELONGS_TO]->Topic` remain
the manual CLI command (`cli.py:963`), the manual API endpoint (`routers/topics.py:274`),
and the one-shot v3 migration.

**Disposition — three parts, no manufactured parity.**

1. **State the truth in the spec and in the test suite.** A behaviour-preservation test
   over these eight paths is **vacuous**: it compares two empty results and proves
   nothing. Any test claiming to preserve topic behaviour must be treated as a defect
   until it is rewritten.
2. **The projection derives the edge and says so.** `BELONGS_TO` is projected as
   `Problem —(evidence)→ Paper —(RESEARCHES)→ Topic`. Every reader is satisfied, and the
   eight endpoints return non-empty results **for the first time**. That is a deliberate,
   declared behaviour *change* — not a preservation — and it belongs in the release notes.
3. **Parity tests get an anti-vacuity guard, pointed at a fixture that actually exists.**
   Every projection parity test asserts `len(result) > 0` **before** comparing, and the
   expected rows come from a hand-checked fixture — never from "run the old code, run the
   new code, diff". See §5.2.1 for what that fixture is; an earlier draft cited an
   "8-paper hand-checked fixture" that does not exist.

#### 5.2.1 What the parity fixture actually is

`packages/core/tests/extraction/fixtures/ground_truth_chain/` covers **8 papers**, but its
README is explicit: *"Only `reconciled/` is the answer key. `human/` and `claude/` are
retained as evidence."* The directory holds:

| Directory | Files | Usable as an answer key? |
|---|---|---|
| `reconciled/` | **2** — `paper_cskg.gold.yml`, `paper_cskg2.gold.yml` | **Yes** — the only authoritative entity gold |
| `claude/` | 3 | No — one reviewer's independent pass |
| `human/` | 4 | No — the other reviewer's independent pass |

So the entity-level parity fixture is **2 reconciled papers**, not 8 and not 3. That is
small, and saying so is the point: an AC that claims more coverage than exists is exactly
the vacuous-test failure §5.2 is about.

Citation parity is a separate and better-supplied case. The **10 verified `CITES` edges
across all 8 papers** are recorded as a table in the fixture README (`:79-90`), curated
from OpenAlex `referenced_works` and confirmed against first pages on 2026-07-27 — they do
not depend on `reconciled/` at all. The README's own caveat carries over: curation used
OpenAlex while the importer uses Semantic Scholar, so an edge diff must be re-checked
against S2 before being called a bug.

Two consequences for the ACs:

- **AC-6 / AC-7 bind to `reconciled/` (2 papers) for entity and counter parity**, and to the
  README's 10-edge table for `CITES` parity. Nothing binds to `human/` or `claude/`.
- **Widening the reconciled set is a prerequisite, not a nice-to-have**, for any claim about
  Topic, ResearchConcept, Model or Method parity at corpus scale. It is recorded as U-9.

### 5.3 Hazard 3 — `/api/reviews/*` is entirely non-functional

**Verified.** `review_queue.py` calls `self._repo.write_transaction` / `read_transaction`
at eight sites (`:184,223,250,274,296,335,366,425`). `Neo4jRepository` defines neither —
its only session accessor is the sync `session()` contextmanager at `repository.py:143`.
The names exist solely as `AsyncMock`s in the test file.

**It is not alone.** Verification for C-7 found `PUT /api/problems/{id}` is broken the same
way — wrong call shape, uncaught exception, UI-reachable — and `SynthesisAgent` makes three.
**Three application write surfaces are non-functional**, which is the general form of this
hazard: for a meaningful part of the write surface, "current behaviour" is an exception, so
*preserve current behaviour* is not a coherent migration goal and a test asserting it would
be asserting the exception.

**Disposition — explicitly scoped out as data; re-built as new work in KGCS.**

- **Not preserved.** There is no working behaviour to preserve. Preserving a `TypeError`
  is not a migration goal.
- **No data migrated.** `PendingReview` and `REVIEWS` are assumed to have zero instances.
  *This assumption must be confirmed by a node count before cutover* (§8 U-3) — it is the
  cheapest check in the whole migration.
- **Re-built on KGCS.** The replacement is KGCS's `ReviewQueue` port with
  `PersistentReviewQueue` + `JsonFileReviewStore` (the only durable KGCS store out of the
  box), reading the **candidate ledger** via `LedgerReader` — never the canonical graph. A
  `GraphReader` and a `LedgerReader` are never the same object (KGIS ADR-0011); the review
  UI reads uncertain records, the API query endpoints read canonical ones.
- **The three latent defects become requirements, not bugs to port:** (a) review payloads
  must not assume Neo4j can store a list-of-maps — KGCS's `ReviewCase` lives inside an
  opaque `payload` dict for exactly this reason; (b) priority must be an ordered value, not
  a string enum compared with `<=` and sorted `ASC` (`review_queue.py:208,210,284`);
  (c) **`resolve()` must reach the graph** — today it writes only to the `PendingReview`
  node and never creates the link or sets the mention's concept, so a human verdict is
  silently discarded. In KGCS, resolution routes through `ReviewRouter` into the *same*
  `ConceptEvolutionPlanner` and `PlanExecutor` as the automatic path (§9 law 14), which
  makes (c) structurally impossible to reintroduce.
- **Interim honesty fix (out of scope for this PR, recorded as a follow-up):** until the
  rebuild lands, the three endpoints should return `501 Not Implemented` rather than a 500
  from an `AttributeError`. That touches `packages/` and is therefore not this PR's work.

### 5.4 Hazard 4 — the ER population is corrupted at source

**Verified, and the discarded work is visible in the code.** `auto_linker.py:176` computes
an embedding for the new concept and assigns it to the `ProblemConcept` object;
`auto_linker.py:352` then stores `concept.to_neo4j_properties()`, which does
`self.model_dump(exclude={"embedding"})` (`knowledge_graph/models/entities.py`) and —
unlike `repository.create_problem:290-292` — never re-adds it. **Every `ProblemConcept`
created by the auto-linker is invisible to `concept_embedding_idx`.**
`ConceptMatcher.find_candidate_concepts` queries that index, so those concepts can never be
matched again: every mention becomes a new concept, forever.
`concept_refinement.py:230-240` compounds it by rewriting `canonical_statement` without
re-embedding, so surviving vectors describe pre-refinement text.

**Disposition.**

1. **Legacy `ProblemConcept` identity is not authoritative** and is not translated. This is
   the primary driver of the re-derive migration strategy (§6).
2. **The projector never carries a legacy vector.** It re-embeds from canonical text, or
   reuses a canonical `Representation(kind="vector")` whose key matches the configured
   model. Never the stored property.
3. **Expect a large duplicate population** in the legacy graph and measure it: the count of
   legacy concept ids collapsing onto one canonical identity is the migration's headline
   metric, not an embarrassment to hide.
4. **Configuration that prevents recurrence**, all adopter-side and all mandatory:
   - `DeterministicRuleMatcher`, not `CalibratedMatcher`, until a labeled golden set exists
     — an uncalibrated `CalibratedMatcher` cannot tell you it is uncalibrated, and
     `calibrate_logistic` on an empty golden set returns a plausible-looking all-zero model
     (KGCS D-8);
   - `strong_namespaces={doi, orcid}` explicitly, in **both** the identity rule and the
     cluster validator — `orcid` is no longer a default at `39203ad`;
   - assert `cluster_validation.pairwise_complete` in the adopter's bridge before calling
     `decide` — a cluster can be `valid=True` on **zero** pairwise evidence and the policy
     gate never reads that flag (KGCS D-7);
   - assert every cluster member is present in `entities` — a member omitted from the map
     is silently unchecked, including by the authority constraint (KGCS D-12).

### 5.5 Hazard 5 — `SOLVED_BY` is dead vocabulary

**Verified.** Repo-wide, `SOLVED_BY` appears exactly twice in `.py` files, both prose:
`re_ingestion.py:6` (a docstring describing the purge guardrail) and `cli.py:421` (help
text). No code writes it. The purge guardrail at `re_ingestion.py:72-93` therefore protects
a relationship type that cannot exist, and any migration step that assumes "manual curation
edges" exist is assuming data that was never written. `HAS_TOPIC` was the same and PR #66
removed it.

**Disposition.** `SOLVED_BY` is **not declared** in `RESEARCH_ONTOLOGY` — and because the
`relation_types` set is non-empty, its absence actively forbids the term rather than
silently permitting it. The guardrail prose disappears with `re_ingestion.py` (§4.6). No
migration step, test, or fixture may assume `SOLVED_BY` data exists. Two related pieces of
phantom vocabulary are retired at the same time: `ProblemMention.workflow_state`
(documented as set to `AUTO_LINKED` at `auto_linker.py:77`, never written by the Cypher at
`:272-278`), and the live `# TODO: Create INSTANCE_OF relationship in next task` at
`concept_matcher.py:289`, which makes
`match_mention_to_concept(auto_link_high_confidence=True)` a silent no-op.

**The regression guards stay.** `packages/core/tests/extraction/test_e8_purge.py:179,191,208`
asserts `HAS_TOPIC` is absent from the write vocabulary — that guard is what PR #66 bought.
"The term appears nowhere" means nowhere it could be *written*, not nowhere it is
*mentioned*: a test asserting absence is the enforcement of this rule, not a violation of
it. AC-15 carries the exemption explicitly so that a later search-and-delete pass cannot
take the guard with it.

---

## 6. Analysis D — identity migration

### 6.1 The invariant

> **Every legacy id resolves to exactly one of: a canonical identity, or an explicit
> `unmapped` record naming the reason. Never two. Never silently a different entity than
> before.**

This is the "no silent identity fork" rule, stated so it can be tested: a uniqueness
constraint on `(legacy_label, legacy_id)` in the map, plus a totality test asserting the
map covers every node in the frozen snapshot.

### 6.2 Where the map lives

An **adopter-owned** table — `identity_map(legacy_label, legacy_id, identity_id,
alias_rendered, method, basis, frozen_at, run_id)` — alongside the candidate ledger. Not in
Neo4j (it is migration provenance, not a canonical fact) and not in KGIS (the registry
describes *graphs*, not legacy row mappings; there is no contract home for this).

It must cover **every UUID4-keyed label**, because those UUIDs are exposed directly as API
path parameters (`/problems/{id}`, `/topics/{id}`, `/concepts/{id}`, `/models/{id}`,
`/methods/{id}`) and are also stored as foreign keys **inside node properties**, not only on
edges: `ProblemMention.concept_id`, `ProblemMention.paper_doi`, `Topic.parent_id`,
`Problem.evidence.source_doi` (buried in a JSON blob), and the `PendingReview` FKs.
`Paper.doi` and `trace_id` are the two anchors that need no mapping.

### 6.3 Per-label derivability — stated honestly

| Legacy label | Legacy key | Canonical derivation | Reproducible? |
|---|---|---|---|
| `Paper` | `doi` | alias `Paper:doi:<doi>` → `find_entities(alias=)` | **Yes** — deterministic |
| `IngestionRun` | `trace_id` | carried unchanged | **Yes** |
| `Topic` | UUID; natural `(name, level, parent_id)` | materialized path from the taxonomy YAML at a pinned revision | **Yes**, given the pinned YAML |
| `ProblemMention` | UUID | `(doi, section, sha(statement))` | **Yes**, if the statement text survives |
| `Author` | UUID | ORCID / S2 alias when present | **Partial** — name-only authors are not derivable, and the legacy node may itself be a false merge of two people |
| `ResearchConcept` | UUID | cosine ≥ 0.90 vs whichever node arrived first | **No** |
| `Model` | UUID | same, plus `is_canonical` protection | **No** |
| `Method` | UUID | same | **No** |
| `ProblemConcept` | UUID | cosine against a vector **that was never stored** (§5.4) | **No** — in practice "one concept per mention" |
| `Problem` | UUID | evidence join on `evidence.source_doi` + statement (§6.4) | **Partial — and it must be mapped, not skipped.** See below. |
| `PendingReview` | UUID | non-functional (§5.3) | n/a — assumed empty |

**Four of twelve labels have no mechanical derivation, and the spec does not pretend
otherwise.** Their identity depended on ingestion order, the embedding model version and
the threshold of the day. No amount of care recovers a decision that was never recorded.

**`Problem` in particular must be in the map, and an earlier draft wrongly excluded it.**
Correcting C-2 changes this row from "nothing to map" to a real obligation, because those
UUIDs are `/api/problems/{id}` path parameters the UI calls `DELETE` on
(`packages/ui/src/lib/api.ts:199`). Leaving them out would make AC-12's totality claim
false. Three sub-cases, each decidable from the frozen snapshot:

| Observable | Origin | Map outcome |
|---|---|---|
| `evidence.source_doi` names a paper in the corpus | extraction-era or v3-migration data | evidence join (§6.4) |
| statement matches `create_sample_problems()` in `scripts/load_sample_problems.py` | demonstration data | `unmapped`, reason `demonstration_data` → `410 Gone` |
| `status == 'deprecated'` | a human soft-deleted it through `DELETE /api/problems/{id}` (`repository.py:408` sets `ProblemStatus.DEPRECATED`) | **join, then carry the soft delete forward** as a `RETRACT_ASSERTION` on the canonical identity. A human decision must not be silently undone by re-derivation. |

`status='deprecated'` is the **only** human mutation a legacy `Problem` can carry, because
`PUT` crashes before touching the graph (C-7). That is a convenient accident: it means the
legacy graph holds no undetectable human edits to `Problem` — had `PUT` worked, edits would
have been indistinguishable from extraction output, since `update_problem` records no actor
and the router never reaches the `version += 1` at `repository.py:375`.

> **`deprecated` is load-bearing and ambiguous, and both facts matter.** An earlier draft of
> this spec wrote `status='archived'`. No such value exists: `ProblemStatus` is
> `{open, in_progress, resolved, deprecated}` (`knowledge_graph/models/enums.py:13-16`) and
> `grep -rn "archived"` over `packages/` and `scripts/` returns nothing but a word inside a
> paper fixture. So the draft's acceptance criterion quantified over the **empty set**: it
> would have gone green while every real soft-deleted `Problem` fell through and migrated as
> **active** — silently undoing the one human decision this section exists to preserve.
>
> Correcting the value exposes a second problem the wrong value was hiding. `deprecated` is
> reachable two ways: a human `DELETE`, and an extractor or operator setting it directly,
> since `ProblemStatus.DEPRECATED` is an ordinary domain value (`entities.py` even validates
> that `RESOLVED`/`DEPRECATED` carry evidence). Nothing in the node records **which**. The
> migration therefore does **not** claim to recover intent: it carries `deprecated` forward as
> a retraction in every case, because retracting a problem that was merely marked deprecated
> is recoverable by a later curation act, while resurrecting one a human deleted is a silent
> loss. Erring toward the recoverable failure is the whole of the reasoning, and it is stated
> rather than implied.

### 6.4.1 The join key — one key, defined once

An earlier draft named three different keys for one join — `sha(statement)` in §3.3,
`(doi, section, sha(statement))` in §6.3 and `(doi, quoted_text)` in §6.4 — and
`statement` ≠ `quoted_text` in the fixtures. There is now exactly one:

```
K = ( entity_type , doi , surface , span )
      surface = NORM( the canonical name, or the statement for a Problem )
      span    = sha256-16 of NORM( quoted_text )
```

**Why each part is load-bearing, with the evidence:**

| Part | Why |
|---|---|
| `entity_type` | In the ground-truth fixtures one span is cited by **both** a Model and a Method. Without the type they collide. |
| `doi` | Scopes the key to a paper; the same concept recurs across the chain by design. |
| `surface` | **The discriminator, and the fix for the 16% collision rate.** `(doi, quoted_text)` alone is unique for `problems` (40 problems over 8 papers, 0 collisions) but collides for exactly the labels §6.3 marks non-derivable: **32 of 194 ResearchConcept/Model/Method entries share a span**, because one enumeration sentence evidences many entities — 4 distinct Models in `reconciled/paper_cskg2.gold.yml`, 5 in `human/paper_fact_completion.gold.yml`. Adding the surface form splits them, and it is the *same* normalized surface form §3.3 already uses as the candidate's semantic key, so the join key and the identity key agree by construction. |
| `span` | Distinguishes two genuinely different mentions of the same surface form in one paper, and is what ties the mapping to evidence rather than to a string. |
| normalization | **`NORM`, defined once in §3.1** — this section does not restate it, because the two-definition draft disagreed with §3.3 on 13% of names. The corruptions it handles are the ones the gold files document: a hyphen dropped at a line break (`paraphrasedistilroberta-base-v2`), and a `fi` ligature (`Classiﬁer`) which NFKC folds. A raw hash over `quoted_text` would miss both. |

`surface` is `NORM(statement)` for a Problem and `NORM(canonical name)` for
ResearchConcept / Model / Method. `K` is the migration join key **and**, rendered as
`<doi>#<surface>#<span>`, the `paper_span` alias in §3.3 — literally the same function on
both sides, which is what makes "agree by construction" a fact rather than an aspiration.

### 6.4.2 The migration procedure

1. **Freeze.** Take a read-only snapshot of the legacy graph at a named timestamp. Record
   `MATCH (n) RETURN labels(n), count(*)` and `MATCH ()-[r]->() RETURN type(r), count(*)`.
   This is also what settles U-3, U-4 and U-5 (§8).
2. **Re-derive, do not translate.** Re-run ingestion from the *source corpus* into KGIS,
   and let KGCS ER decide identity afresh under the conservative profile. The resulting
   identities are new and — unlike the legacy ones — reproducible.
3. **Bind old ids by evidence, not by similarity.** For each legacy node, compute `K`
   (§6.4.1) and find the canonical identity carrying the same `K`; for reference data use
   the natural key instead. This is a **deterministic join**, never a vector comparison.
4. **Record the three outcomes.**
   - *Exactly one match* → write the mapping, `method="evidence_join"`.
   - *Zero matches* → `unmapped`, reason `no_canonical_counterpart` (the legacy node came
     from a paper no longer in the corpus, or from the dead V1 path).
   - *Many matches* → `unmapped`, reason `ambiguous`, routed to human review. **Not
     auto-resolved.**
5. **Collisions are expected and are the point.** Many legacy ids → one canonical identity
   is the corrupted duplicate population (§5.4) being correctly merged. Both legacy ids map
   to the same `identity_id`; the API resolves both. The collision count is a published
   migration metric.
6. **A residual many-match is one of two things, and the report must say which.** Under
   `(doi, quoted_text)` alone, a legacy id matching several canonical identities was the
   *normal* case for enumerated entities — 16% of them — and diagnosing that as "the legacy
   node conflated two things" would have been wrong 32 times out of 194. With `K` the
   enumeration case is split by `surface`, so a remaining many-match means either a genuine
   legacy conflation **or** a key still too coarse for that entity type. Both are marked
   `ambiguous` and routed to a human, and the migration report states the count per entity
   type so a systematic key problem is visible rather than filed as 200 individual
   conflations.
7. **Serve unmapped ids honestly.** A request for an `unmapped` legacy id returns
   **`410 Gone`** with the reason — not a `404` (which implies it never existed) and never a
   redirect to a best guess (which is a silent identity fork by another name).
8. **Freeze the map.** After cutover it is append-only and never recomputed. Recomputing it
   would fork identity by definition.

### 6.5 The cost, stated plainly

Step 2 means re-running LLM extraction over the corpus. That is real money and real
latency, and it is the honest price of hazard 4.

The cheaper alternative — translating legacy nodes directly into candidates — is
**rejected**: it would import the corrupted ER population, the never-stored embeddings and
the fabricated `INSTANCE_OF.confidence = 1.0` into the canonical graph permanently, and the
canonical graph is the thing the whole adoption exists to make trustworthy. A one-time
extraction cost buys a reproducible identity model; the alternative buys a cheaper
migration and a permanently untrustworthy graph.

---

## 7. Upstream defects found

Recorded for upstream fixing. **No adopter workaround is designed for any of these beyond
the minimum configuration that keeps the adopter safe.**

### D-KGIS-0 / D-KGCS-1 (new, P1) — `select_survivor` defeats seed-model authority

`DefaultNormalizer.normalize` (`39203ad:src/kgcs/er/normalize.py:257-278`) populates
`source_reliability`, `embedding`, `affiliations`, `neighbors` and the temporal fields
**only for an `EntityCandidate`**; for a `CanonicalEntity` it sets `source_key` and
`graph_id` and nothing else. `select_survivor` (`er/cluster.py:504-518`) ranks by
`source_reliability` with "an absent reliability sorts lowest". **Therefore an established
canonical entity always loses survivorship to a fresh candidate** — and *unconditionally*,
because `CandidateScores.source_reliability` is a required field, so even the worst
possible candidate (`source_reliability=0.0`) outranks an absent value.

Concrete consequence for agentic-kg: this directly defeats the `Model.is_canonical` seed
protection that `seed_models.py` exists to provide. A seeded canonical model would be
superseded as merge survivor by an LLM-extracted mention of itself.

**The fix is split across two repos, and an earlier draft aimed the whole of it at KGCS.**
`CanonicalEntity` (`kg_contracts/assertions.py:63-89`) has **no `source_reliability` field
at all**, and `kg_contracts` ships from KGIS. So:

- **KGIS ask (contract change):** give `CanonicalEntity` a comparable reliability — or an
  explicit "established identity" marker — so KGCS has something to read. This is a
  `kg_contracts` change and therefore the highest review scrutiny in that repo.
- **KGCS ask (behaviour, actionable independently):** make `select_survivor` prefer an
  established `CanonicalEntity` over a candidate when reliability is not comparable,
  instead of sorting the absent value lowest. This is a strictly safer default and needs no
  contract change.

agentic-kg is the worked example; report 04's D-9 is the general form.
- **Minimum adopter safety until fixed:** the ER→plan bridge must refuse to execute any
  `MERGE_IDENTITIES` whose cluster contains a seed-loaded Model and whose selected survivor
  is not that model. This is a refusal, not a workaround — it blocks the merge and routes to
  review rather than reimplementing survivorship.

### D-KGIS-1 (carried, P1) — revoked keys are not resubmittable under the default id strategy

`candidate_id` is the `ledger_entries` primary key and is checked globally across
tombstones, while `DeterministicIdStrategy` mints
`"cand_" + stable_suffix(graph_id, candidate_kind, semantic_key)`. A re-ingest of the same
fact after revoke/erase mints the **same** id, collides with the tombstone, and is swallowed
as `DUPLICATE` — contradicting ADR-0013's "a tombstone releases its dedup key". The upstream
tests pass only because their factory mints random ids.

**Relevance here:** agentic-kg's re-ingestion flow is precisely "submit the same fact
again". Recorded, not worked around; if it blocks, the resolution is an upstream fix, not a
bespoke `IdStrategy` in agentic-kg.

### D-KGIS-2 (carried, P1) — no `Protocol` for the evidence registry

`ExtractionPipeline.evidence_registry` is typed to the concrete `SqliteEvidenceRegistry`,
as are `StructuredEvidenceRecorder` and the contract hook. agentic-kg cannot substitute a
cloud-backed evidence registry without failing `mypy --strict`. Given Cloud Run's ephemeral
filesystem this is a live constraint, not a theoretical one.

### D-KGIS-3 (carried, P2) — `ExtractionPipeline` is ontology-unchecked by default

Two pipelines, opposite defaults, for the same safety property, on the path where ontology
drift is likeliest. Mitigated here by §3.1's explicit `candidate_validator`, but the default
should flip upstream.

### Closed by the pin / by upstream progress

- **KGIS B1** (`import kgis` requires `pytest`) — **fixed** at `baf0b67e`.
- **KGCS D-6** (`issn`/`isbn` strong namespaces) — **fixed** at `39203ad`; note the
  `orcid` consequence in §3.3.

### Adopter-owned, not a defect anywhere

- **SQLite ledger vs Cloud Run.** ADR-0012 fixes the ledger to one SQLite file with WAL and
  single-writer semantics; Cloud Run gives an ephemeral filesystem and horizontal
  autoscaling. The default topology violates both durability and the single-writer rule.
  ADR-0012 explicitly permits a backend swap behind the same ports. **Decision deferred to
  the deployment PR**, with the options being a mounted volume, a single pinned ingestion
  worker, or an agentic-kg `CandidateSink`/`LedgerReader` over Postgres validated by the
  shared contract suites.
- **The `ErDecision → CurationPlan` bridge does not exist in KGCS** and must be written in
  the adopter. This is the narrowest place to put the "never auto-merge a Paper" rule, and
  §5.4's four mandatory assertions live there.

---

## 8. UNDETERMINED register

Recorded rather than guessed. Each names what would settle it.

| # | Question | What settles it |
|---|---|---|
| **U-1** | Does the deployment's Neo4j support two databases (§4.2)? | **Partly settled, and it is bad news: CI runs `neo4j:5.26-community` (`.github/workflows/smoke-ingest.yml:41`), and Community Edition supports one user database.** So the two-database design is **untestable in CI as it stands**, whatever production runs. Two consequences that are decisions, not unknowns: the projector must be written so its separation mechanism is swappable (two databases, or one database plus a canonical label prefix and a distinct session factory), and CI must exercise the single-database fallback. What remains genuinely open is only the **production** tier — settled by the Terraform/AuraDB config or `SHOW DATABASES` against the deployed instance. If production is also Community, the separation is conventional rather than enforced everywhere, and that weakening is recorded rather than glossed. |
| **U-2** | What `source_reliability` should each source carry (arXiv / OpenAlex / Semantic Scholar / the LLM arms)? | A policy decision by the repo owner, ideally informed by the 8-paper ground-truth set. **No defensible value can be derived from the legacy data, which has no such axis.** Placeholder values must not be shipped as if measured. |
| **U-3** | Does the live database contain `PendingReview`, V1 `Problem`, or duplicate `IngestionRun` nodes? | A read-only `MATCH (n) RETURN labels(n), count(*)` plus `MATCH ()-[r]->() RETURN type(r), count(*)`. The cheapest and highest-value check in the migration; §5.3 and §6.4 both depend on it. |
| **U-4** | What is the real duplicate rate in the legacy `ProblemConcept` population? | The same snapshot, grouped by normalized `canonical_statement`. It sizes the migration's headline metric and the review workload. |
| **U-5** | What embedding model and dimension is actually deployed? | `SHOW INDEXES` on the live database plus the deployed `EMBEDDING_MODEL` env value. §3.1's representation key must match reality. |
| **U-6** | Which of the 15 mutation endpoints does the Next.js UI actually call? | **Settled for Problem, which was the part that mattered:** `packages/ui/src/lib/api.ts:194` calls `PUT /api/problems/{id}` (which crashes — C-7) and `:199` calls `DELETE /api/problems/{id}` (which works). That is why §6.3 must map `Problem` ids, and why `status='deprecated'` — not `archived`, which does not exist — is the only human mutation to preserve. The other 13 endpoints are still unenumerated; the same grep over `api.ts` settles them and scopes the 202-instead-of-200 change (§4.5). |
| **U-7** | Is the Phase-3 adopter gate open? | KGIS's governance delta places agentic-kg at Phase 3 (retrofit), after baseball-ai and the traffic shadow, and requires six migration-minimum tools to exist first. Confirm with the KGIS owner before committing to a retrofit date. |
| **U-8** | Do any callers depend on the incidental ordering of the paginated reads that have `LIMIT` without `ORDER BY` (`graph.py:39,77,127`; `topics.py:213`)? | A product decision, not a code question. |
| **U-9** | How many papers can the reconciled ground-truth set cover (§5.2.1)? | Today: **2**. Reconciling `paper_empire` and `paper_fact_completion` — both of which already have a `human/` pass, and `fact_completion` a `claude/` pass too — is the cheapest widening. Until then no AC may claim corpus-scale entity parity. |

---

## 9. Acceptance criteria for downstream PRs

### 9.0 The recurring failure mode: a check that verifies nothing

This programme has now produced the same class of defect five times, three of them inside
this spec. It is worth naming, because it is not carelessness — each instance *reads* as a
rigorous check:

| Shape | Instance |
|---|---|
| Compare two results that are both empty | The topic parity test §5.2 was written to forbid |
| Quantify over a set that is empty because the value does not exist | AC-12b's `status='archived'` |
| Assert an identity between two things defined separately, which have drifted | `NORM` stated twice, §3.1 vs §6.4.1, 13% apart |
| Cite a fixture that does not hold what you claim | The "8-paper hand-checked fixture"; the real answer key is 2 papers (§5.2.1) |
| Assert against your own re-implementation of someone else's rule | **AC-4, found below** |
| Re-run an idempotent operation and call the no-op a determinism proof | **AC-9, found below** |

**Two more instances, found by self-audit on this pass** rather than by review:

- **AC-4 was asserting a copy.** It said "every registered `CurationProfile` has
  `_auto_link_permitted == False`". That function is **private in KGCS** — `er/resolution.py:521`
  and `advisers/orchestrator.py:389`, and `grep` finds zero occurrences of it in
  `src/kgcs/__init__.py`, so it is not exported. An adopter can only import a private symbol
  across a package boundary — the exact pattern KGIS ADR-0017 forbids — or re-implement the
  predicate, in which case the test proves the *adopter's copy* returns `False` and says
  nothing whatsoever about what KGCS will do. It would stay green through an upstream change
  to the real predicate. Rewritten below as a public-data assertion **plus** a behavioural one.
- **AC-9 was proving a no-op.** It said "running the projector twice at the same published
  epoch yields a byte-identical graph". But §4.3 specifies the projector as an **idempotent
  upsert**: the second run matches every node by `identity_id` and changes nothing. The graph
  is identical because the second run did nothing, not because projection is deterministic.
  Rewritten below to project into two independent empty graphs and compare those.

**Standing rule for every AC below.** A test that can pass without exercising the thing it
names is a defect, not a passing test. Concretely: any criterion quantified over a set must
assert that set is non-empty; any criterion comparing two results must assert both are
non-empty; any criterion naming an upstream rule must exercise the upstream code rather than
a local restatement of it; any criterion citing a fixture must name the file.

### 9.1 The criteria

Each criterion is checkable and traces to a decision above.

| AC | Criterion | Traces to |
|---|---|---|
| AC-1 | No module under `packages/api/` or `packages/core/src/agentic_kg/agents/` imports `GraphMutationStore`, and no such module holds an object satisfying it. Asserted by a test, not by review. | §2 rule 1 |
| AC-2 | `Neo4jGraphStore` passes `GraphMutationStoreContract` with a pristine graph per `make_store()`, and supports at minimum `CREATE_IDENTITY`, `ATTACH_ASSERTION`, `RETRACT_ASSERTION`, `MERGE_IDENTITIES`. | §4.2 |
| AC-3 | `assert not isinstance(Neo4jGraphStore(...), LedgerReader)` and `assert not isinstance(ledger, GraphReader)`. | KGIS ADR-0011 |
| AC-4 | **Two assertions, neither importing a private symbol.** (a) Over public profile data: every registered `CurationProfile` fails at least one of `identity_authority_mode is OPEN`, `er_mode is ACTIVE`, `"AUTO_LINK" in allowable_auto_actions`. (b) Behavioural, exercising KGCS's own predicate: for every registered profile, `ErResolutionPolicy.decide(...)` on a maximally-confident match never returns `ErAction.AUTO_LINK`. (b) is the one that survives an upstream change; (a) alone is a restatement. The test also asserts the registry is non-empty. | §3.3, §5.4, §9.0 |
| AC-5 | A `FailingCompletionClient` leaves the ER decision byte-identical to the deterministic baseline. | KGCS §9 law 1 |
| AC-6 | For every projected counter, `property == degree(edge)` at the published epoch, over **the whole projected graph**. A structural invariant — it binds to no fixture. | §5.1 |
| AC-7 | Every projection parity test asserts `len(result) > 0` before comparing. Expected rows come from `reconciled/paper_cskg.gold.yml` and `reconciled/paper_cskg2.gold.yml` (**2 papers**) for entity and topic parity, and from the 10-edge table in the fixture README (`:79-90`, **8 papers**) for `CITES` parity. No test binds to `human/` or `claude/`. | §5.2.1 |
| AC-8 | The projector filters `REVOKED`; a revoked identity never appears in the projection graph. | §4.3 |
| AC-9 | Projecting the same published epoch into **two independently empty graphs** yields equal content — equality computed over projected labels, properties and edges, excluding Neo4j internal ids and the `ProjectionWatermark.built_at`. Re-running against an already-projected graph is **not** an acceptable substitute: §4.3 makes the projector an idempotent upsert, so that run is a no-op and proves nothing (§9.0). The test also asserts the projected graph is non-empty. | §4.3, §9.0 |
| AC-10 | No candidate anywhere carries a `confidence=` kwarg; every candidate carries both `extraction_confidence` and `source_reliability`. | §3.5 |
| AC-11 | Every `semantic_key` matches `<type>/<namespace>/<key>` and contains no UUID. | §3.1 |
| AC-12 | `identity_map` has a uniqueness constraint on `(legacy_label, legacy_id)` and is total over the frozen snapshot — **`Problem` included** — with every row either a mapping or an `unmapped` record naming its reason. | §6.1, §6.3 |
| AC-12b | Every legacy `Problem` with `status == ProblemStatus.DEPRECATED` (`"deprecated"` — the value `DELETE /api/problems/{id}` actually writes at `repository.py:408`) maps to a canonical identity that is **retracted, not active**. The test asserts the status value against `ProblemStatus` rather than a string literal, so a future enum change breaks the test instead of silently emptying it; and it asserts its fixture contains at least one deprecated `Problem`, so it cannot pass over an empty set. | §6.3, §9.0 |
| AC-12c | The migration join uses `K` of §6.4.1 and nothing else; the report gives the `ambiguous` count **per entity type**, so a systematically coarse key is visible rather than filed as many individual conflations. | §6.4.1, §6.4.2 step 6 |
| AC-13 | An `unmapped` legacy id returns `410 Gone`, never `404` and never a redirect. | §6.4 |
| AC-14 | The extraction pipeline is constructed with an explicit `OntologyCandidateValidator(RESEARCH_ONTOLOGY, strict=True)`. | §3.1 |
| AC-15 | `SOLVED_BY` and `HAS_TOPIC` appear in no **write vocabulary, ontology declaration, Cypher string or guardrail list**. **Negative-assertion regression guards are explicitly exempt and must be preserved** — `packages/core/tests/extraction/test_e8_purge.py:179,191,208` asserts `HAS_TOPIC` is absent, which is the guard PR #66 paid for; an assertion that a term does *not* appear is the enforcement of this AC, not a violation of it. Any equivalent guard added for `SOLVED_BY` is likewise exempt. | §5.5 |
| AC-15b | Every AC that quantifies over a set asserts that set is non-empty, and every parity AC asserts both sides are non-empty before comparing. Enforced by review against §9.0's standing rule. | §9.0 |
| AC-15c | Downstream code takes an **injected `MigrationConfig`** and does not call `get_migration_config()` inline, and reaches the optional packages only through `agentic_kg.migration.imports`. | ADR-0004 decisions 3-4 |
| AC-16 | The ER→plan bridge asserts `pairwise_complete`, asserts all cluster members are present in `entities`, and refuses a `MERGE_IDENTITIES` whose cluster contains a seed Model that is not the survivor. | §5.4, §7 D-KGIS-0/D-KGCS-1 |

---

## 10. Related documents

- [ADR-0003 — KGIS/KGCS ontology mapping and the canonical/projection split](../governance/adr/0003-kgis-kgcs-ontology-mapping.md)
- [KGIS/KGCS Adoption & Migration Orchestration Plan](../plans/2026-09-17-kgis-kgcs-adoption-migration.md) — this spec is its Phase 1 output; rules 6 and 7 (ontology mapping stays in the adopter; do not duplicate reusable KGIS/KGCS functionality) are the constraints §4.1 applies
- `llm/governance/governance-delta.md` — project principles and Domain Review Questions
- `llm/features/BACKLOG.md` — master feature catalog
- Upstream: `djjay0131/agentic-kgis` @ `baf0b67e`, `djjay0131/agentic-kgcs` @ `39203ad`
