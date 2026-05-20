# Feature: Extraction Prompt Expansion (E-8, V1: Topics + Concepts)

**Status:** SPECIFIED
**Date:** 2026-04-22 (drafted) · 2026-05-18 (dual-persona review complete)
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-8
**Depends On:** E-1 (Topic entities, PR #22), E-2 (ResearchConcept entities, PR #23)
**Decoupled From:** E-3 (Model), E-4 (Method) — deferred to a V2 pass that reuses the same prompt architecture

## Problem

E-1 (Topic) and E-2 (ResearchConcept) shipped first-class entity types, repository CRUD, APIs, and CLI commands — but **no automated path populates them**. Every `Topic` and `ResearchConcept` node in the graph today has to be created by hand via `agentic-kg create-concept` or `assign-topic`. The ingestion pipeline (`ingest_papers` → `pdf_extractor` → `problem_extractor` → `kg_integration_v2`) only extracts `Problem` statements from Limitations/Future Work sections; it sees the paper's topic and the concepts it discusses but writes neither. As real ingestion scales, the `Topic` taxonomy stays frozen at the 29 seed nodes and `ResearchConcept` stays empty, nullifying the investment in E-1/E-2.

## Goals

- Every ingested paper emits, in addition to existing `ProblemMention`s:
  - Zero-or-more `DISCUSSES` edges from `Paper` to closed-set `Topic` nodes (`BELONGS_TO` — paper's area/subtopic)
  - Zero-or-more `ResearchConcept` nodes (created or merged via embedding-dedup) linked to the paper via `DISCUSSES`
  - Zero-or-more `INVOLVES_CONCEPT` edges from the paper's extracted `ProblemConcept`s to the paper's extracted `ResearchConcept`s, derived heuristically (no extra LLM calls)
- Extraction runs in parallel across problem, topic, and concept tasks with no shared prompt state — one failing extractor does not kill the other two.
- Prompt architecture explicitly designed so V2 can add `Model` (E-3) and `Method` (E-4) extractors without rewriting V1 prompts or schemas.
- No regression in problem-extraction quality or throughput; total per-paper LLM wall-clock increase bounded by the slowest of the three parallel calls (not the sum).
- Quality gate at verify time: a small hand-labeled eval set of 5 papers, one per major area, with both average and per-paper precision floors checked.

## Non-Goals

- **E-3 Model / E-4 Method extraction** — deferred to a V2 prompt revision. V1 schemas and templates are designed for extension but do not include these entity types.
- **Global concept-to-concept canonicalization** — if two papers extract "attention mechanism" and "attention," the `create_or_merge_research_concept` call collapses them via embedding-dedup (already working); this feature does not add a new cross-paper normalization step.
- **Novel topic creation** — the prompt is constrained to the 29 seed nodes in `seed_taxonomy.yml`. Growing the taxonomy is tracked as backlog item T-1.
- **Concept confidence routing** — extracted concepts go through the existing `create_or_merge_research_concept` path. There is no auto-link / agent-review / human-queue pipeline for concepts in V1 (that is the scope of E-7).
- **`INVOLVES_CONCEPT` via LLM** — V1 uses a pure-Python alias-substring heuristic (see B3 in Design Approach). Upgrading to an LLM or embedding-based linker is a future optimization, not an E-8 goal.
- **Changing `ProblemExtractor`** — this feature adds two new sibling extractors. It does not modify `problem_extractor.py` or its prompts.

## User Stories

- **As a researcher**, I want every paper I ingest to auto-populate its area and subtopic in the graph, so I can browse papers by topic without manual curation.
- **As a researcher**, I want to ask "which concepts does this paper discuss?" and get a populated answer on the first day after ingestion, not after a future normalization pass.
- **As a researcher**, I want "which problems involve attention mechanisms?" to return a non-empty result once papers about attention are ingested — even if the answer has some false positives I can skim past.
- **As a developer**, I want topic/concept extraction failures to be isolated — a bad concept prompt should not lose me the problems that the same paper yields.
- **As a developer** extending the pipeline later, I want to add `Model` / `Method` extractors by copying the `TopicExtractor` class and its prompt template, not by rewriting the orchestrator.

## Design Approach

### Architecture (Route B: parallel per-type)

```
paper.content ──► ProblemExtractor ────► problems   ┐
              ├─► TopicExtractor  ────► topics      │
              └─► ConceptExtractor ───► concepts    ├─► asyncio.gather
                                                    ┘
                                   │
                                   ▼
                          PaperExtractionResult
                                   │
                                   ▼
              kg_integration_v2.py (existing paths + 3 new writers)
                                   │
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
          ProblemMention      Topic edges        ResearchConcept
          (unchanged)         BELONGS_TO         DISCUSSES edges
                              (Paper→Topic)      (Paper→Concept)
                                                       │
                                                       ▼
                                           link_problems_to_concepts()
                                                (B3 heuristic)
                                                       │
                                                       ▼
                                           INVOLVES_CONCEPT edges
                                           (ProblemConcept→Concept)
```

- **Three independent async LLM calls** per paper, one per entity type. One failure never blocks the others — each is logged and the pipeline commits whatever succeeded.
- **Problem extraction is untouched.** No changes to `problem_extractor.py`, its prompt, or its schema.
- **Topic and concept extraction are paper-level, not section-level.** Problems are scattered across Limitations/Future Work; topics and concepts are derivable from the abstract + introduction (+ methodology for concepts). This halves prompt traffic vs. per-section calls.

### New modules and where they live

| File | Purpose |
|---|---|
| `packages/core/src/agentic_kg/extraction/schemas.py` (additions) | `ExtractedTopicAssignment`, `ExtractedResearchConcept`, `ExtractedEntities` envelope |
| `packages/core/src/agentic_kg/extraction/prompts/templates.py` (additions) | `TOPIC_SYSTEM_PROMPT_V1`, `TOPIC_USER_PROMPT_TEMPLATE_V1`, `CONCEPT_SYSTEM_PROMPT_V1`, `CONCEPT_USER_PROMPT_TEMPLATE_V1` — the closed-set taxonomy is rendered into the topic prompt at module load time |
| `packages/core/src/agentic_kg/extraction/topic_extractor.py` (new) | `TopicExtractor` class — parallel to `ProblemExtractor`, paper-level single call |
| `packages/core/src/agentic_kg/extraction/concept_extractor.py` (new) | `ConceptExtractor` class — parallel to `ProblemExtractor`, paper-level single call |
| `packages/core/src/agentic_kg/extraction/pipeline.py` (additions) | `extract_all_entities(paper)` orchestrator with `asyncio.gather` |
| `packages/core/src/agentic_kg/extraction/kg_integration_v2.py` (additions) | Writers for topic-assignment edges, concept creation/merge, and B3 heuristic linker |
| `packages/core/tests/extraction/fixtures/e8_eval/` (new) | 5 hand-labeled fixture papers: `paper_<id>.txt` + `paper_<id>.gold.yml` (expected topics + concepts) |

### Schemas (per-instance taxonomy reload)

The `ExtractedTopicAssignment` schema is built **per-`TopicExtractor`-instance**, not at module import time. Each job invocation constructs a fresh extractor, which reads `seed_taxonomy.yml` once in `__init__`, builds a dynamic Pydantic model whose `topic_name` field is a `Literal` over the current taxonomy names, and injects the same list into its prompt template. This keeps schema ↔ prompt in lockstep within a job and limits taxonomy-drift staleness to a single job's lifetime. See "Taxonomy reload lifecycle" below for the contract.

```python
# Static shape — names-free. The dynamic topic_name Literal is bolted
# on by TopicExtractor.__init__ via pydantic.create_model.
class _ExtractedTopicAssignmentBase(BaseModel):
    level: Literal["domain", "area", "subtopic"]
    confidence: float = Field(ge=0, le=1, default=0.8)
    reasoning: Optional[str] = None

class ExtractedResearchConcept(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)  # grounding, mirrors ExtractedProblem
```

```python
# packages/core/src/agentic_kg/extraction/topic_extractor.py
from pydantic import create_model, Field as PField

class TopicExtractor:
    def __init__(
        self,
        client: BaseLLMClient,
        taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
        min_confidence: float = 0.7,
    ):
        self.client = client
        self.min_confidence = min_confidence
        # Snapshot once per instance.
        taxonomy = load_taxonomy(taxonomy_path)
        self._taxonomy_names = tuple(taxonomy.all_node_names())
        self._taxonomy_levels = {n.name: n.level for n in taxonomy.walk()}
        # Build the Literal-bearing Pydantic model dynamically.
        self.assignment_model = create_model(
            "ExtractedTopicAssignment",
            __base__=_ExtractedTopicAssignmentBase,
            topic_name=(Literal[self._taxonomy_names], PField(...)),
        )
        self.envelope_model = create_model(
            "ExtractedTopicEnvelope",
            topics=(list[self.assignment_model], PField(default_factory=list, max_length=5)),
        )
        # Prompt built from the same snapshot.
        self._prompt_system, self._prompt_user_tpl = build_topic_prompt(self._taxonomy_names)
```

The `ExtractedEntities` envelope in the orchestrator result is still a plain container — not the instructor response schema — so it doesn't need dynamic construction:

```python
class ExtractedEntities(BaseModel):
    """Per-paper container for orchestrator results; not sent to the LLM."""
    topics: list[_ExtractedTopicAssignmentBase] = Field(default_factory=list, max_length=5)
    concepts: list[ExtractedResearchConcept] = Field(default_factory=list, max_length=20)
```

**Taxonomy reload lifecycle.** Each new `TopicExtractor()` instance re-reads the taxonomy from disk. In practice: one Cloud Run Job invocation → one `TopicExtractor` → one taxonomy snapshot, consistent for the life of that job. An operator running `agentic-kg load-taxonomy` mid-flight does **not** affect in-flight jobs; subsequent jobs pick up the change on their next start. This is documented in the ingestion runbook.

### Prompt design — V2-extensible

Each extractor owns its prompt template, versioned via `PromptVersion.V1` (already in `templates.py`). Prompt templates follow the existing `SYSTEM_PROMPT_V1` / `USER_PROMPT_TEMPLATE_V1` naming convention so the next author can literally duplicate-rename for Model/Method.

**Topic prompt structure (closed-set, T-A):**
- System prompt: "You will be given a paper's abstract and introduction. Pick the *smallest number* of topics from the provided list that accurately characterize the paper's research area(s). Do not invent new topic names."
- User prompt: injects the paper title + abstract + intro + `TAXONOMY:\n<newline-joined 29-node list with their level>`. Schema's `Literal` prevents out-of-taxonomy values.

**Concept prompt structure:**
- System prompt: "Extract the research concepts (techniques, theories, frameworks) the paper uses or discusses. Include well-known synonyms as aliases. Do not extract overly general terms like 'machine learning' or 'neural network'. Ground each concept in quoted text."
- User prompt: injects paper title + abstract + intro + methodology. No closed set — the `create_or_merge_research_concept` call on write handles dedup.

Both prompts use the same `system/user` dataclass and `get_extraction_prompt(...)` dispatcher, with a new `EntityKind` enum to route by extractor.

### B3 heuristic: problem ↔ concept linking

Runs after concept creation/merge succeeds for a paper. **Critical design note:** the linker uses surface forms that the *LLM emitted for this paper*, NOT the merged node's accumulated alias list. This prevents alias-pollution, where a popular concept's aliases grow with each paper and match ever more aggressively on later papers. `create_or_merge_research_concept` consolidates entities across papers; B3 deliberately uses only per-paper linguistic evidence.

```python
def link_problems_to_concepts(
    mentions: list[ProblemMention],                         # from THIS paper
    paper_extractions: list[tuple[ExtractedResearchConcept, str]],
    # Each tuple: (what-the-LLM-extracted-for-this-paper, merged_concept_id).
    # The merged id is for graph writes; the aliases come from the extraction.
    min_alias_length: int = 4,
    alias_deny_list: frozenset[str] = DEFAULT_ALIAS_DENY_LIST,
) -> list[tuple[str, str]]:
    """
    Case-insensitive whole-word alias match. Matches against surface forms
    the LLM emitted for THIS paper only, not accumulated historical aliases.
    Returns (problem_concept_id, concept_id) pairs for caller to write
    via repo.link_problem_to_concept().
    """
    edges = []
    for extracted, concept_id in paper_extractions:
        surface = [
            s.lower()
            for s in [extracted.name, *extracted.aliases]
            if len(s) >= min_alias_length and s.lower() not in alias_deny_list
        ]
        if not surface:
            continue
        pat = re.compile(
            r"\b(" + "|".join(re.escape(s) for s in surface) + r")\b",
            flags=re.IGNORECASE,
        )
        for m in mentions:
            hay = f"{m.statement} {m.quoted_text}".lower()
            if pat.search(hay):
                edges.append((m.problem_concept_id, concept_id))
                logger.debug(
                    "B3-link: mention=%s concept=%s matched=%s",
                    m.id, concept_id, pat.search(hay).group(1),
                )
    return edges
```

**Why per-paper aliases only.** If paper #50 extracts "retrieval augmented generation" and it merges into a popular concept that already has aliases `["RAG", "retrieval", "augmented generation", "external retrieval"]` from prior papers, matching against the merged list would fire on any of paper #50's mentions containing "retrieval." By matching only against what the LLM emitted for paper #50 (typically `["retrieval augmented generation", "RAG"]`), we stay tight to the linguistic evidence this paper actually provides. Recall risk: rare cases where the LLM is terse and misses an alias the paper uses. Accepted as a fair trade for the pollution immunity; revisit in a follow-on feature if recall proves too low on the eval set.

**Precision calibration.** `min_alias_length=4` is the starting heuristic (suppresses "ML", "GNN" as linkable — we would rather miss those than mislabel every mention containing "ML"). The implementation phase runs the linker over the 5-paper eval set and tunes this threshold once. Decision and final value go into the verification record.

### Integration point

`kg_integration_v2.py` gains three new writers, all wired into the existing `IntegrationResultV2` flow:

```python
async def integrate_paper_entities(
    paper: Paper,
    extracted: ExtractedEntities,
    mention_results: list[MentionIntegrationResult],  # from existing flow
    repo: Neo4jRepository,
) -> EntityIntegrationResult:
    # 1. Topic assignments → BELONGS_TO edges
    for t in extracted.topics:
        if t.confidence >= MIN_TOPIC_CONFIDENCE:
            topic = repo.get_topic_by_name(t.topic_name)  # closed-set: must exist
            repo.assign_entity_to_topic(
                entity_id=paper.doi, topic_id=topic.id, entity_label="Paper"
            )
    # 2. ResearchConcept create/merge + DISCUSSES edges
    paper_extractions: list[tuple[ExtractedResearchConcept, str]] = []
    for c in extracted.concepts:
        if c.confidence >= MIN_CONCEPT_CONFIDENCE:
            concept, _ = repo.create_or_merge_research_concept(
                name=c.name, description=c.description, aliases=list(c.aliases),
            )
            repo.link_paper_to_concept(
                paper_doi=paper.doi, research_concept_id=concept.id,
            )
            # Retain the ORIGINAL extraction for B3 matching (not `concept`,
            # whose alias list may include prior papers' aliases).
            paper_extractions.append((c, concept.id))
    # 3. B3 heuristic INVOLVES_CONCEPT — per-paper aliases only
    mentions = [m for m in mention_results if m.concept_id]
    edges = link_problems_to_concepts(
        mentions=[repo.get_problem_mention(m.mention_id) for m in mentions],
        paper_extractions=paper_extractions,
    )
    for problem_concept_id, concept_id in edges:
        repo.link_problem_to_concept(
            problem_concept_id=problem_concept_id,
            research_concept_id=concept_id,
        )
    return EntityIntegrationResult(...)
```

### Evaluation set (C: labeled eval, ~5 papers)

**Location:** `packages/core/tests/extraction/fixtures/e8_eval/`

Each eval paper ships as two files:

```
paper_<slug>.txt                # abstract + introduction + methodology concatenated
paper_<slug>.gold.yml           # hand-labeled expected output
```

Gold file structure:
```yaml
paper_id: "2401.12345"
title: "Paper title"
expected_topics:
  # Each expected topic must be one of the 29 seed taxonomy nodes.
  - name: "NLP"
    level: "area"
  - name: "Retrieval-Augmented Generation"
    level: "subtopic"
expected_concepts:
  - canonical: "attention mechanism"
    # Any of these surface forms appearing in the extractor's output
    # counts as a correct extraction for this gold concept.
    acceptable_aliases: ["self-attention", "scaled dot-product attention"]
  - canonical: "retrieval augmented generation"
    acceptable_aliases: ["RAG"]
```

Scoring runs at the verify gate (`@pytest.mark.costly` — skipped by default, opted in during verify):
- **Topic precision** = (correct topic predictions) / (total topic predictions).
  - Gate: average across 5 papers ≥ 0.80 **AND** no individual paper below 0.60.
- **Concept precision** = (predicted concepts matching a gold canonical or acceptable_alias, case-insensitive) / (total predicted concepts).
  - Gate: average across 5 papers ≥ 0.70 **AND** no individual paper below 0.50.
- **Concept recall (anti-gaming tripwire)** = (gold concepts matched by some prediction) / (total gold concepts).
  - Gate: average across 5 papers ≥ 0.50. No per-paper floor — this is a tripwire, not a quality bar.
  - **This is NOT a recall quality target.** Its sole purpose is to make confidence-threshold gaming visible: without it, `MIN_CONCEPT_CONFIDENCE` could be cranked to 0.95 so the extractor emits almost nothing and precision looks perfect. The floor sits at 0.50 precisely because V1 is *willing* to miss extractions — missed concepts are recoverable on re-ingestion; false edges pollute the graph permanently. Do not over-invest in recall tuning to clear this bar.

**Why dual gates (average + per-paper floor).** With N=5, averaging alone is fragile: one outlier dominates and one strong paper can mask one pathological paper (e.g., four papers at 0.95 + one at 0.20 still averages 0.80 — gate passes, latent failure mode ships). The per-paper floor catches pathological extractions specifically; the average enforces overall quality. The floors (0.60 / 0.50) sit intentionally below the average targets — a genuinely hard paper at 0.65 should not block release; a paper at 0.25 should.

**Confidence-threshold governance.** Any change to `MIN_CONCEPT_CONFIDENCE` or `MIN_TOPIC_CONFIDENCE` requires the verify eval run to demonstrate that **both** the precision targets AND the recall tripwire still hold. This prevents a quiet threshold bump from gaming one metric at the other's expense — same governance shape as the B3 deny-list (pattern iii).

**Eval set selection (cross-area lock-in).** Exactly 5 papers, one each from the following areas, to prevent prompt-tuning bias toward easy-to-label domains:
1. NLP
2. CV
3. IR
4. ML / general
5. DM or AI Agents (pick whichever has a clearer paper available)

Each fixture's selection rationale (why this paper, what makes it labelable, any caveats) is documented in a short `e8_eval/SELECTION.md` alongside the fixtures. **Selection requires review by someone other than the spec author** — cheapest path: knowledge-steward persona during the next `constellize:memory:update` pass after the fixtures land. If a confident hand-label cannot be produced for one of the five areas, drop to 4 papers, flag the missing area as a known eval gap in the verify record, and do not substitute another paper from a covered area.

**Future expansion.** 5 papers is the floor, not the target. Once labeling tooling exists (or contributors hand-label additional fixtures), the eval set should expand to ≥ 10 papers. Tracked as a follow-up in the spec's open-questions section, not in V1 scope.

## Sample Implementation

```python
# === packages/core/src/agentic_kg/extraction/schemas.py (additions) ===

from typing import Literal, Optional
from pydantic import BaseModel, Field

class _ExtractedTopicAssignmentBase(BaseModel):
    level: Literal["domain", "area", "subtopic"]
    confidence: float = Field(ge=0, le=1, default=0.8)
    reasoning: Optional[str] = None

class ExtractedResearchConcept(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: Optional[str] = Field(default=None, max_length=400)
    confidence: float = Field(ge=0, le=1, default=0.8)
    quoted_text: str = Field(..., min_length=10)

class ExtractedEntities(BaseModel):
    """Orchestrator container — not an instructor response model."""
    topics: list[_ExtractedTopicAssignmentBase] = Field(default_factory=list, max_length=5)
    concepts: list[ExtractedResearchConcept] = Field(default_factory=list, max_length=20)


# === packages/core/src/agentic_kg/extraction/topic_extractor.py (new) ===

class TopicExtractor:
    """Parallel-to-ProblemExtractor; single paper-level call.
    Taxonomy is snapshotted at __init__ — one job, one snapshot."""

    def __init__(
        self,
        client: BaseLLMClient,
        taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
        min_confidence: float = 0.7,
    ):
        self.client = client
        self.min_confidence = min_confidence
        taxonomy = load_taxonomy(taxonomy_path)
        self._names = tuple(taxonomy.all_node_names())
        # Dynamic Pydantic model: Literal bound to this instance's taxonomy.
        self.assignment_model = create_model(
            "ExtractedTopicAssignment",
            __base__=_ExtractedTopicAssignmentBase,
            topic_name=(Literal[self._names], PField(...)),
        )
        self.envelope_model = create_model(
            "ExtractedTopicEnvelope",
            topics=(list[self.assignment_model], PField(default_factory=list, max_length=5)),
        )
        self._system_prompt, self._user_prompt_tpl = build_topic_prompt(self._names)

    async def extract(self, paper_title: str, sections_text: str) -> list[_ExtractedTopicAssignmentBase]:
        if not sections_text.strip():
            return []  # skip LLM call entirely — nothing to extract from
        try:
            response = await self.client.extract(
                prompt=self._user_prompt_tpl.format(
                    paper_title=paper_title, section_text=sections_text,
                ),
                response_model=self.envelope_model,
                system_prompt=self._system_prompt,
            )
        except LLMError as e:
            logger.warning("Topic extraction failed: %s", e)
            return []  # degrade gracefully — other extractors keep running
        return [t for t in response.content.topics if t.confidence >= self.min_confidence]


# === packages/core/src/agentic_kg/extraction/pipeline.py (orchestrator) ===

@dataclass
class ExtractionFailure:
    """Structured record of a single extractor's unexpected failure."""
    extractor: str                  # "problem" | "topic" | "concept"
    exception_type: str             # e.g. "TimeoutError"
    message: str                    # str(exc), truncated to 500 chars
    traceback: str                  # truncated to 4 KB
    occurred_at: datetime

@dataclass
class PaperExtractionResult:
    problems: list[ExtractedProblem]
    topics: list[_ExtractedTopicAssignmentBase]
    concepts: list[ExtractedResearchConcept]
    failures: list[ExtractionFailure]  # empty on full success

    @property
    def is_partial(self) -> bool:
        return bool(self.failures)

async def extract_all_entities(paper: PaperContent) -> PaperExtractionResult:
    """Parallel extraction.
    Every extractor (known or unknown exception) degrades to an empty
    result + a recorded ExtractionFailure. Partial results are ALWAYS
    returned so downstream integration can commit what succeeded."""

    async def _run(name: str, coro):
        try:
            return name, await coro, None
        except BaseException as e:   # intentionally catches CancelledError too
            logger.exception("Extractor %s failed for paper %s", name, paper.doi)
            failure = ExtractionFailure(
                extractor=name,
                exception_type=type(e).__name__,
                message=str(e)[:500],
                traceback=traceback.format_exc()[:4096],
                occurred_at=datetime.now(timezone.utc),
            )
            return name, _empty_for(name), failure

    results = await asyncio.gather(
        _run("problem", problem_extractor.extract_from_sections(...)),
        _run("topic", topic_extractor.extract(...)),
        _run("concept", concept_extractor.extract(...)),
        # gather() itself cannot raise because every branch catches.
    )
    slots = {name: (payload, failure) for name, payload, failure in results}
    return PaperExtractionResult(
        problems=slots["problem"][0],
        topics=slots["topic"][0],
        concepts=slots["concept"][0],
        failures=[f for _, f in slots.values() if f is not None],
    )


# === B3 linker shown in full above. ===
```

## Edge Cases & Error Handling

### Topic prompt returns a name not in the taxonomy
- **Scenario:** LLM hallucinates "Graph Neural Networks" when the taxonomy has "GNN".
- **Behavior:** Pydantic `Literal` validation fails → `instructor` retries up to `max_retries` times with the error message in the conversation → if still invalid, the topic is dropped and logged at WARN level. Paper still receives zero topic assignments (degraded, not crashed).
- **Test:** Mock LLM client to return a fake topic name; assert the extractor returns `[]` and logs the rejection.

### LLM emits confidence below threshold
- **Scenario:** Extractor returns 8 concepts with average confidence 0.4.
- **Behavior:** `TopicExtractor`/`ConceptExtractor` filter by `min_confidence` (default 0.7). Unconfident extractions are dropped before reaching the writer; counts are logged.
- **Test:** Unit test that injects low-confidence items and asserts they are filtered.

### Concept extraction call fails (known LLM error) while problem extraction succeeds
- **Scenario:** OpenAI returns a 500 on the concept call; problem + topic calls return cleanly.
- **Behavior:** The concept extractor's own try/except catches `LLMError`, returns `[]`, logs WARN. The orchestrator's `_run` sees a clean empty result — no `ExtractionFailure` is recorded. Problems and topics are committed normally; concepts are empty for this paper.
- **Test:** Patch concept extractor to raise `LLMError` internally; assert the extractor returns `[]` and the orchestrator result has `failures == []`, but a WARN was emitted.

### Unexpected exception in one extractor (timeout, schema bug, cancellation)
- **Scenario:** The topic extractor raises `asyncio.TimeoutError` mid-extraction after exhausting retries, OR an instructor post-validation bug raises `AttributeError`. These fall *outside* the known `LLMError` hierarchy.
- **Behavior:** The orchestrator's `_run(...)` catches `BaseException` (including `CancelledError`), records an `ExtractionFailure` with extractor name, exception type, truncated message, and truncated traceback, and returns an empty payload for that extractor. `PaperExtractionResult.is_partial` is `True` and `failures` contains the record. The other extractors' results are preserved. A full traceback is logged at ERROR level with the paper DOI.
- **Audit trail writer:** `integrate_paper_entities` reads `result.failures`; if non-empty, it writes `Paper.extraction_incomplete = true` plus a JSON-stringified list of failing extractor names on the Paper node, so a sanity-check query (`MATCH (p:Paper) WHERE p.extraction_incomplete = true`) can surface papers needing re-ingestion.
- **Test:** Patch topic extractor to raise `TimeoutError`. Assert: `PaperExtractionResult.is_partial is True`, `failures` has one entry with `extractor == "topic"` and `exception_type == "TimeoutError"`, problems and concepts were preserved, and the Paper node carries `extraction_incomplete = true` after integration.

### B3 linker triggers on generic alias
- **Scenario:** The LLM emits "model" (5 chars) as an alias of a concept. Under `min_alias_length=4` alone, "model" would qualify. Every ProblemMention containing "model" would then get linked. False-positive avalanche.
- **Behavior:** Alias length alone is insufficient — the deny-list loaded from `b3_deny_list.yml` (initial terms: `model`, `network`, `system`, `approach`, `method`, `algorithm`, `paper`, `work`) is applied before length filtering. Every match is logged with the triggering alias so calibration can catch drift.
- **Test:** Unit test with an extracted concept whose aliases include "model" and a mention containing "our model achieves"; assert no link is created.

### B3 linker does NOT use historical aliases (pollution immunity)
- **Scenario:** A popular concept has accumulated 12 aliases across 30 prior papers (e.g., `["RAG", "retrieval augmented generation", "retrieval augmentation", "retrieval", "augmented generation", ...]`). Paper #31 extracts the concept with LLM output `["retrieval-augmented generation"]` only (no short forms). The paper's mentions contain "retrieval methods".
- **Behavior:** The linker matches only against the LLM's per-paper surface forms (`["retrieval-augmented generation"]`), NOT the merged node's accumulated alias list. The mention text "retrieval methods" does NOT trigger a match, because "retrieval" is not in this paper's extraction.
- **Test:** Seed a merged `ResearchConcept` with historical aliases `["retrieval", "RAG"]`. Extract concept for paper #31 with aliases `["retrieval-augmented generation"]` (no "retrieval"). Assert that a mention containing "retrieval" does NOT produce an `INVOLVES_CONCEPT` edge.

### Duplicate concepts in the same paper's extraction
- **Scenario:** LLM emits both "attention mechanism" and "self-attention" as separate concepts for one paper.
- **Behavior:** `create_or_merge_research_concept` (E-2) dedups via 0.90 cosine similarity. Both inputs converge to one `ResearchConcept` node with both names as aliases. The paper gets one `DISCUSSES` edge (second call hits the idempotent MERGE).
- **Test:** Integration test against live Neo4j; assert 1 concept node + 1 edge.

### Paper has no abstract or introduction
- **Scenario:** Section segmenter didn't identify an abstract (common for workshop papers).
- **Behavior:** `paper.concat_sections(...)` returns empty string → extractors skip the call entirely (preflight check) → log INFO "skipped: no input sections" → contribute zero topics/concepts. No LLM budget wasted.
- **Test:** Unit test: feed a paper with no abstract/intro to the orchestrator; assert zero LLM calls made for topic + concept extractors.

### Taxonomy grows mid-deploy
- **Scenario:** `seed_taxonomy.yml` gains a 30th node while ingestion jobs are running.
- **Behavior:** `TopicExtractor` is instantiated **once per batch / process** (matching the Cloud Run Job lifecycle: 1 job = 1 process = 1 batch = 1 snapshot). Each instance snapshots the taxonomy in `__init__`. In-flight batches keep their original snapshot — validation and prompt stay consistent for every paper in that batch. The *next* batch invocation reads the file again and picks up the 30th node automatically. Long-running daemon processes (if ever introduced) must explicitly instantiate `TopicExtractor` fresh per batch-loop iteration — not once at process startup — or accept arbitrary staleness.
- **Detectability — `Paper.taxonomy_hash`:** At integration time, `integrate_paper_entities` writes a `taxonomy_hash` property on the Paper node: a sha256 of the canonically-serialized taxonomy (parse → sort keys → dump → hash). This means whitespace and comment edits do not change the hash; only semantically meaningful taxonomy changes do. A one-line audit query `MATCH (p:Paper) WHERE p.taxonomy_hash <> $current_hash RETURN p` lists every paper ingested under a stale taxonomy. Stale papers are candidates for re-ingestion via the AC-13 purge-then-rewrite path.
- **Test (snapshot isolation):** Unit test — instantiate `TopicExtractor` against a tmp taxonomy file, mutate the file on disk, instantiate a second `TopicExtractor`, assert the second instance's `_names` includes the added node and the first instance's does not.
- **Test (hash on graph):** After integration, assert `Paper.taxonomy_hash` equals the canonical sha256 of the taxonomy the extractor used. Mutate the taxonomy file (semantic change), confirm hash changes; mutate only comments/whitespace, confirm hash does NOT change.

### Re-ingestion of an `extraction_incomplete` paper
- **Scenario:** Paper X was ingested last week with `extraction_incomplete=true` (topic extractor failed). The operator re-ingests it today after the LLM upstream is healthy.
- **Behavior (purge-then-rewrite):** The orchestrator detects the paper exists in the graph and:
  1. Refuses to proceed if any `Problem` node attributable to this paper has *non-extraction* incident edges (e.g., a manual `SOLVED_BY`, human-curated tag, or an inbound edge from another paper's pipeline). Re-ingestion requires an explicit `--force-rewrite` flag to override. The CLI surfaces the blocking edges so the operator can audit before forcing.
  2. Otherwise: purges this paper's extraction footprint — `Problem` and `ProblemMention` nodes uniquely attributable to this paper, plus all `HAS_TOPIC` / `INVOLVES_CONCEPT` / `DISCUSSES` / `EXTRACTED_FROM` edges originating from this paper.
  3. Does **NOT** delete shared `Topic` or `ResearchConcept` nodes — they may be referenced by other papers. E-1's closed-set topic merge and E-2's embedding-based dedup correctly re-converge concepts on the next extraction pass.
  4. Re-runs all three extractors from scratch. On full success, `extraction_incomplete` is set back to `false` and per-extractor failure records are cleared.
- **Why purge-then-rewrite rather than incremental re-run-only-the-failing-extractor:** Incremental requires the graph to durably remember *which* extractors failed and reconcile partial overlap (e.g., the topic extractor's idempotency assumes the paper's existing extraction footprint is consistent). Purge-then-rewrite is one well-understood operation with a single failure mode.
- **Test (happy path):** Ingest a paper with topic extractor patched to fail. Confirm `extraction_incomplete=true` and only concept+problem footprint in graph. Re-ingest with topic patch removed. Assert `extraction_incomplete=false`, the original problem/concept nodes were purged + rewritten (new UUIDs on problems is acceptable; concept nodes merge-converge by embedding), and topic edges now exist.
- **Test (guardrail):** Ingest a paper with topic failure. Manually attach a `SOLVED_BY` edge to one `Problem` node. Re-ingest without `--force-rewrite`. Assert: orchestrator refuses, exit code non-zero, error message names the blocking edge. Re-ingest with `--force-rewrite`. Assert: re-ingestion proceeds, the `SOLVED_BY` edge is reported as collateral data loss in the verify log.

## Acceptance Criteria

### AC-1: New schemas
- **Given** `extraction/schemas.py`
- **When** imported
- **Then** `_ExtractedTopicAssignmentBase`, `ExtractedResearchConcept`, and `ExtractedEntities` classes exist with the field validators described above
- **And** module import does NOT read `seed_taxonomy.yml` (taxonomy coupling is owned by `TopicExtractor`, not the schema module)

### AC-2: Topic extractor (closed-set, per-instance taxonomy)
- **Given** a `TopicExtractor` instantiated against a specific `taxonomy_path`
- **When** the LLM returns a `topic_name` that is in that path's taxonomy
- **Then** the extractor returns the topic assignment
- **And** when the LLM returns a name NOT in that path's taxonomy, the dynamically-built Pydantic model rejects it and the extractor returns `[]` with a WARN log
- **And** two `TopicExtractor` instances pointed at different taxonomy snapshots have independent accepted-name sets

### AC-3: Concept extractor
- **Given** a `ConceptExtractor` with a mocked LLM client
- **When** the client returns concepts above the confidence threshold
- **Then** the extractor returns them verbatim
- **And** concepts below threshold are filtered out

### AC-4: Parallel orchestration
- **Given** `extract_all_entities(paper)` with mocked problem, topic, and concept extractors
- **When** all three are async-callable
- **Then** they are invoked concurrently via `asyncio.gather` (verified by timing or by mock call order)
- **And** `PaperExtractionResult` contains all three outputs

### AC-5: Per-extractor degradation (known and unknown exceptions)
- **Given** the orchestrator with three extractors
- **When** one of them raises *any* exception (both known `LLMError` and arbitrary non-`LLMError` exceptions like `TimeoutError`, `AttributeError`, or `CancelledError`)
- **Then** the other two still return their results in `PaperExtractionResult`
- **And** for known `LLMError` (handled inside the extractor) no `ExtractionFailure` is recorded, only a WARN log
- **And** for unexpected exceptions the orchestrator records an `ExtractionFailure` in `PaperExtractionResult.failures` with extractor name, exception type, truncated message, truncated traceback, and timestamp
- **And** an ERROR log is emitted including the paper DOI and full traceback

### AC-5b: Partial extractions are observable on the graph
- **Given** a `PaperExtractionResult` where `is_partial` is true (at least one `ExtractionFailure`)
- **When** `integrate_paper_entities(...)` runs
- **Then** the corresponding `Paper` node carries `extraction_incomplete = true` and a JSON-stringified `extraction_failed_extractors` property listing the failing extractor names
- **And** a sanity-check query `MATCH (p:Paper) WHERE p.extraction_incomplete = true RETURN p` returns exactly those papers
- **And** on a subsequent successful re-ingestion of the same paper, the `extraction_incomplete` flag is cleared

### AC-6: BELONGS_TO edge written
- **Given** an `ExtractedTopicAssignment` and a live `Paper` node
- **When** `integrate_paper_entities(...)` runs
- **Then** a `BELONGS_TO` edge exists from the `Paper` to the matching `Topic` node
- **And** the edge is idempotent on re-ingestion (no double-counts)

### AC-7: DISCUSSES edge written; concept merged
- **Given** an `ExtractedResearchConcept` and a live `Paper` node
- **When** `integrate_paper_entities(...)` runs
- **Then** `create_or_merge_research_concept` is called, returning an existing concept if embedding-similarity ≥ 0.90
- **And** a `DISCUSSES` edge exists from the `Paper` to the `ResearchConcept`

### AC-8: B3 heuristic links problems to concepts using per-paper aliases only
- **Given** a paper with 1+ extracted `ProblemMention`s and 1+ `ExtractedResearchConcept`s (each associated with a merged `ResearchConcept.id`)
- **When** `link_problems_to_concepts(...)` runs with `min_alias_length=4` and the deny-list applied
- **Then** mentions whose statement or quoted_text contains a surface form from the **LLM-emitted aliases for this paper** (whole-word, case-insensitive, length ≥ 4, not in deny-list) are linked via `INVOLVES_CONCEPT`
- **And** historical aliases accumulated on the merged concept from *other* papers are NOT used for matching (pollution immunity test: set up a merged concept with aliases from prior papers not present in the current extraction; assert those aliases do not trigger matches)
- **And** each match is logged with the matched alias for later audit

### AC-9: Problem extraction untouched
- **Given** the existing `ProblemExtractor` test suite
- **When** this feature is merged
- **Then** all existing tests in `packages/core/tests/extraction/` continue to pass with zero modifications
- **And** `problem_extractor.py` is unchanged

### AC-10: Prompt templates are V2-extensible
- **Given** `extraction/prompts/templates.py`
- **When** inspected
- **Then** topic and concept prompts follow the same naming convention as `SYSTEM_PROMPT_V1` / `USER_PROMPT_TEMPLATE_V1`
- **And** `get_extraction_prompt` dispatches via an `EntityKind` enum so adding `EntityKind.MODEL` / `EntityKind.METHOD` in V2 is additive, not a rewrite

### AC-11: Tests passing
- **Given** the full test suite
- **When** `pytest packages/core/tests/extraction/` runs
- **Then** new unit tests cover: each extractor's success path, taxonomy rejection, low-confidence filtering, orchestrator parallelism, per-extractor degradation, B3 linker, deny-list behavior, and each integration writer
- **And** all tests pass

### AC-12: Eval set precision
- **Given** 5 hand-labeled fixture papers in `packages/core/tests/extraction/fixtures/e8_eval/`, one per area (NLP, CV, IR, ML/general, DM-or-Agents), with a `SELECTION.md` documenting each pick and a review sign-off by someone other than the spec author
- **When** the eval test runs (opted in via `-m costly`, run during `/constellize:feature:verify`)
- **Then** all gates hold:
  - Topic precision: average ≥ 0.80 **AND** no paper below 0.60
  - Concept precision: average ≥ 0.70 **AND** no paper below 0.50
  - Concept recall (anti-gaming tripwire): average ≥ 0.50 (no per-paper floor)
- **And** per-paper scores are printed in the verification report for regression tracking
- **And** any change to `MIN_CONCEPT_CONFIDENCE` / `MIN_TOPIC_CONFIDENCE` re-runs this eval and must clear both precision targets and the recall tripwire
- **And** if one area cannot be hand-labeled confidently, the eval set drops to 4 papers and the missing area is recorded as a known gap in the verification record (no substitute from a covered area)

### AC-13: Re-ingestion is purge-then-rewrite with annotation guardrail
- **Given** Paper X exists in the graph with `extraction_incomplete=true`
- **When** the operator re-ingests Paper X without `--force-rewrite`
- **Then** if any `Problem` node attributable to Paper X has non-extraction incident edges, the orchestrator refuses with a non-zero exit code and lists the blocking edges
- **And** if no such edges exist, the paper's extraction footprint (Problems, ProblemMentions, and outbound HAS_TOPIC / INVOLVES_CONCEPT / DISCUSSES / EXTRACTED_FROM edges) is purged before re-running all three extractors
- **And** shared `Topic` and `ResearchConcept` nodes are NOT deleted
- **And** on a fully successful re-run, `extraction_incomplete` is cleared back to `false` and the per-extractor failure records are removed
- **And** `--force-rewrite` overrides the annotation guardrail and reports collateral data loss in the verify log

### AC-14: Completeness query contract
- **Given** the helper module `agentic_kg.queries.completeness`
- **When** any new or existing analytical Cypher query in the codebase filters on a `Paper`'s extracted entities (topics, concepts, problems)
- **Then** the query MUST either compose `complete_papers_filter()` to exclude `extraction_incomplete` papers, OR carry a code comment explicitly stating that it accepts partial-extraction papers and why
- **And** the helper exposes:
  - `complete_papers_filter() -> str` returning a Cypher fragment to exclude incomplete papers
  - `incomplete_papers_by_extractor(extractor: str) -> list[Paper]` for audit
  - `completeness_health_check() -> dict[str, float]` returning `% incomplete` per extractor
- **And** the verify gate runs a one-time codebase audit (grep-driven) to confirm every analytical query in `packages/core/src/agentic_kg/` either uses the helper or carries the documented exemption comment. Audit output is captured in the verification record.

### AC-15: Taxonomy version pinned per paper
- **Given** a paper is being ingested with `TopicExtractor` instantiated at batch start against `seed_taxonomy.yml`
- **When** `integrate_paper_entities` writes the Paper node
- **Then** the Paper node carries a `taxonomy_hash` property: sha256 of the canonically-serialized taxonomy used by the extractor for this batch (parse → sort keys → dump → hash, so cosmetic edits do not alter the hash)
- **And** a sanity-check query `MATCH (p:Paper) WHERE p.taxonomy_hash <> $current_hash RETURN p` returns exactly the papers ingested under a now-stale taxonomy
- **And** on re-ingestion via AC-13, the `taxonomy_hash` is updated to reflect the taxonomy snapshot used for the re-run

### AC-16: No regression in ingestion wall-clock
- **Given** a baseline per-paper ingestion wall-clock measured before this feature
- **When** the same paper is ingested after this feature
- **Then** the total wall-clock does not exceed `baseline + max(topic_latency, concept_latency)`
- **And** this is verified manually during the implementation's first staging run (logged, not gated — hard-coding a latency budget would be brittle)

## Technical Notes

- **Affected files:**
  - Create: `extraction/topic_extractor.py`, `extraction/concept_extractor.py`, `queries/completeness.py`, `extraction/fixtures/b3_deny_list.yml`, `tests/extraction/fixtures/e8_eval/*`, `tests/extraction/fixtures/e8_eval/SELECTION.md`
  - Modify: `extraction/schemas.py`, `extraction/prompts/templates.py`, `extraction/pipeline.py`, `extraction/kg_integration_v2.py`, ingest CLI (add `--force-rewrite` flag + re-ingestion purge path)
  - Touch: none in `problem_extractor.py`
- **Reuse:** `BaseLLMClient.extract()` (existing), `create_or_merge_research_concept` (E-2), `assign_entity_to_topic` (E-1), `link_paper_to_concept` (E-2), `link_problem_to_concept` (E-2)
- **Confidence thresholds:** `MIN_TOPIC_CONFIDENCE = 0.7`, `MIN_CONCEPT_CONFIDENCE = 0.7` — constants in `kg_integration_v2.py`, tunable.
- **Deny-list** (generic aliases excluded from B3): stored as a structured YAML fixture, `extraction/fixtures/b3_deny_list.yml`, not a bare code constant. Each entry carries provenance (`term`, `reason`, `added` date) so the list is self-documenting:
  ```yaml
  deny_list:
    - term: "model"
      reason: "ubiquitous; would link nearly every mention"
      added: "2026-05-14"
    - term: "network"
      reason: "ubiquitous in ML papers"
      added: "2026-05-14"
    # ...system, approach, method, algorithm, paper, work — same initial batch
  ```
  `link_problems_to_concepts` loads this into `DEFAULT_ALIAS_DENY_LIST` (a `frozenset` of the `term` values) at module import. YAML stays the source of truth in git — every change lands in PR review and git history.
- **Deny-list governance (pattern iii).** Adding a term post-V1 requires: (1) edit `b3_deny_list.yml` with a `reason` + `added` date, (2) open a PR — normal review, (3) the verify gate's calibration eval run over the 5 hand-labeled fixtures must demonstrate the new entry does not reduce concept recall below the AC-12 floor. Governance is tied to the verify gate, not left to ad-hoc edits.
- **Prompts are NOT chain-of-thought.** CoT would roughly 3× tokens for marginal precision gain on well-structured extraction tasks. Revisit in V2 if eval precision falls below targets.
- **Token budget.** V1 single-call per entity type means ~3× LLM calls per paper vs. current (problems-only). Offset by shorter per-call prompts for topic (abstract+intro only) and concept (abstract+intro+methods only) — overall cost increase estimated at ~1.5–2× per paper.

## Dependencies

- **E-1 (merged)** — provides `Topic` nodes, `assign_entity_to_topic`, seed taxonomy, taxonomy loader.
- **E-2 (merged)** — provides `ResearchConcept`, `create_or_merge_research_concept`, `link_paper_to_concept`, `link_problem_to_concept`.
- **`instructor` library** — already a tacit dependency via `llm_client.py` (still missing from `pyproject.toml`, flagged as a known issue in `activeContext.md`; this feature inherits that gap).
- **OpenAI API access** — no new keys, just more calls per paper.

## Open Questions

- ~~**Deny-list policy**~~ — RESOLVED (QA review): structured YAML fixture (`b3_deny_list.yml`) with per-entry provenance, loaded into `DEFAULT_ALIAS_DENY_LIST` at import. Additions follow governance pattern iii (PR + verify-gate calibration eval). A new DB was considered and rejected as overkill for a ~20-entry curated list; YAML-in-git keeps governance free via PR review.
- **Confidence thresholds** — per-extractor vs. a single shared value? V1 uses per-extractor but both default to 0.7. Revisit after first calibration run.
- **Methodology section coverage** — some papers don't segment a clear "methodology" section. Should the concept extractor also pull from "approach" / "model" / "experiments" sections? (Defer — implementation inspects the section segmenter's output on real papers and decides.)
- **Eval fixture maintenance** — who updates gold files when the taxonomy grows? (Answer: whoever grows the taxonomy; document in contributing guide.)
- **Eval set expansion to 10+ papers** — V1 ships 5 as a floor. Once labeling effort drops (tooling or contributor labeling), expand. Tracked as a follow-up feature, not V1 scope.
- **Retry policy** — inherit `ProblemExtractor.max_retries=3` for topic/concept extractors? Probably yes; defer to implementation.
- **V2 scope confirmation** — when `Model` and `Method` extractors are added, do they each get their own prompt or merge into `ExtractedEntities` as additional optional list fields? Recommend separate extractors (Route B symmetry), but defer.

## Review Record

Dual-persona review completed 2026-05-18. Decisions locked:

**Tech Lead review:**
- Taxonomy reload — **Option C**: per-instance snapshot via `pydantic.create_model`, instantiated once per batch.
- B3 alias pollution — **Option 1**: linker matches per-paper extracted aliases only, never merged-node accumulated aliases.
- Exception handling — silent-degrade-but-accountable: structured `ExtractionFailure` records + `Paper.extraction_incomplete` flag.

**QA review:**
- Q1 (eval-set design) — dual gates (average + per-paper floor) for precision; cross-area lock-in (1 each NLP/CV/IR/ML/DM-or-Agents) with `SELECTION.md` + external review; 5-paper floor with documented path to 10+.
- Q2 (partial-success persistence) — re-ingestion is purge-then-rewrite (Option 1a) with annotation guardrail + `--force-rewrite`; completeness is an explicit query-level contract via `queries/completeness.py` (no silent default), audited at verify.
- Q3 (taxonomy snapshot drift) — per-batch lifecycle pinned; `Paper.taxonomy_hash` (canonical sha256) added for staleness audit.
- Q4a (deny-list governance) — structured YAML fixture with per-entry provenance; additions follow PR + verify-gate calibration eval (pattern iii). New DB considered, rejected as overkill.
- Q4b (confidence gaming) — concept recall ≥ 0.50 added as an anti-gaming tripwire; threshold changes must re-clear precision + recall at verify.
