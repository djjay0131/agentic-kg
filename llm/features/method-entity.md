# Feature: Method / Methodology Entities (E-4)

**Status:** SPECIFIED
**Date:** 2026-06-09
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-4
**Depends On:** E-3 (Model) — reuses the `_NODE_LINK_RELATIONSHIPS` generalization landed in E-3 to add `APPLIES_METHOD` in one line.
**Decoupled From:** E-8 (extraction prompt expansion V2 — will populate Method nodes from paper text), E-7 (cross-entity normalization), E-5 (citation graph).

## Problem

The knowledge graph cannot answer "which papers use fine-tuning", "what methods does this problem area rely on", or trace methodology adoption trends. Today, methods like "fine-tuning", "transfer learning", "contrastive learning", and "data augmentation" appear only buried inside `Baseline.name` strings and `Constraint.text` fields — unsearchable, undeduplicated, and unlinked. The gap analysis (§4.1 Gap 4) identifies Method as the fourth-most-common entity type in the reference paper's results (~200 nodes), with two natural relationships: `(:Paper)-[:APPLIES_METHOD]->(:Method)` and `(:ProblemConcept)-[:ADDRESSED_BY]->(:Method)`. v1 ships only the former; the problem-side relationship is deferred to v2.

## Goals

- First-class `Method` nodes in Neo4j with `name`, optional `description`, `aliases`, optional `method_type`, embedding, and a denormalized `usage_count`.
- One new relationship type: `APPLIES_METHOD` (Paper → Method). Idempotent MERGE on write; denormalized counter ticks transactionally with the edge.
- A repository CRUD surface mirroring E-2 ResearchConcept: `create_or_merge_method`, `get_method`, `get_method_by_name`, `update_method`, `delete_method`, `search_methods_by_embedding`, `link_paper_to_method`, `unlink_paper_from_method`, `get_papers_for_method`.
- Embedding-based dedup on every create attempt with a **0.90 cosine threshold** (matches E-2's `ResearchConcept`; Method's name space is conceptual phrases, not Model-style identities, so the stricter 0.95 isn't warranted).
- Pure open-set design — **no `is_canonical` flag, no seed YAML, no canonical-protection rules**. Method names are conceptual phrases ("fine-tuning", "contrastive learning"), not identities with a clear curator-driven canonical form. The hybrid open-set machinery E-3 built specifically for Model collisions does not earn its keep here.
- A REST surface at `/api/methods` mirroring `/api/concepts`.
- A CLI: `agentic-kg create-method` and `agentic-kg link-method` (no `load-methods`).
- An automated testcontainers integration test ("done demo") that loads ad-hoc Methods, links 5 synthetic Papers to them via the CLI, and confirms `GET /api/methods/{id}/papers` returns them. This is the verify gate.
- No dedup eval-set in v1. Method dedup quality validation rides on the testcontainers integration test and on E-8 V2's extractor eval when that lands.

## Non-Goals

- **`ADDRESSED_BY` (ProblemConcept → Method) relationship.** Deferred to v2. v1 ships only `APPLIES_METHOD`. Rationale: matches E-3's conservative scope decision (Model shipped `USES_MODEL` only, deferred `BENCHMARKED_ON`). Real demand for `ADDRESSED_BY` will surface naturally from E-8 V2's extractor — manually wiring problem↔method by hand in v1 is operator-overhead with low payoff, and we'd rather drive the relationship from real extraction evidence than from pre-baked guesses.
- **Migration of existing `Baseline.name` / `Constraint.text` strings into Method nodes.** Existing Problems' baseline / constraint strings stay where they are. Method nodes only get populated via the `/api/methods` endpoint, the CLI, and E-8 V2 (extractor). Same rebuild-over-migrate ethos that drove E-3 (and the user's recurring "we can rebuild or re-extract" preference).
- **`is_canonical` flag, seed YAML loader, `--canonical` CLI flag, canonical-protection rules in `create_or_merge_method`, canonical-canonical WARN log.** All deliberately omitted. See *Design Approach* for rationale.
- **Dedup eval-set / `@pytest.mark.costly` eval gate.** E-3 needed it for canonical-protection regression coverage; without canonical protection, the eval set's primary job is gone. Threshold quality validation deferred to E-8 V2 and to ad-hoc spot-checks if the dedup behavior gets flagged in practice.
- **Closed enums for `method_type`.** Free-form strings in v1. Promote to enums later if the data clusters cleanly with low cardinality.
- **LLM-based Method extraction from papers.** That is E-8 V2.
- **Frontend UI for Method browsing.** API + Neo4j Browser for now.
- **`EXTENDS_METHOD` (Method → Method) lineage edges.** The gap analysis mentions them; deferred to v2 alongside `ADDRESSED_BY`.

