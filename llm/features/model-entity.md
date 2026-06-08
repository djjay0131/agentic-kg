# Feature: Model / Architecture Entities (E-3)

**Status:** IMPLEMENTED
**Date:** 2026-06-06 (specified) · 2026-06-08 (implementation Units 1-10 landed)
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-3
**Depends On:** None hard. Inherits patterns from E-1 (Topic) and E-2 (ResearchConcept) and reuses the embedding infrastructure those features established.
**Decoupled From:** E-8 (extraction prompt expansion — V2 adds LLM-based Model extraction once entity exists), E-7 (cross-entity normalization), E-4 (Method — sibling entity, separate spec).

## Problem

The knowledge graph cannot answer questions about which ML models papers use, compare model adoption across research areas, or surface model lineage (BERT → RoBERTa → DeBERTa). Today, models appear only as opaque substrings inside `Baseline.name` JSON fields embedded in `Problem` nodes — for example `Baseline(name="GPT-4 baseline trained for 100 epochs", paper_doi=..., performance={...})`. A researcher asking "which papers in the ingested corpus actually used BERT?" cannot get an answer from the graph; the data exists but is not searchable, not deduplicated, and not linked to Papers as first-class graph structure. The gap analysis (§4.1 Gap 3) identifies Model as the third-most-common entity type in the reference paper's results (~400 nodes), and the third-highest-priority missing entity in our schema.

## Goals

