# Feature: Extraction Prompt Expansion V2 (E-8 V2: Models + Methods + Citation Wiring)

**Status:** SPECIFIED
**Date:** 2026-06-14
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-8 (V2 follow-up)
**Depends On:** E-3 (Model entities, VERIFIED), E-4 (Method entities, VERIFIED), E-5 (Citation graph helper, VERIFIED), E-6 (Entity descriptions, VERIFIED), E-8 V1 (Topic + Concept extractors, VERIFIED)
**Decoupled From:** E-7 (cross-entity normalization), L-1 (low-cost SLM client)

## Problem

E-3 (Model), E-4 (Method), and E-5 (Citation graph) shipped first-class entities + repository CRUD + APIs + CLI commands — but **nothing in the ingestion pipeline populates them**. Every `Model` and `Method` node in the graph today comes from `agentic-kg create-model` / `create-method` operator calls or from the canonical `seed_models.yml`. Every `Paper -CITES-> Paper` edge comes from manual `agentic-kg citation-graph` invocations or test fixtures.

Three concrete gaps:

1. **E-3 / E-4 not populated from papers.** `extract_all_entities` orchestrates `problem_extractor`, `topic_extractor`, `concept_extractor` in parallel. No `model_extractor` or `method_extractor` exists. When the ingestion job runs over real arXiv papers, every Model and Method extraction is silently skipped.
2. **E-5 `populate_citations` is a standalone helper.** It ships fully tested, but `PaperImporter.import_paper` (the entry point for both `ingest` CLI and Cloud Run Job) never calls it. New Paper nodes land in the graph with zero outbound CITES edges.
3. **E-6 punted the ingestion-path description-generation decision to V2.** The new `generate_description: bool = False` kwarg defaults False per E-6 AC-11 (cost-neutral for existing callers). V2 has to decide whether the new ingestion-path Model + Method creation calls flip it ON.

Each gap is small in isolation. Bundled together as V2, they close the loop on the entity-expansion arc (E-1 through E-6): papers ingested through the standard path produce Topic + Concept + Model + Method nodes with the right edges, plus citation graph enrichment, in one orchestrated commit.

## Goals

- **Model + Method extractors as parallel siblings of ConceptExtractor.** Same shape (paper-level single LLM call, paper title + selected sections, Pydantic envelope, confidence filter). Mirror ConceptExtractor's open-set design — no closed Model/Method taxonomy.
- **`extract_all_entities` grows to five awaitables** (problem + topic + concept + model + method) via two new named kwargs. `PaperExtractionResult` gains `models: list[ExtractedModel]` and `methods: list[ExtractedMethod]` fields.
- **`integrate_paper_entities` writes USES_MODEL and APPLIES_METHOD edges** by calling `repo.create_or_merge_model` and `repo.create_or_merge_method` per extraction (E-3 / E-4 dedup + canonical-protection runs automatically), then `repo.link_paper_to_model` / `link_paper_to_method`. The B3 problem→concept linker is NOT extended to Model/Method in V2 (out of scope).
- **`PaperImporter.import_paper` calls `populate_citations`** after the Paper node is persisted. Default-on via a new `populate_citations: bool = True` kwarg. Applies to both create and `update_existing=True` paths. Test code passes `populate_citations=False` to skip the S2 call.
- **Description-generation stays OFF for the ingestion path.** Both new extractor → integrator calls (`create_or_merge_model`, `create_or_merge_method`) pass the sync method with `generate_description=False` per E-6 AC-11. CLI commands (`create-model`, `create-method`) keep their `generate_description=True` default from E-6.
- **No regression in extraction quality or wall-clock** for problem / topic / concept extraction. V2 adds two extractors to the existing `asyncio.gather`; total per-paper wall-clock increases by `max(model_latency, method_latency)`, not the sum.
- **Eval-set extension** (mirrors E-8 V1 AC-12): the existing 5-paper fixture set (`tests/extraction/fixtures/e8_eval/`) gains gold labels for expected Models + Methods per paper. Precision floors at verify time gate quality.

## Non-Goals

- **B3-style problem↔Model or problem↔Method linker.** V1 added `INVOLVES_CONCEPT` via the B3 surface-form heuristic. V2 does NOT add an `INVOLVES_MODEL` or `INVOLVES_METHOD` mirror — the relationships are paper-scope (`USES_MODEL`, `APPLIES_METHOD`), not problem-scope. Deferred.
- **Auto-canonical Model marking.** `is_canonical=True` remains reserved for `seed_models.yml`. The extractor never sets it, even when the LLM identifies "this is a landmark model"; that's a curation call, not an extraction call.
- **Description generation in the ingestion path.** Per Q1 decision: ~5 LLM calls per paper (problem + topic + concept + model + method) is the cost ceiling. Description-gen at create time is operator-driven via the existing E-6 CLI flags. When L-1 lands, this decision can be revisited.
- **Cross-paper Model/Method canonicalization.** E-7's scope. Embedding-dedup already converges per-paper extractions ("BERT" → existing BERT node); a deliberate cross-paper normalization pass (e.g., "BERT-base" → "BERT") is E-7.
- **Closed-set Model taxonomy.** TopicExtractor uses a closed `Literal` over the seed taxonomy. We do NOT mirror that for Model. The seed YAML is closed-set-like (19 canonical entries) but new models ship weekly; an open-set extractor + embedding-dedup is the right shape.
- **Re-ingestion path changes.** E-8 V1 AC-13's purge-then-rewrite already handles `Paper.extraction_incomplete=true` and clears extraction footprint. V2 inherits the path; the existing audit query just additionally surfaces missing Model / Method gaps once the extractors land.
- **New CLI subcommands.** Operators already have `create-model` / `create-method` / `citation-graph`. V2 changes the ingestion path, not the operator surface.

## User Stories