## User Stories

- **As a researcher**, I want to ask "which papers apply contrastive learning?" and get a populated answer, so I can survey method adoption across a topic area.
- **As a researcher**, I want to search "knowledge distillation" and get a ranked list of related methods, so I can discover variants I hadn't named.
- **As a developer**, I want `Method` to follow the same Pydantic + repository + API pattern as `ResearchConcept` so I don't learn a new abstraction.
- **As a developer extending the pipeline later** (E-8 V2), I want `APPLIES_METHOD` and `ADDRESSED_BY` to be one-line additions to the existing `_NODE_LINK_RELATIONSHIPS` map. v1 ships APPLIES_METHOD; V2 adds ADDRESSED_BY the same way.

## Design Approach

### Data Model

New entity in `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`:

```python
class Method(BaseModel):
    """A research method / methodology as a first-class graph node."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    # Free-form string in v1; revisit if data clusters.
    method_type: Optional[str] = None         # "training", "evaluation", "data_processing", ...

    embedding: Optional[list[float]] = None    # 1536 dims (text-embedding-3-small)
    usage_count: int = Field(default=0, ge=0)  # Denormalized: count of APPLIES_METHOD edges

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def to_neo4j_properties(self) -> dict:
        """Aliases JSON-encoded; timestamps ISO-encoded; embedding excluded."""
        data = self.model_dump(exclude={"embedding"})
        data["aliases"] = json.dumps(self.aliases)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data
```

### New Relationship

| Relationship | From → To | Purpose |
|---|---|---|
| `APPLIES_METHOD` | Paper → Method | Paper uses or applies this method. |

### Schema Changes

- Constraint: `method_id_unique` on `Method.id`.
- Indexes: `method_name_idx`.
- Vector index: `method_embedding_idx` (1536 dims, cosine).
- Schema version bumped per the `SchemaManager` pattern (v5 → v6 after E-3).

### Embedding-Based Dedup

Standard E-2 ResearchConcept shape — **no canonical protection**:

1. Embed `"{name}: {description}"` (or just `name` if no description).
2. Vector search existing Method nodes, top-k=5, min cosine ≥ **0.90** (configurable; matches `DEFAULT_CONCEPT_DEDUP_THRESHOLD`).
3. If a candidate scores ≥ threshold: merge — incoming name joins the existing node's aliases (existing name wins, deduplicated); the merged node's `description` and `method_type` get populated from the incoming call only if the existing values are `None`. Return `(existing, created=False)`.
4. If no candidate is above threshold: create a new node with `(method, created=True)`.

On embedding service failure: fall back to **create-without-embedding** (dedup skipped, node carries `embedding=None`), log a WARN. Mirrors AC-13 in E-3.

### Repository Surface

Added to `packages/core/src/agentic_kg/knowledge_graph/repository.py`:

| Method | Purpose |
|---|---|
| `create_or_merge_method(...)` | Embedding-dedup'd create, returns `(Method, created: bool)` |
| `create_method(method, generate_embedding=True)` | Direct create (no dedup); raises `DuplicateError` on id collision |
| `get_method(method_id)` | Fetch by ID; raises `NotFoundError` |
| `get_method_by_name(name)` | Case-sensitive name lookup; deterministic tie-breaker (alphabetical id) |
| `update_method(method_id, **fields)` | Partial update |
| `delete_method(method_id)` | `DETACH DELETE` semantics — node + all inbound `APPLIES_METHOD` edges in one shot. No force flag (no canonical to protect). |
| `search_methods_by_embedding(embedding, top_k, min_score)` | Vector search |
| `link_paper_to_method(paper_doi, method_id)` | Idempotent `APPLIES_METHOD` edge + transactional `usage_count++` |
| `unlink_paper_from_method(paper_doi, method_id)` | Reverse with `usage_count--` |
| `get_papers_for_method(method_id, limit)` | Inverse traversal |

**One-line additions to existing helpers** (E-3 generalization absorbing E-4 with zero rework):

- `_NODE_LINK_RELATIONSHIPS["APPLIES_METHOD"] = ("Paper", "doi", "Method", "usage_count")`.

That's it. `_link_entity_to_node` and `_unlink_entity_from_node` handle the new relationship for free.

### API Surface

Added to `packages/api/src/agentic_kg_api/routers/methods.py` (new):

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/methods` | List methods, paginated, filters: `name=`, `method_type=` |
| `GET` | `/api/methods/{id}` | Detail with `usage_count` and paginated linked papers |
| `GET` | `/api/methods/{id}/papers` | Papers linked via APPLIES_METHOD |
| `GET` | `/api/methods/search?q=...` | Vector similarity search over method embeddings |
| `POST` | `/api/methods` | Create with dedup check (returns `created=true|false`). Accepts optional `threshold` field (`0.0 ≤ threshold ≤ 1.01`) to override the default 0.90 for that call. Operator escape valve for unwanted-merge scenarios: passing `threshold=1.01` effectively disables dedup. |
| `POST` | `/api/methods/{id}/link-paper` | Link a Paper to this Method |
| `DELETE` | `/api/methods/{id}` | Delete (DETACH DELETE; no force needed — no canonical protection) |

### CLI Commands

| Command | Description |
|---|---|
| `agentic-kg create-method --name <name> [--description <desc>] [--aliases a,b] [--method-type <type>] [--threshold N]` | Manual create with dedup. `--threshold 1.01` bypasses dedup for cases where an operator is sure the new Method should be distinct from existing near-matches. |
| `agentic-kg link-method --paper-doi <doi> --method-id <id>` | Link a Paper to a Method |

**No `load-methods` command** — there's no seed YAML.

### "Done Demo" — testcontainers integration test (the verify gate)

Per QA Q3 review: the eval set is dropped; the testcontainers integration test is the verify floor.

After schema initialization on a fresh testcontainers Neo4j instance:

1. Seed 5 synthetic Paper nodes via `repo.create_paper`.
2. For each Paper, call `repo.create_or_merge_method(name=...)` with a well-known method name ("fine-tuning", "contrastive learning", "knowledge distillation", "data augmentation", "few-shot learning") and confirm the first call creates and the second call on the same name merges.
3. Call `repo.link_paper_to_model` (wait — `repo.link_paper_to_method`) for each (paper, method) pair.
4. Call `repo.get_papers_for_method(method_id)` for each method and assert the linked Paper is returned.
5. Confirm `usage_count` increments per link.

This proves the v1 path-to-usefulness end-to-end with no operator-side hand-labeling required.

## Sample Implementation

```python
# packages/core/src/agentic_kg/knowledge_graph/repository.py (additions)

DEFAULT_METHOD_DEDUP_THRESHOLD = 0.90


# ONE-LINE absorption of APPLIES_METHOD into the E-3 generalization:
_NODE_LINK_RELATIONSHIPS = {
    "INVOLVES_CONCEPT": ("ProblemConcept", "id",  "ResearchConcept", "mention_count"),
    "DISCUSSES":        ("Paper",          "doi", "ResearchConcept", "paper_count"),
    "USES_MODEL":       ("Paper",          "doi", "Model",           "usage_count"),
    "APPLIES_METHOD":   ("Paper",          "doi", "Method",          "usage_count"),
}


