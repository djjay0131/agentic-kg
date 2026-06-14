# Feature: Entity Descriptions at Create-Time (E-6)

**Status:** VERIFIED
**Date:** 2026-06-14
**Implementation Date:** 2026-06-14
**Verification Date:** 2026-06-14
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** E-6
**Depends On:** E-1 (Topic), E-2 (ResearchConcept), E-3 (Model), E-4 (Method) — all four already carry a `description: Optional[str]` field wired into embedding generation. This feature populates the field at create time.
**Decoupled From:** E-7 (cross-entity normalization), E-8 (extraction prompt expansion V2), L-1 (low-cost SLM client).

## Problem

Four entity types (Topic, ResearchConcept, Model, Method) already have a `description: Optional[str]` field that the embedding pipeline uses as `"{name}: {description}"` for richer vectors. The field exists but is almost always NULL:

- **Topic**: only seed YAML entries have descriptions.
- **ResearchConcept**: only hand-curated nodes; ingestion-time dedup-merge creates nodes with `description=None`.
- **Model**: only the seed YAML's canonical entries.
- **Method**: no seed YAML at all (per E-4's design), essentially zero nodes have descriptions.

Bare-name embeddings collide aggressively in similarity search — "fine-tuning" and "transfer learning" land close together when both lack descriptions, polluting dedup decisions and ranking. The fix is structural: **generate a self-validated description at create time as part of the `create_or_merge_X` flow**, so the field is populated when the embedding gets computed, not as an afterthought.

Earlier framing considered an operator-triggered backfill CLI for existing NULL nodes. We pivoted away from that approach during spec review: we're early enough in the build (282 nodes / 151 edges in staging) that a one-time re-ingestion handles the existing cleanup case via E-8's already-shipped purge-then-rewrite path. The ongoing problem — new entities created with NULL descriptions — is the one worth solving structurally.

## Goals

- Add an opt-in `generate_description: bool = False` kwarg to `create_or_merge_topic`, `create_or_merge_research_concept`, `create_or_merge_model`, `create_or_merge_method`. **Default False** so existing call sites stay cost-neutral (no spec change for E-2 / E-3 / E-4 ingestion-path callers; E-8 V2 makes its own choice when it lands).
- When `generate_description=True` AND the caller didn't pass a `description` AND the matched/created node would otherwise persist with `description=None`: call the LLM with the self-validation Pydantic schema, accept the result only if all four self-validation gates pass, persist the description as part of the same `create_or_merge_X` flow.
- Self-validation via **four explicit boolean gates** in the structured response: `is_factually_grounded`, `is_concise`, `is_specific`, `is_not_tautological`. All four must be True. Per saved `feedback_llm_self_validation` preference.
- Operator-facing CLI commands (`create-concept`, `create-model`, `create-method`) flip `generate_description=True` by default and expose `--no-generate-description` to opt out (cost control during bulk-create scripts).
- The embedding generated for the node always uses whatever `description` value was finalized at the end of `create_or_merge_X` — including the LLM-generated one when self-validation passes. Existing embedding-pipeline behavior (`"{name}: {description}"` when description is present, name-only when None) is unchanged.
- On self-validation rejection: log WARN with the rejection reason, persist the node with `description=None`, do not block the create. The dedup-merge / new-node decision is unchanged.
- On LLM call failure: log WARN, persist the node with `description=None`, do not block the create.
- A testcontainers integration test demonstrating the create-time happy path + a self-validation smoke-test sentinel (the regression-catcher for "future refactor accidentally accepts failing responses").

## Non-Goals

- **A `backfill-descriptions` CLI.** Explicitly killed during spec review. The 282 staging nodes get cleaned up via re-ingestion through E-8 AC-13's purge-then-rewrite. Future NULL nodes are rare-and-recoverable: the operator can call `update_X(node.id, description=..., regenerate_embedding=True)` manually if they want to fill one in.
- **Auto-enabling `generate_description=True` for ingestion-path call sites.** E-2's `create_or_merge_research_concept` in `kg_integration_v2.py`, E-1's taxonomy loader, etc. continue to default False. They stay cost-neutral. E-8 V2 makes its own decision per the V2 spec when that lands.
- **Retry loops on rejected descriptions.** If self-validation rejects within a `create_or_merge_X` call, the description stays NULL for that call. The next call to `create_or_merge_X(name=...)` with `generate_description=True` re-attempts (the merged node still has `description=None`, so the helper runs again). The operator's recourse for a persistently-rejected node is to pass `--description "..."` explicitly. Same idempotent shape as the rest of our LLM-backed features.
- **External-source descriptions** (OpenAlex, Semantic Scholar, Wikipedia). Out of scope — LLM-generated is uniform and good enough for v1.
- **`description` on Author / Paper / Problem family.** Paper uses `abstract`; the Problem family uses `statement` / `canonical_statement` which are already substantive content. Author has no description field today and isn't vector-indexed. Each would be a separate spec.
- **LLM-as-judge with a separate critic call.** Self-validation happens in the same `instructor.extract()` call that produced the description.
- **Pre-engineered cost routing.** When the L-1 SLM client lands, the existing `BaseLLMClient` injection point in `create_or_merge_X` picks up the cheaper provider transparently. No spec change required.
- **Embedding regeneration for nodes whose description was previously set.** `create_or_merge_X` already handles embedding correctly on first persistence; the description-generation hook does not change embedding semantics.

## User Stories

- **As an operator running `agentic-kg create-method --name "contrastive learning"`**, I want a sensible description generated and embedded automatically, so I don't have to write one.
- **As an operator running a bulk script** that creates 200 Methods, I want `--no-generate-description` so I can skip the LLM cost and add descriptions later if I want.
- **As an E-8 V2 spec author (future)**, I want explicit control over whether description-generation runs per extracted entity, so I can manage the per-paper LLM call budget.
- **As a researcher**, I want vector search across Topic / Concept / Model / Method to return semantically relevant results, because the embeddings are computed from richer text than the bare name.

## Design Approach

### Self-Validation Response Schema

```python
class DescriptionWithSelfCheck(BaseModel):
    """LLM response schema for create-time description generation.

    The LLM produces both the description and its own self-evaluation
    against four explicit gates. Acceptance requires ALL True.
    """
    description: str = Field(..., min_length=20, max_length=400)

    # Self-validation gates — the LLM checks its own output in the same call.
    is_factually_grounded: bool = Field(
        description="True if the description is grounded in well-known facts, not speculation."
    )
    is_concise: bool = Field(
        description="True if the description is 1-2 sentences."
    )
    is_specific: bool = Field(
        description="True if the description names what distinguishes this entity, not generic platitudes."
    )
    is_not_tautological: bool = Field(
        description="True if the description doesn't just rephrase the name."
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="If any gate above is False, name which one and why.",
    )

    @property
    def passes_self_validation(self) -> bool:
        return all([
            self.is_factually_grounded, self.is_concise,
            self.is_specific, self.is_not_tautological,
        ])
```

### New Helper Module

`packages/core/src/agentic_kg/knowledge_graph/description_generation.py`:

```python
async def generate_description_with_self_check(
    *,
    entity_type: Literal["topic", "concept", "model", "method"],
    name: str,
    aliases: list[str],
    llm_client: BaseLLMClient,
) -> Optional[str]:
    """Generate a self-validated description. Returns the description
    string when all gates pass; None on validation rejection OR LLM
    failure. Never raises (caller continues with description=None)."""
    ...
```

### Wiring Into `create_or_merge_X` — Sync + Async Sibling Methods

Each of the four existing methods gets a new kwarg with default False. **Per QA Q2 review**, the async LLM helper requires an async caller, but `create_or_merge_X` is sync today and the FastAPI handlers that call it run inside a live event loop (so `asyncio.run` would crash). Solution: **add an async sibling per entity type**.

```python
# Sync version (unchanged contract; new kwarg, no LLM call when False)
def create_or_merge_topic(
    self, name: str, description: Optional[str] = None, ...,
    generate_description: bool = False,
    llm_client: Optional[BaseLLMClient] = None,
) -> tuple[Topic, bool]:
    """Sync caller. If generate_description=True is passed, raises
    NotImplementedError — use ``acreate_or_merge_topic`` from sync
    callers via ``asyncio.run(...)`` or directly from async callers."""
    ...

# Async sibling
async def acreate_or_merge_topic(
    self, name: str, description: Optional[str] = None, ...,
    generate_description: bool = False,
    llm_client: Optional[BaseLLMClient] = None,
) -> tuple[Topic, bool]:
    """Async caller. Supports description generation."""
    ...
```

Behavior contract:

1. Existing dedup / merge logic runs first (shared by sync and async siblings). We know whether we're creating new or merging.
2. After the merge/create decision, if the resulting node would persist with `description IS NULL` AND `generate_description=True` was passed AND the caller didn't supply an explicit description AND `llm_client` is non-None:
   - The **async sibling** awaits `generate_description_with_self_check(...)`.
   - The **sync method** raises `NotImplementedError` to make the contract loud (callers must pick a path).
   - If the helper returns a string, use it as the description for this call.
   - If it returns None (self-validation rejection or LLM failure), leave description as None and proceed.
3. The rest of the existing `update_X` / `create_X` path runs unchanged. Embedding generation uses the final description value.

Each call site picks the method appropriate to its context:

- **CLI handlers**: sync code, use `asyncio.run(repo.acreate_or_merge_X(..., generate_description=True))`. The CLI is a fresh process with no live loop, so `asyncio.run` works.
- **FastAPI handlers**: async code, use `await repo.acreate_or_merge_X(...)` directly. No `asyncio.run`, no crash.
- **Existing ingestion-path callers (`kg_integration_v2.py`, taxonomy loader, etc.)**: sync code, continue to use the sync `create_or_merge_X` without the kwarg. Default behavior preserved.

### CLI Defaults

The three existing operator-facing create commands (`create-concept`, `create-model`, `create-method`) flip `generate_description=True` by default. New flag `--no-generate-description` opts out:

```
agentic-kg create-method --name "fine-tuning"
   → calls asyncio.run(repo.acreate_or_merge_method(
       name="fine-tuning", generate_description=True, llm_client=...))
   → LLM call + self-validation → description persisted (if gates pass)

agentic-kg create-method --name "X" --no-generate-description
   → calls repo.create_or_merge_method(name="X", generate_description=False)
   → no LLM call; node persists with description=None
```

For ingestion-path callers (`kg_integration_v2.py`, taxonomy loader, E-8 V1 extractor wiring): no changes. They continue to call the sync `create_or_merge_X` without the kwarg, so `generate_description=False` applies and behavior is unchanged.

**Missing `OPENAI_API_KEY` — silent fallback (Tech Lead Q3 review).** The CLI handlers call `get_openai_client()` to obtain the LLM client. If the OpenAI client cannot be constructed (typically because `OPENAI_API_KEY` is not set in the environment), the CLI:

1. Catches the construction error.
2. Logs a one-line WARN to stderr: `"OPENAI_API_KEY not set; skipping description generation. Pass --description to provide one explicitly."`
3. Falls back to the sync `create_or_merge_X` path with `generate_description=False`.
4. Completes the entity creation (the primary task) successfully and exits 0.

Rationale: hard-failing on missing API key would block the common case of "create one node by hand" for operators in environments without LLM credentials. The WARN log is the signal that description generation didn't happen; the operator's primary intent (create the node) still succeeds.

### Prompt Templates

Stored alongside E-8 prompts in `packages/core/src/agentic_kg/extraction/prompts/templates.py`:

```python
DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1 = (
    "You are a research librarian. Output factual, concise descriptions of "
    "research entities (topics, concepts, models, methods). After generating "
    "the description, rigorously self-evaluate it against the four boolean "
    "criteria in the response schema. If any criterion is False, populate "
    "rejection_reason."
)

DESCRIPTION_GENERATION_USER_PROMPT_TEMPLATE_V1 = (
    "Write a 1-2 sentence factual description of the {entity_type} "
    '"{name}"{aliases_hint}. Focus on what it IS and what distinguishes '
    "it from similar {entity_type}s. Do NOT just rephrase the name."
)
```

`{aliases_hint}` expands to `" (also known as: a, b, c)"` when aliases are present, empty string otherwise (first three aliases max).

## Sample Implementation

```python
# packages/core/src/agentic_kg/knowledge_graph/description_generation.py

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.llm_client import BaseLLMClient
from agentic_kg.extraction.prompts.templates import (
    DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1,
    DESCRIPTION_GENERATION_USER_PROMPT_TEMPLATE_V1,
)

logger = logging.getLogger(__name__)


class DescriptionWithSelfCheck(BaseModel):
    description: str = Field(..., min_length=20, max_length=400)
    is_factually_grounded: bool
    is_concise: bool
    is_specific: bool
    is_not_tautological: bool
    rejection_reason: Optional[str] = None

    @property
    def passes_self_validation(self) -> bool:
        return all([
            self.is_factually_grounded, self.is_concise,
            self.is_specific, self.is_not_tautological,
        ])


async def generate_description_with_self_check(
    *,
    entity_type: Literal["topic", "concept", "model", "method"],
    name: str,
    aliases: list[str],
    llm_client: BaseLLMClient,
) -> Optional[str]:
    """Generate a self-validated description. Returns the description
    string when all gates pass; None on validation rejection OR LLM
    failure. Never raises — the caller proceeds with description=None."""
    aliases_hint = (
        f" (also known as: {', '.join(aliases[:3])})" if aliases else ""
    )
    user_prompt = DESCRIPTION_GENERATION_USER_PROMPT_TEMPLATE_V1.format(
        entity_type=entity_type, name=name, aliases_hint=aliases_hint,
    )

    try:
        response = await llm_client.extract(
            prompt=user_prompt,
            response_model=DescriptionWithSelfCheck,
            system_prompt=DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1,
        )
    except Exception as e:
        logger.warning(
            "LLM call failed during description generation for %s '%s': %s",
            entity_type, name, e,
        )
        return None

    result = response.content
    if not result.passes_self_validation:
        logger.warning(
            "Self-validation rejected description for %s '%s': %s",
            entity_type, name,
            result.rejection_reason or "(no reason given)",
        )
        return None

    return result.description


# packages/core/src/agentic_kg/knowledge_graph/repository.py (additions to each create_or_merge_X)

def create_or_merge_method(
    self,
    name: str,
    description: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    method_type: Optional[str] = None,
    threshold: Optional[float] = None,
    embedding: Optional[list[float]] = None,
    generate_description: bool = False,        # NEW (E-6)
    llm_client: Optional[BaseLLMClient] = None, # NEW (E-6) — injected by CLI
) -> tuple[Method, bool]:
    # ... existing dedup logic ...

    # E-6: if description still None after merge/create decision and
    # generate_description is enabled, attempt LLM generation.
    if (
        generate_description
        and (description is None or description == "")
        and llm_client is not None
    ):
        # Note: import locally so existing callers don't pay an import cost.
        from agentic_kg.knowledge_graph.description_generation import (
            generate_description_with_self_check,
        )
        import asyncio
        generated = asyncio.run(generate_description_with_self_check(
            entity_type="method", name=name,
            aliases=list(aliases or []), llm_client=llm_client,
        ))
        if generated is not None:
            description = generated

    # ... existing create / merge / update path uses final description value ...
```

## Edge Cases & Error Handling

### Self-validation rejection
- **Scenario:** LLM returns `description="X is a method."`, `is_specific=False`, `rejection_reason="too generic, doesn't distinguish from other methods"`.
- **Behavior:** Helper returns None. `create_or_merge_X` proceeds, persisting the node with `description=None`. WARN log carries the rejection reason.
- **Test:** Mock LLM client returns a failing response; assert no description set; assert WARN log content; assert the merge / create still completes successfully.

### LLM call exception
- **Scenario:** OpenAI returns 5xx; instructor exhausts retries.
- **Behavior:** Helper catches and returns None. `create_or_merge_X` proceeds with `description=None`. WARN log identifies the failed entity.
- **Test:** Mock client raises; assert no crash, no description, node still persisted, WARN logged.

### Caller passes explicit description AND generate_description=True
- **Scenario:** `create_or_merge_method(name="X", description="An explicit description.", generate_description=True)`.
- **Behavior:** Caller-provided description wins. No LLM call. Node persists with the caller's description.
- **Test:** Assert llm_client.extract is never called.

### `generate_description=True` but no `llm_client` injected
- **Scenario:** Caller asks for generation but didn't supply a client (programming error in a future call site).
- **Behavior:** Helper is not invoked; node persists with `description=None`. WARN log notes the missing client.
- **Test:** Assert no crash, no description, WARN logged with a clear message.

### Generation succeeds for a node that ends up being a merge (not a new node)
- **Scenario:** `create_or_merge_method(name="fine-tuning", generate_description=True)` is called. Dedup finds an existing Method matching the embedding. The existing node has `description=None`. The newly-generated description applies to the existing node, not a freshly created one.
- **Behavior:** Existing merge path runs `update_method(existing.id, description=generated, ...)`. Embedding is regenerated to incorporate the new description. `created=False` is returned (this was a merge, not a new node).
- **Test:** Pre-create a Method with description=None; call create_or_merge with the same name + `generate_description=True`; assert the existing node now has the generated description.

### Generated description identical to existing (no-op merge case)
- **Scenario:** Two consecutive `create_or_merge_method(name="X", generate_description=True)` calls — first creates with a generated description, second merges into it.
- **Behavior:** Second call sees the merged node already has a description; the helper does NOT run (`if description is None` is False because the existing description carries through the merge step). No second LLM call, no cost.
- **Test:** Call twice; assert llm_client.extract called only once total.

### Embedding generation fails after description was set
- **Scenario:** Description gets generated and accepted. The downstream embedding call (`generate_method_embedding(name, description)`) raises (OpenAI embedding service down).
- **Behavior:** Per existing `create_or_merge_method` behavior, the node persists with description but without an embedding. Existing AC-13-equivalent (E-4 / E-3 pattern) applies: WARN logged.
- **Test:** Patch the embedding generator to raise; assert description set but embedding=None.

### Aliases list is empty / very long
- **Scenario:** `aliases=[]` or `aliases=[15 entries]`.
- **Behavior:** Empty list → `aliases_hint = ""`. Long list → first three only in the prompt (avoids prompt bloat for entities that have accumulated alias drift).
- **Test:** Unit test the prompt construction with each shape.

## Acceptance Criteria

### AC-1: `DescriptionWithSelfCheck` schema
- **Given** `description_generation.py` is imported
- **When** a `DescriptionWithSelfCheck` is constructed with all four gates True
- **Then** `passes_self_validation` returns True.
- **And** when any single gate is False, `passes_self_validation` returns False.
- **And** the description field carries Pydantic `min_length=20`, `max_length=400` matching the entity model bounds.

### AC-2: `generate_description_with_self_check` happy path
- **Given** a mocked LLM client that returns a `DescriptionWithSelfCheck` with all gates True
- **When** `generate_description_with_self_check(entity_type="method", name="contrastive learning", aliases=["InfoNCE"], llm_client=mock)` is called
- **Then** the returned value is the description string.
- **And** `llm_client.extract` was called once with the description prompt and the response_model.

### AC-3: Self-validation rejection returns None + WARN
- **Given** a mocked LLM client that returns `is_specific=False` and `rejection_reason="too generic"`
- **When** the helper runs
- **Then** the return value is None.
- **And** a WARN log contains the rejection reason.

### AC-4: LLM call exception returns None + WARN (never raises)
- **Given** a mocked LLM client that raises during `extract()`
- **When** the helper runs
- **Then** the return value is None.
- **And** a WARN log identifies the failed entity by name.
- **And** the helper does not re-raise.

### AC-5: `create_or_merge_X` opt-in semantics (NEW kwarg, sync method)
- **Given** each of the sync methods `create_or_merge_topic`, `create_or_merge_research_concept`, `create_or_merge_model`, `create_or_merge_method`
- **When** they are called WITHOUT `generate_description` (existing call sites)
- **Then** behavior is unchanged from the pre-E-6 baseline (no LLM call, no description generation).
- **And** when called with `generate_description=True`, the method raises `NotImplementedError` with a message pointing the caller to the async sibling (per QA Q2 review — the sync path cannot run the async helper safely).
- **And** when called with `generate_description=False` (the only supported sync path), no LLM call happens regardless of whether `llm_client` was passed.

### AC-6: `acreate_or_merge_X` generates description when enabled (async sibling)
- **Given** a fresh KG, a mocked LLM returning a passing `DescriptionWithSelfCheck`, and `await repo.acreate_or_merge_method(name="X", generate_description=True, llm_client=mock)`
- **When** the call runs
- **Then** the returned Method has the LLM-generated description.
- **And** the Method's embedding was generated from `"{name}: {description}"` (verified by the existing `generate_method_embedding` mock or by inspecting the embedding arg).
- **And** `created=True` is returned.
- **And** when called with `generate_description=True` AND an explicit description, the explicit description wins (no LLM call).
- **And** when called with `generate_description=True` AND `llm_client=None`, no LLM call happens (WARN logged); node persists with description=None.

### AC-7: Merge path applies generated description to existing node
- **Given** a pre-existing Method with `name="fine-tuning"` and `description=None`
- **When** `await repo.acreate_or_merge_method(name="fine-tuning", generate_description=True, llm_client=mock)` is called and the dedup matches
- **Then** the existing Method's description is updated with the LLM-generated value.
- **And** the embedding is regenerated to incorporate the new description.
- **And** `created=False` is returned.
- **Rationale (Tech Lead Q1 review):** The merge-override is intentional. An operator who deliberately wants the merged node to stay NULL must pass an explicit empty/sentinel description; the default is "fill in missing values from any caller that can provide them" — matches the existing "fill description / method_type from incoming when existing is None" semantics in E-4.

### AC-8: CLI `--no-generate-description` flag opts out
- **Given** the installed CLI
- **When** `agentic-kg create-method --name "X" --no-generate-description` is invoked
- **Then** the underlying `create_or_merge_method` call uses `generate_description=False`.
- **And** no LLM call happens.
- **And** when the flag is omitted, the default behavior at the CLI is `generate_description=True`.

### AC-9: Testcontainers integration test (happy path)
- **Given** a fresh testcontainers Neo4j, a mocked `BaseLLMClient.extract` returning a passing `DescriptionWithSelfCheck`
- **When** `create_or_merge_method(name="contrastive learning", generate_description=True, llm_client=mock)` runs
- **Then** the Method persisted in Neo4j has the LLM-generated description.
- **And** the Method's embedding is non-None.

### AC-10: Self-validation smoke-test sentinel
- **Given** a mocked LLM client returning `is_factually_grounded=False`
- **When** `create_or_merge_method(name="X", generate_description=True, llm_client=mock)` runs
- **Then** the persisted Method has `description IS NULL`.
- **And** `passes_self_validation` was evaluated (the WARN log contains the rejection reason).
- **And** the merge / create result is still returned (the failure is recovery, not crash).
- **Rationale:** Pins the contract that self-validation failure does NOT write a half-baked description.

### AC-11: Existing functionality untouched
- **Given** the existing test suite
- **When** E-6 is merged
- **Then** all existing tests pass with zero modifications.
- **And** every existing `create_or_merge_X` caller (in `kg_integration_v2.py`, taxonomy loader, E-8 V1 wiring, etc.) continues to omit `generate_description` and gets the unchanged behavior.

### AC-12: CLI silent-fallback when `OPENAI_API_KEY` is missing
- **Given** the `OPENAI_API_KEY` environment variable is not set
- **When** `agentic-kg create-method --name "X"` runs (default would be `generate_description=True`)
- **Then** the CLI prints a one-line WARN to stderr: `"OPENAI_API_KEY not set; skipping description generation. Pass --description to provide one explicitly."`
- **And** the sync `create_or_merge_method` path runs with `generate_description=False`.
- **And** the entity is created successfully with `description=None`; exit code 0.
- **And** when `--no-generate-description` is passed explicitly, no warning is emitted (the operator already opted out).
- **Rationale (Tech Lead Q3 review):** Hard-failing on missing API key would block the common case of "create one node by hand" for operators without LLM credentials. The WARN log preserves the signal that description generation didn't happen while completing the operator's primary task.

### AC-13: Embedding pipeline incorporates description when present
- **Given** a Method created via `create_or_merge_method(..., generate_description=True)` with a passing LLM response
- **When** the embedding is generated as part of the create flow
- **Then** the embedding call sees the formed `"{name}: {description}"` text.
- **And** when `generate_description=False` (or self-validation rejects), the embedding falls back to name-only as today.

## Technical Notes

- **Affected files:**
  - Create: `knowledge_graph/description_generation.py`, `tests/knowledge_graph/test_description_generation.py`, `tests/integration/test_e6_done_demo.py`, `tests/test_cli_generate_description.py` (or extend existing test_cli_methods/models/concepts files).
  - Modify: `knowledge_graph/repository.py` (kwarg + helper invocation on 4 `create_or_merge_X` methods), `cli.py` (3 CLI commands gain `--no-generate-description` and a `llm_client` injection at the handler level), `extraction/prompts/templates.py` (add the 2 new prompt constants).
  - Touch: none in API routers, agents, ingestion, or the data acquisition layer.
- **Reuse:** `BaseLLMClient` from E-8 V1. `instructor` already in `pyproject.toml`. Existing `generate_X_embedding(name, description)` helpers — no changes.
- **No new dependencies.**
- **CLI injection of `llm_client`:** the existing `create-method` / `create-model` / `create-concept` handlers do not currently construct an LLM client. They will need to call `get_openai_client()` (the existing singleton from E-8 V1) before invoking `create_or_merge_X`. Documented in *Sample Implementation*.
- **`asyncio.run` inside a synchronous repository method:** the `create_or_merge_X` methods are sync; the helper is async. The simplest wiring uses `asyncio.run(helper(...))` at the call site inside the repo method. Concern: if the caller is itself inside a running event loop (e.g., E-8 V2's async pipeline), `asyncio.run` raises. Resolution: implementation can detect a running loop via `asyncio.get_event_loop().is_running()` and either use `asyncio.create_task` or refactor the helper into a sync wrapper. The simplest move for v1 is the sync wrapper, since `create_or_merge_X` is sync. Documented as an implementation choice; tests cover both code paths if a running loop is detected.

## Dependencies

- **Existing:** `BaseLLMClient` with `extract()` and `instructor` structured-output integration (E-8 V1 shipped this).
- **Existing:** `update_X` / `create_X` paths within each `create_or_merge_X` method (E-1 / E-2 / E-3 / E-4 shipped these).
- **None new.**

## Open Questions

- **Auto-on-create for ingestion-path callers (E-8 V2).** When E-8 V2 is spec'd, the author decides whether to flip `generate_description=True` in their extractor-driven `create_or_merge_X` calls. Per-paper cost implication: ~4 extra LLM calls (one per entity). Out of this spec; flagged for E-8 V2.
- **`description` on Author / Paper / Problem family.** Each is its own spec if pursued — different field semantics (Author has none, Paper uses `abstract`, Problem uses `statement`). Not in v1.
- **External-source descriptions.** OpenAlex / Semantic Scholar / Wikipedia for richer descriptions per entity type. Out of v1; tracked as a possible follow-up if the LLM-generated quality bar isn't enough.
- **Cost-aware model routing (L-1).** When L-1 ships, the existing `get_openai_client()` injection point swaps to the cheaper provider. No spec change required.

## Review Record

The interview answers and the mid-draft pivot are recorded below.

**User-answered decisions:**

- **Q1 (Tech Lead, answered: b) — Strategy.** Decision: LLM-generated descriptions over field-addition-only or external-source approaches. (The field-addition piece of E-6 was already shipped via E-1 / E-2 / E-3 / E-4; the remaining gap is populating values for nodes that have the field but no value.)
- **Q2 (Tech Lead, answered: a, later revised) — Pull vs push.** Initial decision: pull-only via a backfill CLI. **Reversed during mid-draft review** when the user observed that we're early enough in the build (282 nodes / 151 edges in staging) that a one-time re-ingestion handles the existing-NULL cleanup case via E-8 AC-13's purge-then-rewrite. The ongoing problem (future creates) is where the structural fix belongs — create-time, opt-in.
- **Q3 (QA, answered: b) — Cost safety model.** Decision: standard CLI safety knobs. **Made moot by the Q2 pivot** — the backfill CLI doesn't exist in the final shape. The cost-control story is now: `--no-generate-description` opt-out flag at the CLI; ingestion-path callers default to `generate_description=False`. Saved as backlog item **L-1** (Local / low-cost SLM for narrow tasks) per the user's framing that cost should drive model selection, not gate the work.
- **Q4 (QA, answered: d — user-proposed) — Quality validation.** Decision: LLM self-validation via explicit gate criteria baked into the Pydantic response schema (`is_factually_grounded`, `is_concise`, `is_specific`, `is_not_tautological`). Single LLM call; one structured response; accept only if all gates pass. Documented as a recurring architectural preference in saved memory `feedback_llm_self_validation`. Will apply to future LLM-touching features in this project.

**Mid-draft pivot — backfill → create-time:**

The user challenged the backfill framing after the draft was largely written: "The spec says the main goal is to backfill descriptions that do not exist. Will we need the backfill feature down the road or is this a cleanup feature? If it's a cleanup feature we are still early enough in the build to just repopulate the graph." Honest answer: backfill was solving a problem (large NULL-description corpus) that we don't yet have at scale. Re-ingestion via E-8 AC-13 handles the existing 282-node cleanup; the ongoing problem (new creates) belongs at create-time. The spec was restructured around opt-in create-time generation with a per-call-site `generate_description: bool = False` kwarg, defaulting False for cost-neutrality on existing call sites and flipping True at operator-facing CLIs. The backfill CLI, the `list_X_missing_description` repo helpers, and the `backfill_descriptions` orchestrator were all dropped from the final shape.

**Phase 7-8 dual-persona review (4 questions):**

- **Q1 (Tech Lead, answered: a) — Merge-override.** Decision: accept the merge case where an LLM-generated description gets applied to an existing node with `description=None` (even if a previous operator deliberately left it NULL). Matches the existing "fill description / method_type from incoming when existing is None" semantics in E-4's `create_or_merge_method`. Documented in AC-7 with rationale.
- **Q2 (QA, answered: b) — Sync vs async strategy.** Decision: add async sibling methods `acreate_or_merge_X` for each entity type. The sync versions stay unchanged in their default-False path but raise `NotImplementedError` when `generate_description=True` is passed. CLI handlers use `asyncio.run(repo.acreate_or_merge_X(...))`; FastAPI handlers use `await repo.acreate_or_merge_X(...)`. Documented in *Wiring Into `create_or_merge_X`* and AC-5 / AC-6.
- **Q3 (Tech Lead, answered: b) — CLI silent fallback when `OPENAI_API_KEY` is missing.** Decision: CLI handlers attempt `get_openai_client()`; on failure (no API key), print a one-line WARN to stderr and fall back to the sync path with `generate_description=False`. Operator's primary task (creating the node) still succeeds. Documented in *CLI Defaults* and AC-12.
- **Q4 (QA, answered: a) — Test surface for self-validation prompt effectiveness.** Decision: accept the prompt-effectiveness gap as a known limitation. AC-10's mocked sentinel catches the most common regression class ("future refactor accepts failing responses"); production prompt quality will be caught by operators reviewing descriptions. A costly+integration eval is premature until quality complaints actually materialize.