- **As a researcher**, I want every paper I ingest to auto-populate the models it uses and methods it applies, so I can ask "which papers use BERT?" or "which papers apply contrastive learning?" without manual curation.
- **As a researcher**, I want a paper's citation neighborhood populated at ingest time so the citation graph is non-empty on day one of ingestion at scale.
- **As a developer**, I want Model / Method extraction failures to be isolated — a bad model prompt should not lose me the problems, topics, or concepts from the same paper.
- **As a Cloud Run Job operator**, I want to disable `populate_citations` during a bulk re-ingestion when Semantic Scholar is throttling my account.
- **As a test author**, I want `PaperImporter.import_paper(populate_citations=False)` so unit tests don't have to mock the S2 client.

## Design Approach

### Architecture (Route B continued — five-way parallel)

```
paper.content ──► ProblemExtractor ────► problems   ┐
              ├─► TopicExtractor   ────► topics      │
              ├─► ConceptExtractor ────► concepts    ├─► asyncio.gather
              ├─► ModelExtractor   ────► models      │   (V2 adds 2)
              └─► MethodExtractor  ────► methods     ┘
                                   │
                                   ▼
                          PaperExtractionResult
                                   │
                                   ▼
              kg_integration_v2.py (existing writers + 2 new)
                                   │
               ┌───────────────┬───┴───┬───────────────┐
               ▼               ▼       ▼               ▼
          Topic edges    Concept    Model edges    Method edges
          (existing)     (existing) USES_MODEL     APPLIES_METHOD
                                    (V2 new)       (V2 new)
                         │
                         ▼
                    B3 heuristic
                    (unchanged, V1 path)


PaperImporter.import_paper(populate_citations=True):
   ──► repository.create_paper / update_paper  (existing)
   ──► [author linking, existing]
   ──► await populate_citations(repo, s2_client, paper_doi)  (V2 wiring)
   ──► return ImportResult with citation_population attached
```