def create_or_merge_method(
    self,
    name: str,
    description: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    method_type: Optional[str] = None,
    threshold: Optional[float] = None,
    embedding: Optional[list[float]] = None,
) -> tuple[Method, bool]:
    """Embedding-based create-or-merge. Pure open-set — no canonical
    protection. Mirrors create_or_merge_research_concept shape."""
    threshold = (
        threshold if threshold is not None else self.DEFAULT_METHOD_DEDUP_THRESHOLD
    )

    if embedding is None:
        try:
            from agentic_kg.knowledge_graph.embeddings import generate_method_embedding
            embedding = generate_method_embedding(name, description)
        except Exception as e:
            logger.warning(
                f"Embedding failed for method '{name}': {e}. "
                "Falling back to create-without-embedding (dedup skipped)."
            )

    if embedding is not None:
        candidates = self.search_methods_by_embedding(
            embedding=embedding, top_k=5, min_score=threshold,
        )
        if candidates:
            best, score = candidates[0]
            logger.info(f"Method dedup: '{name}' -> '{best.name}' (score={score:.3f})")
            merged_aliases = sorted(
                set(best.aliases)
                | set(aliases or [])
                | ({name} if name != best.name else set())
            )
            self.update_method(
                best.id,
                aliases=merged_aliases,
                description=best.description or description,
                method_type=best.method_type or method_type,
            )
            return self.get_method(best.id), False

    method = Method(
        name=name,
        description=description,
        aliases=list(aliases or []),
        method_type=method_type,
        embedding=embedding,
    )
    self.create_method(method, generate_embedding=False)
    return method, True


def link_paper_to_method(self, paper_doi: str, method_id: str) -> bool:
    """APPLIES_METHOD via the generalized helper — no code duplication."""
    return self._link_entity_to_node(
        entity_id=paper_doi, target_id=method_id, relationship="APPLIES_METHOD",
    )