- First-class `Model` nodes in Neo4j with `name`, optional `description`, `aliases`, `architecture`, `model_type`, `year_introduced`, `introducing_paper_doi`, `is_canonical`, embedding, and a denormalized `usage_count`.
- One new relationship type: `USES_MODEL` (Paper → Model). Idempotent MERGE on write; denormalized counter ticks transactionally with the edge.
- A repository CRUD surface that mirrors E-2: `create_or_merge_model`, `get_model`, `update_model`, `search_models_by_embedding`, `link_paper_to_model`.
- Embedding-based dedup on every create attempt with a **0.95 cosine threshold** (stricter than E-2's 0.90 because model name space is more collision-prone).
- A **hybrid open-set design**: any name can become a Model, but ~100 well-known models are curated in `seed_models.yml` and flagged `is_canonical=True`. Canonical nodes are write-protected from drift via a merge-direction rule: non-canonical → canonical merges are allowed, canonical → non-canonical merges are not.
- A CLI loader (`agentic-kg load-models`) that idempotently loads the seed YAML.
- A REST surface at `/api/models` mirroring `/api/concepts`.
- A hand-labeled 10-pair dedup eval that gates the 0.95 threshold against name-collision regressions, gated at the verify step under `@pytest.mark.costly`.
- A scripted "done demo" that manually links ≥ 5 real Papers from the staging KG to seed Models via the CLI and confirms `GET /api/models/{id}/papers` returns them.

## Non-Goals

- **Migration of existing `Baseline.name` strings into Model nodes.** Existing Problems' baseline strings stay where they are. Model nodes only get populated via the seed YAML loader, the `/api/models` endpoint, and the CLI for v1. The future E-8-V2 extractor will populate from paper text. Rationale: baseline strings are dirty in ways that produce polluted Model nodes a curated set never recovers from; a clean forward path beats a backward-compat migration. Reflects the user's general "rebuild over migrate" preference.
- **`VARIANT_OF` lineage edges.** Deferred to v2. v1 ships only `USES_MODEL`. Until V2, lineage chains (BERT → RoBERTa → DeBERTa) cannot be traversed in the graph; this is an accepted v1 limitation in exchange for smaller scope and no curation drift.
- **`IMPLEMENTS` edge from embedded Baselines to Models.** Promoting `Baseline` from embedded JSON to a graph node is a separate scope expansion; skipping in v1.
- **`BENCHMARKED_ON` (ProblemConcept → Model).** Deferred — emerges naturally from E-8-V2 extractor.
- **Closed enums for `architecture` / `model_type`.** Free-form strings in v1; promote to enums only if data shows clear clusters with low cardinality (gap analysis suggests transformer / cnn / rnn for architecture; language_model / vision_model / multimodal for model_type, but committing now would constrain seed entries unhelpfully).
- **LLM-based Model extraction from papers.** That is E-8 V2.
- **Frontend UI for Model browsing.** API + Neo4j Browser for now; UI is a follow-up if researchers actually ask for it.

## User Stories

- **As a researcher**, I want to ask "which papers used BERT?" and get a populated answer, so I can survey model adoption across a topic.
- **As a researcher**, I want to search "transformer" and get a ranked list of transformer-family models, so I can discover variants I hadn't named.
- **As a developer**, I want `Model` to follow the same Pydantic + repository + API pattern as `ResearchConcept` so I don't learn a new abstraction.
- **As a curator**, I want to maintain a YAML of ~100 canonical models in git so I can govern lineage and naming via PR review, without writing migration code each time the list changes.
- **As a system operator**, I want re-running `agentic-kg load-models` to be safe and idempotent so seed updates are low-risk.

## Design Approach

### Data Model

New entity in `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`:

```python
class Model(BaseModel):
    """An ML model / architecture as a first-class graph node."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    # Free-form strings in v1 (no enum); revisit if the data clusters.
    architecture: Optional[str] = None         # "transformer", "cnn", "mamba", ...
    model_type: Optional[str] = None           # "language_model", "vision_model", ...
    year_introduced: Optional[int] = None      # 2018 for BERT
    introducing_paper_doi: Optional[str] = None

    # Hybrid open-set: True only for seed YAML entries.
    is_canonical: bool = False

    embedding: Optional[list[float]] = None    # 1536 dims (text-embedding-3-small)
    usage_count: int = Field(default=0, ge=0)  # Denormalized: count of USES_MODEL edges
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def to_neo4j_properties(self) -> dict:
        data = self.model_dump(exclude={"embedding"})
        data["aliases"] = json.dumps(self.aliases)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data
```

### New Relationship

| Relationship | From → To | Purpose |
|---|---|---|
| `USES_MODEL` | Paper → Model | Paper uses or benchmarks against this model. |

### Schema Changes

- Constraint: `model_id_unique` on `Model.id`.
- Indexes: `model_name_idx`, `model_is_canonical_idx`.
- Vector index: `model_embedding_idx` (1536 dims, cosine).
- Schema version bumped per the existing `SchemaManager` pattern (v4 → v5 if E-2 was v4).

### Embedding-Based Dedup with Canonical Protection

On every `create_or_merge_model` call:

1. Embed `"{name}: {description}"` (or just `name` if no description).
2. Vector search existing Model nodes, top-k=5, min cosine ≥ **0.95** (configurable).
3. If a candidate scores ≥ threshold:
   - **Canonical protection rule:** if the existing candidate is `is_canonical=True`, the incoming call's `is_canonical=False` does NOT downgrade it. The incoming name is added to the canonical node's aliases; canonical state is preserved. If the incoming call also passes `is_canonical=True`, the merge is idempotent (no field changes for matching seed loads).
   - If the existing candidate is `is_canonical=False` and the incoming `is_canonical=True`, the merge promotes the existing node to canonical and overwrites the name with the canonical one (the prior name moves to aliases). This handles the case where a curator's seed lands after community contributions populated a near-duplicate.
   - Otherwise (both non-canonical): standard E-2-style alias merge.
4. If no candidate is above threshold, create a new node with the supplied `is_canonical` value.

### Canonical Seed Loader

`packages/core/src/agentic_kg/knowledge_graph/seed_models.py` (new):

```python
DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "seed_models.yml"

def load_seed_models(repo: Neo4jRepository, path: Path = DEFAULT_SEED_PATH) -> dict:
    """Load curated canonical Models from YAML. Idempotent."""
    entries = yaml.safe_load(path.read_text())
    created, merged = 0, 0
    for entry in entries:
        _, is_new = repo.create_or_merge_model(
            name=entry["name"],
            description=entry.get("description"),
            aliases=entry.get("aliases", []),
            architecture=entry.get("architecture"),
            model_type=entry.get("model_type"),
            year_introduced=entry.get("year_introduced"),
            introducing_paper_doi=entry.get("introducing_paper_doi"),
            is_canonical=True,
        )
        created += int(is_new)
        merged += int(not is_new)
    return {"created": created, "merged": merged}
```

Initial seed (`data/seed_models.yml`): ~100 entries covering the most-cited models per the OpenAlex top-models list, spanning language (BERT, GPT-2/3/4, T5, LLaMA, Mistral, Claude, Gemini, ...), vision (ResNet, ViT, CLIP, Stable Diffusion, ...), multimodal (CLIP, Flamingo, GPT-4V, ...), classical ML (XGBoost, LightGBM, ...), and graph (GCN, GraphSAGE, GAT, ...). Source list to be finalized at implementation time and reviewed by someone other than the spec author (knowledge-steward persona during next memory:update).

### Repository Surface

Added to `packages/core/src/agentic_kg/knowledge_graph/repository.py`:

| Method | Purpose |
|---|---|
| `create_or_merge_model(...)` | Embedding-dedup'd create, returns `(Model, created: bool)` |
| `get_model(model_id)` | Fetch by ID; raises `NotFoundError` |
| `get_model_by_name(name)` | Case-sensitive name lookup; canonical preferred on tie |
| `update_model(model_id, **fields)` | Partial update |
| `delete_model(model_id, force=False)` | `DETACH DELETE` semantics — removes the node and ALL incident `USES_MODEL` edges in one shot. Refuses if `is_canonical=True` unless `force=True` is passed. Lost edges rebuild via re-extraction (rebuild-over-migrate ethos); no audit log written. |
| `search_models_by_embedding(embedding, top_k, min_score)` | Vector search |
| `link_paper_to_model(paper_doi, model_id)` | Idempotent `USES_MODEL` edge + transactional `usage_count++` |
| `unlink_paper_from_model(paper_doi, model_id)` | Reverse with `usage_count--` |
| `get_papers_for_model(model_id, limit)` | Inverse traversal |

`_link_entity_to_concept` in E-2 hardcodes `("Paper", "doi", "paper_count")` and the `DISCUSSES` / `INVOLVES_CONCEPT` set. E-3 **generalizes** that helper: rename `_CONCEPT_RELATIONSHIPS` → `_NODE_LINK_RELATIONSHIPS` and add `"USES_MODEL": ("Paper", "doi", "usage_count")` (and rename the helper to `_link_entity_to_node` or similar). Existing concept-linking call sites continue to work through the generalized helper. Verify gate must re-confirm `INVOLVES_CONCEPT` and `DISCUSSES` semantics are unchanged after the rename (covered by the existing E-2 regression suite, which AC-12 requires to pass). **Rationale (Tech Lead review, Q5):** the marginal risk to E-2 is small, and E-4 (Method, `APPLIES_METHOD`) is the next sibling — we'd hit the same generalization choice in two months and would prefer one helper over three copies.

### API Surface

Added to `packages/api/src/agentic_kg_api/routers/models.py` (new):

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/models` | List models, paginated, filters: `name=`, `architecture=`, `is_canonical=` |
| `GET` | `/api/models/{id}` | Detail with `usage_count` and paginated linked papers |
| `GET` | `/api/models/{id}/papers` | Papers linked via USES_MODEL |
| `GET` | `/api/models/search?q=...` | Vector similarity search over model embeddings |
| `POST` | `/api/models` | Create with dedup check (returns `created=true|false`) |
| `POST` | `/api/models/{id}/link-paper` | Link a Paper to this Model |
| `DELETE` | `/api/models/{id}` | Delete; refuses canonical unless `?force=true` |

### CLI Commands

| Command | Description |
|---|---|
| `agentic-kg load-models [--file PATH]` | Load seed YAML into the graph (idempotent) |
| `agentic-kg create-model --name <name> [--description <desc>] [--aliases a,b]` | Manual create with dedup |
| `agentic-kg link-model --paper-doi <doi> --model-id <id>` | Link a Paper to a Model |

### Dedup Eval Set

`packages/core/tests/extraction/fixtures/e3_dedup/dedup_pairs.yml` (new), ~10 hand-labeled pairs. Schema:

```yaml
- input: "bert"
  expected: "BERT"                # name in seed YAML
- input: "BERT-large"
  expected: "BERT"
- input: "the BERT model"
  expected: "BERT"
- input: "GPT-4-turbo"
  expected: "GPT-4"
- input: "ResNet-50"
  expected: "ResNet"
- input: "completely unrelated thing"
  expected: NEW                   # MUST NOT merge into anything
- input: "Mistral-7B"
  expected: "Mistral"
- input: "DistilBERT"
  expected: NEW                   # Distinct variant; should NOT merge into BERT
- input: "T5-base"
  expected: "T5"
- input: "stablediffusion"
  expected: "Stable Diffusion"
```

The eval test runs after `load_seed_models()` has populated the canonical set, then calls `create_or_merge_model(input)` for each entry and asserts the resolved canonical name matches `expected` (or that a new node was created when `expected: NEW`). **Gate: 10/10 correct.** A single mis-merge fails the precision gate. Threshold tuning is allowed; any change to `DEFAULT_MODEL_DEDUP_THRESHOLD` re-runs this eval.

### "Done Demo" — manual linking exercise

After seed load, the verify step requires:

1. Pick 5 real Papers from the staging KG (different DOIs, ideally covering multiple research areas).
2. Manually identify their primary model via paper abstract/intro reading.
3. Run `agentic-kg link-model --paper-doi <doi> --model-id <model>` for each.
4. Call `GET /api/models/{model_id}/papers` and confirm the linked paper appears.
5. Capture the trace in the verification record.

This demonstrates the path to a useful state, not just that entities exist.

## Sample Implementation

```python
# packages/core/src/agentic_kg/knowledge_graph/repository.py (additions)

DEFAULT_MODEL_DEDUP_THRESHOLD = 0.95


def create_or_merge_model(
    self,
    name: str,
    description: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    architecture: Optional[str] = None,
    model_type: Optional[str] = None,
    year_introduced: Optional[int] = None,
    introducing_paper_doi: Optional[str] = None,
    is_canonical: bool = False,
    embedding: Optional[list[float]] = None,
    threshold: Optional[float] = None,
) -> tuple[Model, bool]:
    """Embedding-dedup'd create. Returns (model, created).

    Canonical protection: incoming non-canonical never overrides existing
    canonical. Incoming canonical promotes a matching non-canonical (prior
    name moves to aliases). Mirrors E-2's create_or_merge_research_concept
    plus the canonical-merge rule.
    """
    threshold = threshold if threshold is not None else DEFAULT_MODEL_DEDUP_THRESHOLD
    if embedding is None:
        try:
            embedding = generate_model_embedding(name, description)
        except Exception as e:
            logger.warning(f"Embedding failed for model '{name}': {e}")

    if embedding is not None:
        candidates = self.search_models_by_embedding(
            embedding=embedding, top_k=5, min_score=threshold,
        )
        if candidates:
            best, score = candidates[0]
            logger.info(f"Model dedup: '{name}' -> '{best.name}' (score={score:.3f})")

            # Canonical protection
            new_aliases = sorted(set(best.aliases) | set(aliases or []) | (
                {best.name} if is_canonical and not best.is_canonical and best.name != name else set()
            ) | (
                {name} if name != best.name and not (is_canonical and not best.is_canonical) else set()
            ))
            new_name = name if (is_canonical and not best.is_canonical) else best.name
            new_canonical = best.is_canonical or is_canonical

            self.update_model(
                best.id,
                name=new_name,
                aliases=new_aliases,
                description=best.description or description,
                is_canonical=new_canonical,
            )
            return self.get_model(best.id), False

    # No match — new node.
    model = Model(
        name=name, description=description,
        aliases=list(aliases or []),
        architecture=architecture, model_type=model_type,
        year_introduced=year_introduced,
        introducing_paper_doi=introducing_paper_doi,
        is_canonical=is_canonical,
        embedding=embedding,
    )
    self._create_model_node(model)
    return model, True


def link_paper_to_model(self, paper_doi: str, model_id: str) -> bool:
    """USES_MODEL with idempotent MERGE + transactional usage_count++."""
    def _link(tx, doi, mid):
        result = tx.run(
            """
            MATCH (p:Paper {doi: $doi})
            MATCH (m:Model {id: $mid})
            OPTIONAL MATCH (p)-[existing:USES_MODEL]->(m)
            WITH p, m, existing
            FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                CREATE (p)-[:USES_MODEL]->(m)
                SET m.usage_count = m.usage_count + 1,
                    m.updated_at = $now
            )
            RETURN existing IS NULL AS created
            """,
            doi=doi, mid=mid, now=datetime.now(timezone.utc).isoformat(),
        )
        record = result.single()
        if record is None:
            raise NotFoundError(f"Paper {doi!r} or Model {mid!r} not found")
        return bool(record["created"])

    with self.session() as session:
        return self._execute_with_retry(session, _link, paper_doi, model_id)
```

## Edge Cases & Error Handling

### Two seed entries collide on embedding (canonical vs canonical)
- **Scenario:** Seed YAML contains both `"GPT-4"` and `"GPT-4 Turbo"`; embedding cosine ≥ 0.95.
- **Behavior:** First load: `"GPT-4"` is created with `is_canonical=True`. Second load attempt: `"GPT-4 Turbo"` finds the match, the canonical protection rule fires (both canonical), names do not change, aliases merge. Operationally undesirable: the curator intended two distinct nodes. **Mitigation:** seed loader logs a WARNING when an incoming canonical entry merges into another canonical entry; the curator must split them via lower-similarity names or explicit `id` pinning in the YAML.
- **Test:** Unit test where two seed entries embed close; assert merge happens, WARN is emitted with both names.

### Mixed-case alias matching
- **Scenario:** Incoming call `create_or_merge_model(name="bert")` against existing canonical `Model(name="BERT", aliases=["bert-base"])`.
- **Behavior:** Embedding match fires (case-insensitive at the embedding layer), canonical protection preserves `"BERT"` as the canonical name, `"bert"` is added to aliases (already present? deduplicated by `set()` in alias merge).
- **Test:** Unit test asserting exact-string alias dedup post-merge.

### `is_canonical` flag changed via `update_model`
- **Scenario:** Operator runs `update_model(model_id, is_canonical=False)` on a node previously canonical. (Why would they? Removing a seed entry.)
- **Behavior:** Allowed. Downgrades the node. A subsequent `create_or_merge_model(name=..., is_canonical=True)` of a near-duplicate can now promote a different node to canonical. Logged at INFO level for audit. **Future safeguard:** if the verify-time audit shows curators downgrading nodes accidentally, lock the field behind a `--force` parameter.
- **Test:** Integration test asserting downgrade succeeds and a later seed re-load can promote a different node.

### Paper or Model missing on `link_paper_to_model`
- **Scenario:** Operator calls the CLI with a typo'd DOI or model id.
- **Behavior:** `link_paper_to_model` raises `NotFoundError` with a message naming which side is missing. CLI catches and prints a clear error; exit code non-zero. `usage_count` is NOT incremented (transactional).
- **Test:** Unit test for each missing side.

### Empty or malformed seed YAML
- **Scenario:** Operator commits an entry missing `name`, or `aliases: null`, or duplicate names.
- **Behavior:** Parser validates each entry against a small Pydantic `_SeedModelEntry` model at load time. Validation errors halt the load with line/index context. Duplicate names within the YAML are also caught at parse time, not at merge time (clearer failure mode).
- **Test:** Unit test with each malformed shape.

### Vector embedding service unavailable
- **Scenario:** OpenAI embedding endpoint times out during a load or create call.
- **Behavior:** `generate_model_embedding` raises; `create_or_merge_model` falls back to **create-without-embedding**, logs a WARN, and proceeds. The node carries `embedding=None`. A later reconciliation pass (manual operator command, follow-up) can backfill embeddings. **Risk:** without embedding, dedup is skipped for that call, so a duplicate node could be created. Acceptable for v1; the operator will see it in the dashboard and can manually merge.
- **Test:** Unit test mocking embedding failure; assert node created with `embedding=None` and WARN logged.

### Concurrent loads of the same seed file
- **Scenario:** Two operators run `agentic-kg load-models` simultaneously.
- **Behavior:** Each create attempt embeds + vector-searches + merges via the idempotent path. The transaction-bound MERGE on USES_MODEL is safe, but the create path is two operations (search then create). Race: both find no match, both create. **Result:** two near-duplicate nodes that the next seed re-run would merge. Documented as a known limitation; production use should serialize loads. Not blocking v1.

### Existing Baseline strings reference a now-renamed model
- **Scenario:** A curator changes a seed entry's canonical name from `"GPT-4"` to `"GPT-4 (OpenAI 2023)"`. The embedded `Baseline.name` strings in existing Problem nodes still say `"GPT-4"`.
- **Behavior:** No automatic update of Baseline strings (we are not migrating). The next time E-8-V2 re-extracts a paper, the new extraction will route through `create_or_merge_model` and merge to the renamed node. Old Problem nodes carry stale baseline names until re-extraction; see the user's "rebuild over migrate" preference.

## Acceptance Criteria

### AC-1: Model entity model
- **Given** `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`
- **When** imported
- **Then** the `Model` class exists with the field shape described in *Data Model* (`id`, `name`, `description`, `aliases`, `architecture`, `model_type`, `year_introduced`, `introducing_paper_doi`, `is_canonical`, `embedding`, `usage_count`, `created_at`, `updated_at`) and a working `to_neo4j_properties()` method.
- **And** importing the module does not read `seed_models.yml`.

### AC-2: Schema additions
- **Given** the schema manager
- **When** the schema is initialized against a fresh Neo4j
- **Then** the `model_id_unique` constraint, `model_name_idx` index, `model_is_canonical_idx` index, and `model_embedding_idx` vector index (1536 cosine) exist.
- **And** the schema version is bumped per the existing `SchemaManager` pattern.

### AC-3: Embedding-based dedup with canonical protection
- **Given** a non-empty Model graph
- **When** `create_or_merge_model(name="bert")` runs against an existing `Model(name="BERT", is_canonical=True)` whose embedding scores ≥ 0.95
- **Then** the function returns the existing canonical model, `created=False`, the canonical node's name is unchanged, and `"bert"` is in its aliases.
- **And** when the existing node is `is_canonical=False` and the incoming call is `is_canonical=True` (above threshold), the existing node is promoted: name overwritten to the incoming canonical name, prior name moved to aliases, `is_canonical=True`.
- **And** when both sides are non-canonical, standard alias merge applies (E-2 behavior).

### AC-4: New node when no candidate above threshold
- **Given** an empty Model graph
- **When** `create_or_merge_model(name="completely unrelated thing")` runs
- **Then** a new node is created with `created=True` and the supplied `is_canonical` value.

### AC-5: Seed loader is idempotent
- **Given** a seed YAML file with N valid entries
- **When** `load_seed_models()` is called twice in a row
- **Then** the first call returns `{"created": N, "merged": 0}` and the second returns `{"created": 0, "merged": N}`.
- **And** the resulting node count in the graph is exactly N (no duplicates).
- **And** every loaded node carries `is_canonical=True`.

### AC-6: USES_MODEL edge + transactional usage_count
- **Given** a Paper node and a Model node both exist
- **When** `link_paper_to_model(paper_doi, model_id)` runs
- **Then** a `USES_MODEL` edge exists from Paper to Model and the model's `usage_count` is incremented by 1.
- **And** calling the same link twice does NOT double-increment `usage_count` (idempotent MERGE).
- **And** `unlink_paper_from_model` removes the edge and decrements `usage_count` (clamped at 0).

### AC-7: Repository CRUD surface
- **Given** the implementation merged
- **When** `get_model`, `get_model_by_name`, `update_model`, `delete_model`, `search_models_by_embedding`, `link_paper_to_model`, `unlink_paper_from_model`, `get_papers_for_model` are exercised
- **Then** each returns the documented shape and raises `NotFoundError` where appropriate.
- **And** `delete_model` refuses canonical nodes unless `force=True` is passed.

### AC-8: API surface
- **Given** the running FastAPI app
- **When** the documented `/api/models` endpoints are exercised
- **Then** each returns the expected JSON shape and HTTP status code.
- **And** `POST /api/models` returns `created: true|false` based on the dedup result.
- **And** `DELETE /api/models/{id}?force=false` on a canonical node returns 409 Conflict with a clear error message.

### AC-9: CLI commands
- **Given** the installed CLI
- **When** `agentic-kg load-models`, `agentic-kg create-model --name ...`, and `agentic-kg link-model --paper-doi ... --model-id ...` are invoked
- **Then** each prints a confirming summary and exits 0 on success.
- **And** `load-models` is idempotent over re-runs against the same graph.

### AC-10: Dedup eval — precision + anti-gaming recall tripwire
- **Given** the 10-pair hand-labeled fixture `tests/extraction/fixtures/e3_dedup/dedup_pairs.yml` (8 merge-expecting pairs + 2 `expected: NEW` pairs), the seed YAML loaded, and `@pytest.mark.costly` opted in
- **When** the eval test runs `create_or_merge_model(input)` for each pair
- **Then** **all 10 pairs resolve correctly** — each input either merges to its expected canonical name, OR correctly creates a new node when `expected: NEW`. A single mis-merge or missed-merge fails the precision gate.
- **And** an **anti-gaming recall tripwire** holds: at least **6 of the 8 merge-expecting pairs** must successfully merge (not create a new node). This catches the threshold-tuning regression where someone bumps `DEFAULT_MODEL_DEDUP_THRESHOLD` to clear a false-positive precision miss but silently destroys recall.
- **And** any change to `DEFAULT_MODEL_DEDUP_THRESHOLD` re-runs this eval and must clear **both** the 10/10 precision gate AND the 6/8 merge tripwire. Same governance shape as E-8's concept recall tripwire (pattern iii).
- **And** the eval prints per-pair pass/fail and the merge/no-merge tally in the verify record.

### AC-11: Integration test against testcontainers Neo4j (replaces "done demo")
- **Given** a fresh testcontainers Neo4j instance with the schema initialized, the seed YAML loaded, and 5 synthetic Paper nodes seeded with abstracts mentioning specific seed Models
- **When** the integration test invokes `agentic-kg link-model --paper-doi <doi> --model-id <id>` (or the equivalent repository call) for each pair
- **Then** `GET /api/models/{model_id}/papers` returns each linked paper for the corresponding model.
- **And** the test runs in CI under the `pytest.mark.integration` marker against a testcontainers Neo4j (no human-in-the-loop demo required at verify).
- **And** a separate "real-data demo" against the staging KG remains available as **operator-side validation**, captured in `docs/runbooks/e3-staging-demo.md` for posterity but not gating the spec. This was deliberately restructured from a verify-gate demo to a regression-safe integration test to avoid the verify-blocker pattern E-8's AC-12 hit (see Review Record).

### AC-12: Existing functionality untouched
- **Given** the existing test suite
- **When** E-3 is merged
- **Then** all existing tests in `packages/core/tests/` and `packages/api/tests/` continue to pass with zero modifications.
- **And** `Baseline`, `Problem`, `ProblemMention`, `ProblemConcept`, `Topic`, `ResearchConcept`, `Paper`, `Author` entity files are unchanged except for the additive `Model` import (mirrors E-2's untouched-existing claim).

### AC-13: Embedding service failure is tolerated
- **Given** the embedding service returns an error
- **When** `create_or_merge_model` is called
- **Then** the function creates a new node with `embedding=None` and logs a WARN — the call does not raise.
- **And** dedup is skipped for that call (acceptable v1 limitation; the operator can re-run with a healthy embedding service to consolidate).

## Technical Notes

- **Affected files:**
  - Create: `knowledge_graph/seed_models.py`, `knowledge_graph/data/seed_models.yml`, `api/routers/models.py`, `tests/knowledge_graph/test_model_repository.py`, `tests/extraction/fixtures/e3_dedup/dedup_pairs.yml`, `tests/extraction/test_e3_dedup_eval.py`
  - Modify: `knowledge_graph/models/entities.py` (add `Model`), `knowledge_graph/repository.py` (add Model methods), `knowledge_graph/schema.py` (add constraint/indexes), `knowledge_graph/embeddings.py` (add `generate_model_embedding`), `api/main.py` (mount router), `cli.py` (add three subcommands)
  - Touch: none in `problem_extractor.py`, `kg_integration_v2.py`, agents, or extractor modules.
- **Reuse:** `BaseLLMClient` not needed (no LLM extraction in v1). Existing `search_research_concepts_by_embedding` is the template for `search_models_by_embedding`. Existing `_link_entity_to_concept` is generalized to `_NODE_LINK_RELATIONSHIPS` covering DISCUSSES, INVOLVES_CONCEPT, USES_MODEL (and future APPLIES_METHOD); E-2 regression suite must remain green post-rename.
- **Confidence threshold:** `DEFAULT_MODEL_DEDUP_THRESHOLD = 0.95`. Changes require re-running the AC-10 eval gate.
- **Seed YAML governance:** entries land via PR review. Adding a seed entry is normal git workflow; removing one (or renaming the canonical name) requires the dedup eval to still pass.
- **Vector index dimensions:** 1536 (text-embedding-3-small), consistent with E-1 and E-2.
- **No new top-level dependencies.**

## Dependencies

- **None hard.** Patterns inherited from E-1 (Topic) and E-2 (ResearchConcept).
- **Soft:** the existing embedding service (`generate_research_concept_embedding`) is reused via a sibling `generate_model_embedding`.
- **Sibling:** E-4 (Method entity) is the next-most-similar spec; care taken so E-4 can reuse seed-loader and dedup-eval shapes by copy-rename.

## Open Questions

- **Architecture / model_type enums.** Free-form in v1; promote to closed enums if the seed YAML shows consistent low-cardinality clusters during curation. Decision deferred to implementation review.
- **`introducing_paper_doi` as a graph edge vs a property.** Currently a string property. Future: introduce an `INTRODUCED_BY` edge (Model → Paper). Out of scope for v1.
- **Seed entries with explicit `id` for stable identity across renames.** Currently `id` is auto-generated UUID. If the curator wants to rename a canonical Model without losing inbound edges, they need stable id pinning in the YAML. Defer until the rename pain actually shows up.
- **Concurrency hardening for `load-models`.** Documented race in *Edge Cases*; serialization is the operator's job for v1. Future: add a Neo4j-level advisory lock.
- **Eval-set growth.** v1 ships 10 pairs. If the seed grows past 200 entries, expand the eval to 25-30 pairs. Tracked as a follow-on.
- **Embedding-outage reconciliation tooling.** AC-13 tolerates create-without-embedding under embedding-service outage but provides no detection or recovery path beyond manual `DELETE /api/models/{id}?force=true` + re-extraction. If real operator pain materializes, follow-up spec for an `agentic-kg reconcile-models` CLI that backfills embeddings and consolidates near-duplicates. Decision (QA Q6): defer per the rebuild-over-migrate ethos; document the recovery hole here and the manual workflow in `docs/runbooks/`.
- **Seed YAML CI lint.** A GitHub Actions workflow that validates `seed_models.yml` against the `_SeedModelEntry` schema on every PR would catch malformed entries before merge. Out of v1 scope but worth picking up as a small follow-up if a bad commit ever lands in master.

## Review Record

Dual-persona review completed 2026-06-06. Six questions, all resolved:

**Tech Lead review:**
- **Q1 — Canonical-promotion silent rename behavior.** Decision: seed curation is authoritative. When a `is_canonical=True` create call matches an existing non-canonical node above threshold, the existing node is promoted: name overwritten with the canonical name, prior name moved to aliases, `usage_count` preserved across the rename. No special audit log. Rationale: the curator IS the source of truth for canonical naming; dashboards correctly reflect the canonical name after promotion. Locked in AC-3 and *Embedding-Based Dedup with Canonical Protection*.
- **Q3 — AC-11 verify-gate hazard.** Decision: AC-11 restructured from a human-driven "done demo" on staging to a regression-safe integration test against testcontainers Neo4j with 5 synthetic Paper nodes. The original human demo lives on as optional operator-side validation in `docs/runbooks/e3-staging-demo.md`. Rationale: E-8's AC-12 hand-labeling is currently a verify-blocker; this AC sidesteps the same trap.
- **Q5 — `_link_entity_to_concept` generalization.** Decision: generalize the helper now (rename `_CONCEPT_RELATIONSHIPS` → `_NODE_LINK_RELATIONSHIPS`, register USES_MODEL) rather than sibling-copying it. E-4 lands next and would force the same choice anyway. The E-2 regression suite remains the safety net.

**QA review:**
- **Q2 — Anti-gaming recall tripwire.** Decision: AC-10 augmented with a 6-of-8-merge-expecting-pairs floor in addition to the 10/10 precision gate. Threshold changes must clear both. Same governance shape as E-8's concept recall tripwire (pattern iii).
- **Q4 — Delete semantics.** Decision: `delete_model` uses `DETACH DELETE` — node and all inbound `USES_MODEL` edges removed in one shot. Canonical protection via `force=True`. No audit log; re-extraction recreates the edge structure. Aligns with rebuild-over-migrate.
- **Q6 — Embedding-outage reconciliation.** Decision: defer. v1 tolerates the outage per AC-13 and operators consolidate manually via DELETE + re-extract; the recovery hole is documented in *Open Questions* and a follow-up reconcile-models CLI is acknowledged but not specced. Aligns with rebuild-over-migrate + don't-pre-engineer-ops-tooling.