- **Two independent new async LLM calls** per paper, joined to the existing three via `asyncio.gather`. One failure never blocks the others — each is logged and the pipeline commits whatever succeeded (V1's `_run` failure-isolation pattern carries forward unchanged).
- **Problem / topic / concept extraction is untouched.** No changes to `problem_extractor.py`, `topic_extractor.py`, `concept_extractor.py`, or their prompts.
- **Model and method extraction is paper-level**, sections drawn from the same abstract + intro + methodology + experiments slice that V1's ConceptExtractor uses (with the empty-section short-circuit preserved).
- **Citation enrichment runs in the importer, NOT the integrator.** Citations are paper-data (S2 references), not extraction-data (LLM output). They belong next to the Paper node create call, before the extraction pipeline kicks in.

### New modules and where they live

| File | Purpose |
|---|---|
| `packages/core/src/agentic_kg/extraction/schemas.py` (additions) | `ExtractedModel`, `ExtractedMethod` Pydantic classes |
| `packages/core/src/agentic_kg/extraction/prompts/templates.py` (additions) | `MODEL_SYSTEM_PROMPT_V1`, `MODEL_USER_PROMPT_TEMPLATE_V1`, `METHOD_SYSTEM_PROMPT_V1`, `METHOD_USER_PROMPT_TEMPLATE_V1` |
| `packages/core/src/agentic_kg/extraction/model_extractor.py` (new) | `ModelExtractor` — mirror of `ConceptExtractor` |
| `packages/core/src/agentic_kg/extraction/method_extractor.py` (new) | `MethodExtractor` — mirror of `ConceptExtractor` |
| `packages/core/src/agentic_kg/extraction/pipeline.py` (additions) | `extract_all_entities` gains `model_call`, `method_call` kwargs; `PaperExtractionResult` gains `models`, `methods` fields |
| `packages/core/src/agentic_kg/extraction/kg_integration_v2.py` (additions) | Two new writers (USES_MODEL, APPLIES_METHOD); `MIN_MODEL_CONFIDENCE`, `MIN_METHOD_CONFIDENCE` constants; `EntityIntegrationResult` gains `models_linked`, `methods_linked` counters |
| `packages/core/src/agentic_kg/data_acquisition/importer.py` (modifications) | `PaperImporter.import_paper` gains `populate_citations: bool = True` and `s2_client` kwargs; calls `populate_citations` after paper persist on both create and update paths |
| `packages/core/src/agentic_kg/ingestion.py` (modifications) | `ingest_papers` orchestrator passes `populate_citations` through to the importer (default True; CLI can flip off via flag — see CLI section below) |
| `packages/core/tests/extraction/fixtures/e8_eval/paper_*.gold.yml` (extensions) | Each gold file gains `expected_models` + `expected_methods` lists; `SELECTION.md` updated |

### Schemas

```python
# extraction/schemas.py additions

class ExtractedModel(BaseModel):
    """Open-set Model extraction — dedup at create_or_merge_model.

    The extractor never marks is_canonical=True; that's reserved for the
    seed YAML. Embedding-dedup at write time routes "BERT" / "bert-base"
    to the existing canonical node automatically (E-3 contract).
    """
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    architecture: Optional[str] = Field(default=None, max_length=40)
    model_type: Optional[str] = Field(default=None, max_length=40)
    year_introduced: Optional[int] = Field(default=None, ge=1950, le=2100)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)  # grounding


class ExtractedMethod(BaseModel):
    """Open-set Method extraction — mirror of ExtractedModel."""
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    method_type: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)
```

### Prompt design

Templates follow the existing `SYSTEM_PROMPT_V1` / `USER_PROMPT_TEMPLATE_V1` naming convention so the next author can duplicate-rename for any future entity type. Both new prompts are paper-level (single call per paper), use the same dispatcher, and stay below 2K tokens for the user message.

**Model prompt structure:**
- System prompt: "Extract the specific ML models or neural architectures the paper introduces, uses, or evaluates against. Include the model's well-known short forms as aliases. A Model has weights, a name, and an architecture family. Do NOT extract generic techniques like 'transformers' or 'CNNs' as Models — those are Methods or Concepts. Ground each Model in quoted text."
- User prompt: injects paper title + abstract + intro + methodology + experiments. Open-set: `create_or_merge_model` on write handles dedup + canonical routing.
- Disambiguation hint: "Examples of Models: BERT, GPT-2, ResNet-50, T5, CLIP. NOT examples of Models: transformer architecture, attention mechanism, fine-tuning."

**Method prompt structure:**
- System prompt: "Extract the methods or techniques the paper applies. A Method is a named recipe or procedure: fine-tuning, contrastive learning, knowledge distillation. A Method doesn't have weights — it's something you do, often to a Model. Do NOT extract overly general terms like 'training' or 'evaluation' as Methods. Ground each Method in quoted text."
- User prompt: injects paper title + abstract + intro + methodology + experiments. Open-set.
- Disambiguation hint: "Examples of Methods: contrastive learning, instruction tuning, LoRA, RLHF, knowledge distillation. NOT examples of Methods: training a model, running experiments."

### Empty-section short-circuit (inherited from V1)

Each extractor preflights: if the formatted `sections_text` is empty after slicing the paper, return `[]` immediately without calling the LLM. This preserves V1's "no abstract or intro" behavior and keeps LLM cost predictable for workshop papers with incomplete sectioning.

### Integration writers

`integrate_paper_entities` gains two new sections — symmetric with the existing concept writer:

```python
# Model integration
for m in extraction_result.models:
    if m.confidence < min_model_confidence:
        continue
    model, _ = repo.create_or_merge_model(
        name=m.name,
        description=m.description,                  # None unless LLM emitted one
        aliases=list(m.aliases),
        architecture=m.architecture,
        model_type=m.model_type,
        year_introduced=m.year_introduced,
        # generate_description omitted → defaults to False (E-6 AC-11; Q1)
    )
    repo.link_paper_to_model(paper_doi=paper_doi, model_id=model.id)
    result.models_linked += 1

# Method integration — same shape
for m in extraction_result.methods:
    if m.confidence < min_method_confidence:
        continue
    method, _ = repo.create_or_merge_method(
        name=m.name,
        description=m.description,
        aliases=list(m.aliases),
        method_type=m.method_type,
    )
    repo.link_paper_to_method(paper_doi=paper_doi, method_id=method.id)
    result.methods_linked += 1
```

The B3 problem→concept linker block (existing V1) runs unchanged after these two new sections. The two new writers respect the existing `MIN_*_CONFIDENCE` pattern.

### Citation wiring

`PaperImporter.import_paper` adds two kwargs and one call:

```python
async def import_paper(
    self,
    identifier: str,
    sources: list[str] | None = None,
    update_existing: bool = False,
    create_authors: bool = True,
    populate_citations: bool = True,         # NEW (V2)
    s2_client: Optional["SemanticScholarClient"] = None,  # NEW (V2) — for tests
) -> ImportResult:
    # ... existing fetch + persist logic unchanged ...

    # AFTER the Paper node is created or updated, before returning:
    if populate_citations and result.paper:
        from agentic_kg.knowledge_graph.citation_graph import (
            populate_citations as _populate,
        )
        try:
            citation_result = await _populate(
                repo=self.repository,
                s2_client=s2_client or self._get_s2_client(),
                paper_doi=result.paper.doi,
            )
            result.citation_population = citation_result  # attached for caller audit
        except Exception:
            # populate_citations never raises, but defensive guard absorbs any
            # future refactor that might. Citation enrichment is best-effort.
            logger.exception(
                "populate_citations unexpectedly raised for %s", result.paper.doi,
            )

    return result
```

`s2_client` defaults to a lazily-constructed singleton (via a new `_get_s2_client` helper on `PaperImporter`). Unit tests construct `PaperImporter` with `populate_citations=False` OR with a mock `s2_client` for end-to-end coverage.

`ImportResult` gains a `citation_population: Optional[CitationPopulationResult]` field. CLI / API callers can surface stats (e.g., "imported paper X; 12 references resolved + 3 stubs created").

### CLI surface

No new subcommands. Two existing surfaces gain pass-through control:

- `agentic-kg ingest`: gains `--no-populate-citations` flag (default OFF — citations populate). Same pattern as E-6's `--no-generate-description`.
- `PaperImporter` and `ingest_papers` thread the flag through; the Cloud Run Job's `job_runner.py` reads it from an env var (`POPULATE_CITATIONS=false` to disable for a bulk-import).

### Failure isolation

V1's `_run` orchestrator wrapper catches `BaseException`, records an `ExtractionFailure`, and lets sibling extractors proceed. V2 inherits this unchanged. A Model extractor crash leaves problems / topics / concepts / methods committed; the Paper node is marked `extraction_incomplete=true` and a Cypher audit query surfaces it for re-ingestion. The `ExtractionFailure.extractor` field gains two new valid values: `"model"`, `"method"`.

### Eval set extension

Existing fixtures in `packages/core/tests/extraction/fixtures/e8_eval/` get extended (no new fixtures required for V2's verify floor):

```yaml
# tests/extraction/fixtures/e8_eval/paper_<id>.gold.yml (additions)
expected_models:
  - canonical: "BERT"
    acceptable_aliases: ["bert-base", "bert-large", "BERT-base-uncased"]
  - canonical: "GPT-2"
    acceptable_aliases: ["gpt2"]
expected_methods:
  - canonical: "fine-tuning"
    acceptable_aliases: ["finetuning", "supervised fine-tuning"]
  - canonical: "contrastive learning"
    acceptable_aliases: ["contrastive pretraining"]
```

Verify-time gates (same dual-precision + recall-tripwire shape as V1 AC-12):
- **Model precision** = (correct Model predictions) / (total Model predictions). Average across 5 papers ≥ 0.70 AND no paper below 0.50.
- **Method precision** = (correct Method predictions) / (total Method predictions). Average across 5 papers ≥ 0.65 AND no paper below 0.45.
- **Combined recall tripwire** = (gold Models + gold Methods matched by some prediction) / (gold Models + gold Methods total). Average ≥ 0.45. No per-paper floor.

The Model/Method floors sit slightly below the V1 Concept floors (V1: precision avg ≥ 0.70 / per-paper ≥ 0.50, recall avg ≥ 0.50) because Model/Method extraction is harder: model names overlap with framework names ("PyTorch" is not a Model; "OpenAI" is not a Model), and Methods overlap with Concepts ("attention mechanism" → Concept vs. "scaled dot-product attention" → arguably a Method specialization).

Selection rationale (`SELECTION.md`) gets a short append documenting why each of the 5 papers is labelable for Models + Methods, and any caveats. The "review by someone other than the spec author" requirement from V1 AC-12 applies to the additions.

## Sample Implementation

```python
# === packages/core/src/agentic_kg/extraction/schemas.py (additions) ===

class ExtractedModel(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    architecture: Optional[str] = Field(default=None, max_length=40)
    model_type: Optional[str] = Field(default=None, max_length=40)
    year_introduced: Optional[int] = Field(default=None, ge=1950, le=2100)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)


class ExtractedMethod(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    method_type: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)


# === packages/core/src/agentic_kg/extraction/model_extractor.py (new) ===

class ModelExtractor:
    """Paper-level model extractor. Mirror of ConceptExtractor."""

    def __init__(
        self,
        client: BaseLLMClient,
        min_confidence: float = MIN_MODEL_CONFIDENCE,
        max_models: int = 20,
    ):
        self.client = client
        self.min_confidence = min_confidence
        self._envelope = create_model(
            "ExtractedModelEnvelope",
            models=(
                list[ExtractedModel],
                PField(default_factory=list, max_length=max_models),
            ),
        )

    async def extract(
        self, paper_title: str, sections_text: str,
    ) -> list[ExtractedModel]:
        if not sections_text.strip():
            return []  # empty-section short-circuit — no LLM call
        try:
            response = await self.client.extract(
                prompt=MODEL_USER_PROMPT_TEMPLATE_V1.format(
                    paper_title=paper_title, section_text=sections_text,
                ),
                response_model=self._envelope,
                system_prompt=MODEL_SYSTEM_PROMPT_V1,
            )
        except LLMError as e:
            logger.warning("Model extraction failed: %s", e)
            return []
        return [
            m for m in response.content.models
            if m.confidence >= self.min_confidence
        ]


# === packages/core/src/agentic_kg/extraction/pipeline.py (additions) ===

@dataclass
class PaperExtractionResult:
    problems: list[ExtractedProblem]
    topics:   list[_ExtractedTopicAssignmentBase]
    concepts: list[ExtractedResearchConcept]
    models:   list[ExtractedModel] = field(default_factory=list)   # NEW (V2)
    methods:  list[ExtractedMethod] = field(default_factory=list)  # NEW (V2)
    failures: list[ExtractionFailure] = field(default_factory=list)


async def extract_all_entities(
    *,
    problem_call: Awaitable[list],
    topic_call:   Awaitable[list],
    concept_call: Awaitable[list],
    model_call:   Awaitable[list],         # NEW (V2)
    method_call:  Awaitable[list],         # NEW (V2)
    paper_doi: Optional[str] = None,
) -> PaperExtractionResult:
    results = await asyncio.gather(
        _run("problem", problem_call),
        _run("topic",   topic_call),
        _run("concept", concept_call),
        _run("model",   model_call),
        _run("method",  method_call),
    )
    slots = {name: (payload, failure) for name, payload, failure in results}
    return PaperExtractionResult(
        problems=slots["problem"][0] or [],
        topics=  slots["topic"][0]   or [],
        concepts=slots["concept"][0] or [],
        models=  slots["model"][0]   or [],
        methods= slots["method"][0]  or [],
        failures=[f for _, f in slots.values() if f is not None],
    )


# === extraction/kg_integration_v2.py (additions) ===

MIN_MODEL_CONFIDENCE  = 0.7
MIN_METHOD_CONFIDENCE = 0.7


class EntityIntegrationResult(BaseModel):
    # ... existing fields ...
    models_linked: int = 0    # NEW (V2)
    methods_linked: int = 0   # NEW (V2)


def integrate_paper_entities(
    *,
    paper_doi: str,
    extraction_result: Any,
    mentions: list[Any],
    taxonomy_hash: str,
    repo: Neo4jRepository,
    min_topic_confidence: float = MIN_TOPIC_CONFIDENCE,
    min_concept_confidence: float = MIN_CONCEPT_CONFIDENCE,
    min_model_confidence: float = MIN_MODEL_CONFIDENCE,    # NEW (V2)
    min_method_confidence: float = MIN_METHOD_CONFIDENCE,  # NEW (V2)
) -> EntityIntegrationResult:
    # ... existing topic + concept + B3 writers unchanged ...

    # ---- Models → USES_MODEL ----
    for m in extraction_result.models:
        if m.confidence < min_model_confidence:
            continue
        model, _ = repo.create_or_merge_model(
            name=m.name, description=m.description,
            aliases=list(m.aliases),
            architecture=m.architecture, model_type=m.model_type,
            year_introduced=m.year_introduced,
        )  # generate_description omitted → False per Q1
        repo.link_paper_to_model(paper_doi=paper_doi, model_id=model.id)
        result.models_linked += 1

    # ---- Methods → APPLIES_METHOD ----
    for m in extraction_result.methods:
        if m.confidence < min_method_confidence:
            continue
        method, _ = repo.create_or_merge_method(
            name=m.name, description=m.description,
            aliases=list(m.aliases), method_type=m.method_type,
        )
        repo.link_paper_to_method(paper_doi=paper_doi, method_id=method.id)
        result.methods_linked += 1

    # ... existing extraction_incomplete + taxonomy_hash writers unchanged ...
    return result


# === data_acquisition/importer.py (modifications) ===

class PaperImporter:
    async def import_paper(
        self,
        identifier: str,
        sources: list[str] | None = None,
        update_existing: bool = False,
        create_authors: bool = True,
        populate_citations: bool = True,                       # NEW (V2)
        s2_client: Optional["SemanticScholarClient"] = None,   # NEW (V2)
    ) -> ImportResult:
        # ... existing fetch / persist / author-link logic unchanged ...

        if populate_citations and result.paper:
            try:
                from agentic_kg.knowledge_graph.citation_graph import (
                    populate_citations as _populate,
                )
                result.citation_population = await _populate(
                    repo=self.repository,
                    s2_client=s2_client or self._get_s2_client(),
                    paper_doi=result.paper.doi,
                )
            except Exception:
                # populate_citations never raises by contract, but absorb
                # defensively. Citation enrichment is best-effort.
                logger.exception(
                    "populate_citations unexpectedly raised for %s",
                    result.paper.doi,
                )
        return result
```

## Edge Cases & Error Handling

### LLM extracts a generic Model name ("transformer")
- **Scenario:** ModelExtractor returns `[{name: "transformer", confidence: 0.85, ...}]`. "transformer" is an architecture family, not a specific Model.
- **Behavior:** Pydantic accepts it (no domain validation at extraction time). At integrate-time, `create_or_merge_model` may dedup against an existing generic "transformer" node OR create a new node. The disambiguation hint in the system prompt is the primary defense; the eval-set precision gate is the verify-time tripwire.
- **Test:** Pure unit test mocks the LLM to return `[{"name": "transformer", ...}]`; assert the extractor accepts (no schema rejection), then assert that the integration writer calls `create_or_merge_model` with the name verbatim. Quality is the eval set's concern, not the extractor's.

### LLM extracts a Method that's really a Concept ("attention mechanism")
- **Scenario:** MethodExtractor pulls "attention mechanism" — a ResearchConcept, not a Method.
- **Behavior:** No cross-extractor deduplication in V2. Both `attention mechanism` (Concept) and `attention mechanism` (Method) may land in the graph as separate nodes. The disambiguation hint in the Method system prompt + the eval-set Method-precision gate are the defenses. Cross-entity normalization (E-7) addresses this systematically.
- **Test:** Unit test asserts no normalization happens. Document the known false-positive class in the implementation report.

### Model extractor returns 50 models for a survey paper
- **Scenario:** Survey paper covers BERT, GPT-1/2/3/4, T5, BART, CLIP, ResNet, ViT, etc.
- **Behavior:** Pydantic envelope caps at `max_length=20`. Above 20, `instructor` either truncates or retries with a schema-violation error → on retry exhaustion, the extractor returns `[]` and logs WARN. Per-paper recall takes a hit on survey papers; tracked as acceptable (recall floor in eval is 0.45).
- **Test:** Mock the LLM to return 25 models; assert the extractor returns `[]` after the schema-rejection retry path AND a WARN is logged identifying the over-cap.

### populate_citations called when paper has no S2 id
- **Scenario:** PaperImporter creates a Paper from arXiv-only metadata; S2 lookup at citation time finds nothing.
- **Behavior:** `populate_citations`'s existing contract: returns `CitationPopulationResult(skipped_no_s2_id=True)`, never raises. ImportResult.citation_population records the skip; no edges written.
- **Test:** Mock `s2_client.get_paper_by_doi` to return None; assert ImportResult.citation_population.skipped_no_s2_id is True.

### populate_citations during update_existing=True for a paper that already has CITES edges
- **Scenario:** Paper X has 50 CITES edges from a prior ingest. Operator re-imports with `update_existing=True`.
- **Behavior:** `populate_citations` runs again. Per E-5's idempotency contract, existing edges are preserved (the helper uses MERGE Cypher); new references appended; stale references (no longer in S2 response) are NOT removed. Counts in CitationPopulationResult reflect what the helper observed *this run*, not the cumulative graph state.
- **Test:** Pre-populate a Paper with two CITES edges. Re-import with `update_existing=True` and a mocked S2 response containing three references (two overlapping). Assert final count = 3 CITES edges; assert no edges were deleted.

### Test code accidentally hits S2
- **Scenario:** A new unit test of `PaperImporter.import_paper` doesn't override `populate_citations=False`.
- **Behavior:** Test fails if no S2 credentials are available; passes-but-slow if they are. To prevent the latter, the implementation phase audits all `PaperImporter.import_paper` test call sites and adds explicit `populate_citations=False` where appropriate.
- **Test:** Add a guard test: `test_default_populate_citations_is_true()` asserts the kwarg default and documents the audit obligation.

### One of model or method extractor fails (LLM 500, parse error)
- **Scenario:** ModelExtractor's inner `LLMError` catch fires → returns `[]` + WARN; MethodExtractor succeeds.
- **Behavior:** `_run("model", ...)` sees a clean empty result → no `ExtractionFailure` recorded. Other 4 extractors' results preserved. Paper integrated normally with `models=[]`, `methods=[...]`.
- **Test:** Patch ModelExtractor to raise `LLMError` internally; assert orchestrator returns with `failures=[]` (LLMError is known/expected) and `models=[]`, and a WARN was logged.

### Unexpected exception in a new extractor
- **Scenario:** MethodExtractor's `instructor` integration hits a post-validation `AttributeError` after retries exhaust.
- **Behavior:** V1's `_run` orchestrator wrapper catches `BaseException`, records `ExtractionFailure(extractor="method", exception_type="AttributeError", ...)`, returns `[]` for methods. Paper marked `extraction_incomplete=true`. Other extractors' results preserved.
- **Test:** Patch MethodExtractor to raise `AttributeError`. Assert: `PaperExtractionResult.is_partial is True`, `failures[0].extractor == "method"`, `methods == []`, problems/topics/concepts/models preserved.

### Description-generation default change in V3
- **Scenario:** Future operator changes ingestion-path default to `generate_description=True`.
- **Behavior:** Per E-6 AC-5, calling the SYNC `create_or_merge_model` with `generate_description=True` raises `NotImplementedError`. The integrator (which is sync today) would crash. Migration path: switch the V2 writer's two sync calls to `await repo.acreate_or_merge_model(...)` and `await repo.acreate_or_merge_method(...)`, and make `integrate_paper_entities` async. Documented as a known future work item, not a V2 task.

## Acceptance Criteria

### AC-1: New schemas
- **Given** `extraction/schemas.py`
- **When** imported
- **Then** `ExtractedModel` and `ExtractedMethod` classes exist with the field validators described above
- **And** `confidence` defaults to 0.8, `aliases` defaults to `[]` with `max_length=10`, `quoted_text` is required with `min_length=10`
- **And** `ExtractedModel.year_introduced` is bounded `ge=1950, le=2100`

### AC-2: ModelExtractor — happy path
- **Given** a `ModelExtractor` with a mocked LLM client that returns 2 models above threshold
- **When** `extract(paper_title=..., sections_text=...)` is awaited
- **Then** both models are returned verbatim
- **And** `llm_client.extract` was called once with the `MODEL_USER_PROMPT_TEMPLATE_V1` and `MODEL_SYSTEM_PROMPT_V1`
- **And** the envelope's `max_length=20` is respected

### AC-3: ModelExtractor — confidence filter + empty-section short-circuit
- **Given** a `ModelExtractor` with `min_confidence=0.7`
- **When** the LLM returns a mix of high + low confidence models, the low ones are filtered out
- **And** when `sections_text` is empty/whitespace, the extractor returns `[]` without calling the LLM
- **And** known `LLMError` raised inside `client.extract` is caught and returns `[]` with a WARN log

### AC-4: MethodExtractor — happy path + filter + short-circuit
- **Given** a `MethodExtractor` mirror of AC-2 + AC-3
- **When** the same scenarios run
- **Then** the same contract holds with `method_type` populated when the LLM emits it

### AC-5: extract_all_entities — 5-way parallel
- **Given** `extract_all_entities` with mocked problem/topic/concept/model/method awaitables
- **When** called
- **Then** all 5 are invoked concurrently via `asyncio.gather` (verified by mock call timing or order)
- **And** `PaperExtractionResult` carries `problems`, `topics`, `concepts`, `models`, `methods`, `failures`
- **And** existing V1 callers passing only `problem_call`, `topic_call`, `concept_call` get a clear `TypeError` for the new required kwargs (no silent regression)

### AC-6: Per-extractor degradation (V1 contract continues for new extractors)
- **Given** the 5-way orchestrator
- **When** ModelExtractor raises arbitrary `RuntimeError` (not `LLMError`)
- **Then** `failures` contains one entry with `extractor="model"` and `exception_type="RuntimeError"`
- **And** problems, topics, concepts, methods are preserved with their successful payloads
- **And** the Paper integration writer reads `failures` and sets `extraction_incomplete=true`

### AC-7: USES_MODEL edge written; model dedup-merged
- **Given** a paper with 2 `ExtractedModel`s and a live `Paper` node
- **When** `integrate_paper_entities(...)` runs
- **Then** `create_or_merge_model` is called per extraction with `generate_description=False` (default; not passed explicitly per Q1)
- **And** for each returned `(model, _)`, `link_paper_to_model(paper_doi, model.id)` is called
- **And** `EntityIntegrationResult.models_linked` reflects the count
- **And** an extraction below `MIN_MODEL_CONFIDENCE` is filtered out before the repo call

### AC-8: APPLIES_METHOD edge written; method dedup-merged
- **Given** a paper with 2 `ExtractedMethod`s and a live `Paper` node
- **When** `integrate_paper_entities(...)` runs
- **Then** `create_or_merge_method` is called per extraction with `generate_description=False`
- **And** `link_paper_to_method(paper_doi, method.id)` is called per returned method
- **And** `EntityIntegrationResult.methods_linked` reflects the count
- **And** below-threshold extractions are filtered

### AC-9: Canonical Model protection holds
- **Given** the canonical Model "BERT" exists in the graph from `seed_models.yml`
- **When** ModelExtractor emits `{name: "bert-base-uncased", confidence: 0.9, ...}` and the integrator runs
- **Then** `create_or_merge_model` matches the existing canonical "BERT" via embedding-dedup and merges (the extraction's aliases are appended; no new node is created)
- **And** `link_paper_to_model` links the paper to the canonical "BERT" id
- **And** the extractor never sets `is_canonical=True`
- **And** the existing canonical-protection rules from E-3 are unchanged

### AC-10: PaperImporter.import_paper — populate_citations default-on
- **Given** `PaperImporter.import_paper(identifier=...)` with no `populate_citations` kwarg
- **When** the paper is created (no existing Paper)
- **Then** `populate_citations` is called once on the new Paper's DOI
- **And** the returned `ImportResult.citation_population` carries the helper's `CitationPopulationResult`
- **And** when `populate_citations=False` is passed explicitly, no S2 call is made and `ImportResult.citation_population is None`

### AC-11: PaperImporter.import_paper — populate_citations on update path
- **Given** an existing Paper node X and `PaperImporter.import_paper(identifier=X.doi, update_existing=True)`
- **When** import completes successfully
- **Then** `populate_citations` is called against X.doi (citation re-population on update)
- **And** existing CITES edges are preserved (idempotent MERGE per E-5)

### AC-12: PaperImporter.import_paper — populate_citations exception absorbed
- **Given** `populate_citations` is monkey-patched to raise `RuntimeError("simulated")`
- **When** import_paper runs
- **Then** the import succeeds with the Paper persisted
- **And** an ERROR log is emitted including the paper DOI
- **And** `ImportResult.citation_population is None`
- **And** the test asserts no exception propagates to the caller

### AC-13: CLI `ingest` passes populate_citations through
- **Given** `agentic-kg ingest --no-populate-citations` is invoked
- **When** the underlying `ingest_papers` orchestrator calls `PaperImporter.import_paper`
- **Then** `populate_citations=False` is forwarded
- **And** the default (no flag) is `populate_citations=True`

### AC-14: Cloud Run Job env var forwarding
- **Given** the Cloud Run Job runs with `POPULATE_CITATIONS=false` set
- **When** `job_runner.py` constructs the ingest call
- **Then** `populate_citations=False` is passed to `ingest_papers`
- **And** when the env var is unset or `true` (any non-`false`), the default `populate_citations=True` is preserved

### AC-15: Existing extractors untouched
- **Given** the existing V1 extraction test suite
- **When** V2 is merged
- **Then** all existing tests in `packages/core/tests/extraction/` continue to pass with zero modifications
- **And** `problem_extractor.py`, `topic_extractor.py`, `concept_extractor.py` are unchanged
- **And** `b3_linker.py` is unchanged

### AC-16: Description-generation default unchanged for ingestion path
- **Given** the V2 integration writer calls `create_or_merge_model` and `create_or_merge_method`
- **When** those calls are inspected
- **Then** neither passes `generate_description=True` (the kwarg is omitted, default False per E-6 AC-11)
- **And** the existing CLI behavior (`create-model --no-generate-description` flag at operator surface) is unchanged

### AC-17: Eval set extension — Model + Method precision floors
- **Given** the 5-paper fixture set with extended gold files (`expected_models`, `expected_methods`) plus a `SELECTION.md` documenting each label and a review sign-off by someone other than the spec author
- **When** the eval test runs (opted in via `-m costly`, run during `/constellize:feature:verify`)
- **Then** all gates hold:
  - Model precision: average ≥ 0.70 AND no paper below 0.50
  - Method precision: average ≥ 0.65 AND no paper below 0.45
  - Combined recall tripwire: (matched gold models + gold methods) / total ≥ 0.45 average (no per-paper floor)
- **And** V1's existing Topic + Concept gates remain unchanged
- **And** per-paper scores are printed in the verification report for regression tracking

**Floor-calibration step (QA Q1 review).** The floors above are draft estimates picked slightly below V1's Concept floors (Concept avg ≥ 0.70 / per-paper ≥ 0.50). They are NOT measured against a real LLM baseline yet. The implementation phase MUST run the new extractors against the 5-paper set once before reaching verify, and the implementation report MUST include the measured per-paper precision + combined recall. If the measured numbers cannot clear these floors, the implementation report flags the gap and the verify gate decides whether to: (a) lower the floors with documented justification, (b) tune the prompts, or (c) call the gap a deferred follow-up. Honest about uncertainty without blocking spec lock-in.

### AC-18: Test guard — populate_citations stub by default
- **Given** `packages/core/tests/data_acquisition/conftest.py` (or the closest in-scope conftest)
- **When** any unit test instantiates `PaperImporter` and calls `import_paper`
- **Then** an autouse fixture has monkeypatched `agentic_kg.knowledge_graph.citation_graph.populate_citations` to an `AsyncMock` returning an empty `CitationPopulationResult`
- **And** tests that exercise the populate-citations wiring opt in by un-patching (e.g., via `monkeypatch.undo()` or by injecting an explicit mock `s2_client`)
- **And** an E2E / staging test that DOES want to hit live S2 is marked with `@pytest.mark.e2e` and lives outside the autouse fixture's scope
- **Rationale (QA Q2 review):** The one-time audit of existing test call sites is a fix, not a defense. New tests must be S2-free by default; opting INTO an S2 call is louder than remembering to opt OUT.

### AC-19: No regression in ingestion wall-clock
- **Given** a baseline per-paper ingestion wall-clock measured before V2 (i.e., post-V1)
- **When** the same paper is ingested after V2
- **Then** the total wall-clock does not exceed `baseline + max(model_latency, method_latency) + citation_latency`
- **And** this is verified manually during the implementation's first staging run (logged, not gated)

## Technical Notes

- **Affected files:**
  - Create: `extraction/model_extractor.py`, `extraction/method_extractor.py`
  - Modify: `extraction/schemas.py`, `extraction/prompts/templates.py`, `extraction/pipeline.py`, `extraction/kg_integration_v2.py`, `data_acquisition/importer.py`, `ingestion.py`, `cli.py` (ingest subcommand flag), `job_runner.py` (env var), `tests/extraction/fixtures/e8_eval/paper_*.gold.yml` (additions), `tests/extraction/fixtures/e8_eval/SELECTION.md` (additions)
  - Touch: none in `problem_extractor.py`, `topic_extractor.py`, `concept_extractor.py`, `b3_linker.py`
- **Reuse:** `BaseLLMClient.extract()` (existing), `create_or_merge_model` (E-3), `create_or_merge_method` (E-4), `link_paper_to_model` / `link_paper_to_method` (E-3 / E-4), `populate_citations` (E-5), V1's `_run` orchestrator wrapper, `instructor` envelope construction
- **Confidence thresholds:** `MIN_MODEL_CONFIDENCE = 0.7`, `MIN_METHOD_CONFIDENCE = 0.7` — constants in `kg_integration_v2.py`, tunable per V1 pattern (governance pattern iii: changes require re-running the eval set + clearing the recall tripwire)
- **No new dependencies.** `instructor` and `openai` already in `pyproject.toml`; Semantic Scholar client already wired for `populate_citations`.
- **No new repository methods needed.** All edge writes go through existing E-3 / E-4 / E-5 repository methods.
- **Sectioning:** V2 extractors get the same section slice as V1's `ConceptExtractor` (abstract + intro + methodology + experiments where present). Empty-section short-circuit inherited.
- **L-1 swap point (TL Q3 review).** Each new extractor takes a `client: BaseLLMClient` kwarg in `__init__`. When L-1 (low-cost SLM client) lands as a third `BaseLLMClient` implementation, the orchestrator constructs per-extractor: e.g., `ModelExtractor(client=local_slm_client)` while keeping `ConceptExtractor(client=openai_client)`. Zero V2 code change required — this is documented as the prescribed migration path. Per-extractor cost/quality routing is a deliberate future capability, not a V2 deliverable.

## Dependencies

- **E-3 (VERIFIED)** — provides `Model`, `create_or_merge_model`, `link_paper_to_model`, canonical seed YAML, embedding dedup.
- **E-4 (VERIFIED)** — provides `Method`, `create_or_merge_method`, `link_paper_to_method`, embedding dedup.
- **E-5 (VERIFIED)** — provides `populate_citations` async helper (never-raises by contract), Semantic Scholar client.
- **E-6 (VERIFIED)** — provides the `generate_description: bool = False` kwarg whose default V2 preserves on ingestion-path callers.
- **E-8 V1 (VERIFIED)** — provides `extract_all_entities` orchestrator pattern, `_run` failure-isolation wrapper, `integrate_paper_entities` shape, eval-set scaffolding.
- **OpenAI API access** — no new keys, just more calls per paper (~+2 per paper net).

## Open Questions

- **Methodology + experiments section availability** — workshop papers often don't segment a clear methodology or experiments section. Defer to implementation; fall back to abstract + intro if methodology absent (same fall-back ConceptExtractor uses today).
- **Model vs Method boundary on novel architectures** — when a paper introduces "XYZ-Net, a transformer-based encoder" with named weights, the extractor sees this in the introduction. Documented in the disambiguation hint; eval-set precision floor catches systemic confusion.
- **Cross-extractor deduplication** — `attention mechanism` could plausibly come back from both the Concept and Method extractors. V2 does NOT add cross-extractor canonicalization; documented as expected V2 behavior, E-7 scope.
- **Description generation on canonical Models** — canonical Models in the seed YAML carry a description already (e.g., BERT's description from the seed). When extractor → merge dedup hits a canonical Model, the existing description wins (per E-3's canonical-protection rules); the extracted description is dropped. Documented; no V2 work needed.
- **populate_citations cost on bulk re-ingestion** — operators running a "fix all citations" pass can call `agentic-kg ingest --update-existing` (existing flag) without `--no-populate-citations`, which now re-runs citation populate per paper. For 1000+ paper runs, this is bounded by S2's quota. Documented as the expected operator path.

## Review Record

Interview + dual-persona review completed 2026-06-14.

**Interview decisions (3 questions answered):**

- **Q1 — Cost budget.** Decision: **option (a)**. Add Model + Method extractors; description-generation stays OFF for the ingestion path. ~5 LLM calls per paper (problem + topic + concept + model + method). Description-gen at create time remains operator-driven via E-6's CLI flags. L-1 (low-cost SLM, BACKLOG item) is the right place to drive ingestion-time cost down — V2 keeps the cost ceiling conservative until L-1 lands.
- **Q2 — Citation hook.** Decision: **option (c)**. `PaperImporter.import_paper` gains `populate_citations: bool = True` (default-on, opt-out) and `s2_client: Optional[...]`. Runs on both create AND `update_existing=True` paths. Rationale: `populate_citations` is never-raises by E-5 contract, so default-on is safe; new ingestions get citations "for free"; opt-out matters for unit tests and for Cloud Run Job bulk-imports when S2 is rate-limiting.
- **Q3 — Orchestrator shape.** Decision: **option (a)**. `extract_all_entities` adds two named kwargs (`model_call`, `method_call`). `PaperExtractionResult` gains typed `models` + `methods` list fields. Existing V1 callers passing only the original three kwargs get a clean `TypeError` rather than silent regression — explicit migration step.

**Tech Lead review (3 questions):**

- **TL Q1 — Closed-set vs open-set for Model.** Decision: **option (a)** — trust embedding-dedup. ModelExtractor stays open-set, mirroring ConceptExtractor. "bert-base-uncased" routes to canonical BERT via E-3's 0.95 cosine threshold. Hybrid Literal + open-set was rejected as premature complexity for the 19 canonical entries. Risk: occasional surface-form near-misses below threshold create duplicates; AC-9 documents this and the eval-set Model precision floor catches systemic duplication (see QA Q3).
- **TL Q2 — Concurrent LLM call burst.** Decision: **option (a)** — trust instructor's existing retry. `instructor + openai` already do exponential backoff on 429; `_run` absorbs unrecoverable `LLMError` as `[]`; degraded paper gets `extraction_incomplete=true` and re-ingestion fixes it. Matches V1's failure-isolation pattern. Per-client semaphores rejected as solving a rare-in-practice failure mode at the cost of shared-client coupling.
- **TL Q3 — L-1 swap point.** Decision: **option (a)** — document per-extractor `client: BaseLLMClient` injection as the L-1 migration path. Zero V2 code change required when L-1 lands. Captured in Technical Notes.

**QA review (3 questions):**

- **QA Q1 — Eval-set precision floors.** Decision: **option (a)** — ship draft floors (Model precision avg ≥ 0.70 / per-paper ≥ 0.50; Method precision avg ≥ 0.65 / per-paper ≥ 0.45; combined recall ≥ 0.45 average). Implementation phase runs the extractors against the 5-paper set once before verify; if measured numbers can't clear the floors, the implementation report flags the gap and the verify gate decides whether to lower with documented justification, tune prompts, or defer. AC-17 documents the calibration step explicitly.
- **QA Q2 — Test code accidentally hits S2.** Decision: **option (a)** — autouse conftest fixture monkeypatches `populate_citations` to a no-op for unit tests. Tests that exercise the wiring opt in by un-patching. AC-18 codifies this contract. Defends against the "future tests forget `populate_citations=False`" failure mode the one-time audit can't catch.
- **QA Q3 — Canonical-BERT merge depends on threshold luck.** Decision: **option (a)** — ship as-is. AC-9's canonical merge depends on E-3's embedding-dedup threshold; if duplicates emerge in the eval, the implementation phase can lower `MODEL_DEDUP_THRESHOLD` from 0.95 to 0.90 (governance pattern iii: re-clear precision + recall at verify). Pre-pass alias normalization was rejected as a static layer that needs maintenance when the canonical seed grows.