```

## Edge Cases & Error Handling

### Two Methods collide on embedding from independent operator calls
- **Scenario:** Operator A creates "data augmentation" via API. Operator B creates "data augmentation methods" 5 minutes later. Embedding cosine ≥ 0.90.
- **Behavior:** Standard alias merge. The first node's name wins; the second incoming name lands in aliases. `created=False` returned to operator B.
- **Test:** Integration test creates two near-identical names back-to-back; asserts second merges into first.

### Update conflicts under high concurrency
- **Scenario:** Two operators update the same Method's `description` simultaneously.
- **Behavior:** Last-write-wins (no optimistic locking in v1). Documented limitation; matches E-2/E-3.
- **Test:** Not in v1; flag for future hardening if it materializes.

### `delete_method` on a Method with inbound `APPLIES_METHOD` edges
- **Scenario:** Operator deletes a Method that 10 Papers have linked to.
- **Behavior:** `DETACH DELETE` removes the node and all 10 edges in one shot. Re-extraction via E-8 V2 would recreate edges if needed. Mirrors E-3's rebuild-over-migrate ethos. **No audit log** written.
- **Test:** Integration test creates Method, links 2 Papers, deletes the Method, asserts the Method is gone and the Papers survive with their other relationships intact.

### Embedding service unavailable
- **Scenario:** OpenAI embedding endpoint times out during `create_or_merge_method`.
- **Behavior:** Fallback to **create-without-embedding**, log WARN, dedup skipped for that call. The new node carries `embedding=None`. Subsequent calls against a healthy embedding service may create near-duplicates; documented limitation, recoverable via manual `DELETE + re-create`. Same shape as E-3 AC-13.
- **Test:** Unit test mocks embedding failure; asserts node created without embedding + WARN logged.

### Empty or whitespace-only name
- **Scenario:** Operator POSTs `{"name": "  "}` to `/api/methods`.
- **Behavior:** Pydantic `min_length=2` validation rejects at the schema layer. FastAPI returns 422 with the field-level error message.
- **Test:** API test confirms 422.

### `get_method_by_name` ambiguity
- **Scenario:** Two Method nodes happen to share an exact `name` value (possible if embedding dedup was skipped during an outage).
- **Behavior:** Deterministic tie-breaker — alphabetically-first `id` wins. Logged. No `is_canonical` to use as a tiebreaker (one less branch than E-3's `get_model_by_name`).
- **Test:** Integration test creates two same-named nodes via direct `create_method` (bypassing dedup), confirms `get_method_by_name` returns deterministic result.

### Alias accumulation hits the Pydantic `max_length=20` cap
- **Scenario:** A heavily-used Method node has been the merge target for 20+ near-duplicate `create_or_merge_method` calls. Its alias list is full. A 21st call would push the merged set past the cap.
- **Behavior:** `update_method(aliases=...)` triggers Pydantic `ValidationError` on the alias-list overflow when re-validating the entity. The dedup-merge step fails; the operator's `create_or_merge_method` call raises (failure is loud, not silent). The error message will be Pydantic's generic "list too long" — not the most operator-friendly framing. **Workaround:** the operator manually calls `update_method(method_id, aliases=trimmed_list)` to drop the least-useful alias(es) before retrying the merge. There is no automatic LRU eviction in v1. Tech Lead Q3 review accepted this trade-off as the smallest path; revisit if it materializes as a real operator pain point.
- **Test:** Unit test on the model — a 21-alias dict at construction time raises `ValidationError`; documents the cap. No integration test required because the failure surfaces at the model layer.

## Acceptance Criteria

### AC-1: Method entity model
- **Given** `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`
- **When** imported
- **Then** the `Method` class exists with the field shape described in *Data Model* (`id`, `name`, `description`, `aliases`, `method_type`, `embedding`, `usage_count`, `created_at`, `updated_at`) and a working `to_neo4j_properties()` method.
- **And** there is no `is_canonical` field.

### AC-2: Schema additions
- **Given** the schema manager
- **When** the schema is initialized against a fresh Neo4j
- **Then** the `method_id_unique` constraint, `method_name_idx` index, and `method_embedding_idx` vector index (1536 cosine) exist.
- **And** the schema version is bumped to v6 (E-3 left it at v5).

### AC-3: Embedding-based dedup (open-set, no canonical protection)
- **Given** a non-empty Method graph
- **When** `create_or_merge_method(name="contrastive learning v2")` runs against an existing `Method(name="contrastive learning")` whose embedding scores ≥ 0.90
- **Then** the function returns the existing node, `created=False`, the existing name is preserved, and `"contrastive learning v2"` is in its aliases.
- **And** if `best.description` is None and the incoming call has a description, the merged node adopts the incoming description.
- **And** `method_type` is filled from the incoming call only when the existing value is None (existing wins on conflict).
- **And** alias dedup uses **exact string matching** (Tech Lead Q1 review). Case-variant inputs ("Contrastive Learning" + "contrastive learning") accumulate in the alias list as distinct entries; normalization is not applied. Matches E-2 and E-3 behavior.

### AC-4: New node when no candidate above threshold
- **Given** an empty Method graph
- **When** `create_or_merge_method(name="completely unrelated technique")` runs
- **Then** a new node is created with `created=True`.

### AC-5: APPLIES_METHOD edge + transactional usage_count
- **Given** a Paper node and a Method node both exist
- **When** `link_paper_to_method(paper_doi, method_id)` runs
- **Then** an `APPLIES_METHOD` edge exists from Paper to Method and the method's `usage_count` is incremented by 1.
- **And** calling the same link twice does NOT double-increment `usage_count` (idempotent MERGE).
- **And** `unlink_paper_from_method` removes the edge and decrements `usage_count` (clamped at 0).

### AC-6: Repository CRUD surface
- **Given** the implementation merged
- **When** `get_method`, `get_method_by_name`, `update_method`, `delete_method`, `search_methods_by_embedding`, `link_paper_to_method`, `unlink_paper_from_method`, `get_papers_for_method` are exercised
- **Then** each returns the documented shape and raises `NotFoundError` where appropriate.
- **And** `delete_method` uses DETACH DELETE semantics — no force flag required (there's no canonical to protect).

### AC-7: API surface
- **Given** the running FastAPI app
- **When** the documented `/api/methods` endpoints are exercised
- **Then** each returns the expected JSON shape and HTTP status code.
- **And** `POST /api/methods` returns `created: true|false` based on the dedup result.
- **And** `POST /api/methods` accepts an optional `threshold` field in the request body. Passing `threshold=1.01` makes the dedup search return no matches (per the underlying cosine bound), forcing a new node and returning `created: true`. This is the API-side operator escape valve (QA Q2 review).

### AC-8: CLI commands
- **Given** the installed CLI
- **When** `agentic-kg create-method --name ...` and `agentic-kg link-method --paper-doi ... --method-id ...` are invoked
- **Then** each prints a confirming summary and exits 0 on success.
- **And** there is **no** `load-methods` command (no seed YAML in v1).
- **And** `create-method` accepts a `--threshold N` flag that's forwarded to `create_or_merge_method`. `--threshold 1.01` lets operators bypass dedup when they want a Method that would otherwise be merged into a near-duplicate (QA Q2 review).

### AC-9: Testcontainers integration test (the verify gate)
- **Given** a fresh testcontainers Neo4j instance with the schema initialized
- **When** the integration test seeds 5 synthetic Paper nodes, calls `create_or_merge_method` for 5 well-known Method names (creates first, merges second-call), and `link_paper_to_method` for each (paper, method) pair
- **Then** `get_papers_for_method(method_id)` returns each linked paper for the corresponding method.
- **And** each method's `usage_count` equals the number of papers linked to it.
- **And** the test runs in CI under `pytest.mark.integration` against testcontainers Neo4j (no human-in-the-loop demo at verify).

### AC-10: Existing functionality untouched
- **Given** the existing test suite
- **When** E-4 is merged
- **Then** all existing tests in `packages/core/tests/` and `packages/api/tests/` continue to pass with zero modifications.
- **And** E-3 Model behavior is unchanged (Model continues to use `_NODE_LINK_RELATIONSHIPS` after the new entry is added).
- **And** E-2 ResearchConcept behavior is unchanged.

### AC-11: Dedup smoke test (threshold-regression sentinel)
- **Given** a fresh testcontainers Neo4j instance with the schema initialized and one Method `Method(name="fine-tuning")` created via `create_or_merge_method`
- **When** `create_or_merge_method(name="Fine Tuning")` runs against it with the default `DEFAULT_METHOD_DEDUP_THRESHOLD = 0.90`
- **Then** the second call returns `created=False` — the case-variant near-duplicate merged into the existing node.
- **Rationale:** This is **not an eval set**. It's a single sentinel that fails loudly if anyone bumps the threshold to an absurd value, inverts the dedup comparator, or otherwise breaks dedup such that obvious near-duplicates stop merging. Per QA Q4 review, this is the smallest-cost regression guard against the highest-impact bug class. Threshold precision/recall validation is deferred to E-8 V2.

### AC-12: Embedding service failure is tolerated
- **Given** the embedding service returns an error
- **When** `create_or_merge_method` is called
- **Then** the function creates a new node with `embedding=None` and logs a WARN — the call does not raise.
- **And** dedup is skipped for that call.

## Technical Notes

- **Affected files:**
  - Create: `api/routers/methods.py`, `tests/knowledge_graph/test_method_repository.py`, `tests/knowledge_graph/test_method_entity.py`, `tests/knowledge_graph/test_schema_method.py`, `tests/knowledge_graph/test_method_embedding.py`, `tests/integration/test_e4_done_demo.py`, `api/tests/test_methods.py`, `tests/test_cli_methods.py`
  - Modify: `knowledge_graph/models/entities.py` (add `Method`), `knowledge_graph/models/__init__.py` (export), `knowledge_graph/repository.py` (add Method methods + one entry in `_NODE_LINK_RELATIONSHIPS`), `knowledge_graph/schema.py` (constraint + index + vector index; v5 → v6), `knowledge_graph/embeddings.py` (add `generate_method_embedding`), `api/main.py` (mount router), `api/schemas.py` (Method request/response shapes), `cli.py` (add two subcommands)
  - Touch: none in `problem_extractor.py`, `kg_integration_v2.py`, agents, or extractor modules.
- **Reuse:** Existing `_link_entity_to_node` and `_unlink_entity_from_node` (E-3 generalized them). Existing `search_research_concepts_by_embedding` is the template for `search_methods_by_embedding`. Existing `create_or_merge_research_concept` is the closer structural template than `create_or_merge_model` (no canonical branches).
- **Dedup threshold:** `DEFAULT_METHOD_DEDUP_THRESHOLD = 0.90`. Matches `DEFAULT_CONCEPT_DEDUP_THRESHOLD`. Threshold changes can be validated via ad-hoc spot-checks; no eval-set gate ships in v1.
- **Vector index dimensions:** 1536 (text-embedding-3-small), consistent with E-1, E-2, E-3.
- **No new top-level dependencies.**

## Dependencies

- **E-3 (verified)** — provides the `_NODE_LINK_RELATIONSHIPS` map and the generalized `_link_entity_to_node` / `_unlink_entity_from_node` helpers. E-4 absorbs into them with one line.
- **Soft:** the existing embedding service (`generate_research_concept_embedding`) is the structural template for a sibling `generate_method_embedding`.

## Open Questions

- **`method_type` enums.** Free-form in v1; promote to closed enums if data shows clear low-cardinality clustering. Decision deferred to implementation review.
- **`EXTENDS_METHOD` lineage edges.** Gap analysis mentions them. Same disposition as Model's `VARIANT_OF`: deferred to v2.
- **Dedup eval-set when E-8 V2 lands.** When the extractor starts populating Methods at scale, a small eval-set may become useful to gate `DEFAULT_METHOD_DEDUP_THRESHOLD` against regression. Tracked as a follow-up; not blocking E-4 v1.

## Review Record

Dual-persona review completed 2026-06-09. Four questions, all resolved:

**Tech Lead review:**
- **Q1 — Alias dedup semantics.** Decision: exact string matching only. Case-variant inputs ("Contrastive Learning" + "contrastive learning") accumulate as distinct alias entries. Matches E-2 and E-3 behavior; normalization would introduce inconsistency across entity types. Locked in AC-3.
- **Q3 — Alias cap (max_length=20) failure mode.** Decision: accept the cap. When a heavily-used Method node hits the alias-list cap, subsequent merge calls raise Pydantic `ValidationError`. Operator workaround is `update_method(aliases=trimmed_list)`. No automatic LRU eviction in v1. Locked in *Edge Cases*.

**QA review:**
- **Q2 — Operator escape valve for unwanted merges.** Decision: expose `threshold` on the API (`POST /api/methods`) and CLI (`create-method --threshold N`). Setting `threshold=1.01` forces the dedup search to return no matches and creates a new node. Pre-existing in `create_or_merge_method`'s repo signature; spec adds the CLI + API surface. Locked in AC-7, AC-8, and the API/CLI tables.
- **Q4 — Threshold regression guard.** Decision: add a single dedup smoke test (AC-11). Not an eval set — a sentinel that creates "fine-tuning" + "Fine Tuning" at default threshold and asserts they merge. Catches threshold-inversion bugs and the "threshold accidentally set absurdly high" class without paying for a full eval-set burden. Threshold precision/recall validation remains deferred to E-8 V2.
