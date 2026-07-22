---
title: E-7 · Cross-entity normalization
parent: Design
nav_order: 7
---

# E-7 · Cross-entity normalization

{: .label .label-green }
VERIFIED

**Backlog ID:** E-7 · **Depends on:** E-2/E-3/E-4 (Concept/Model/Method),
E-6 (self-validation pattern), E-8 V1+V2 (extractors) · **Spec:**
[`cross-entity-normalization.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/cross-entity-normalization.md)

## Why

The E-8 V2 extractors run the Concept, Model, and Method extractors against the
same paper text in parallel. Their prompts try to stay in lane ("a Method is not
a Model"), but real papers blur the lines. The same surface form — the canonical
example is *"attention mechanism"* — lands as both a `ResearchConcept` and a
`Method`, and the integrator writes **both**. That gives the graph two nodes for
one idea, with separate `DISCUSSES` / `APPLIES_METHOD` edges from the same paper.
It pollutes vector search and breaks queries like "papers that use attention
mechanism," which return only half the matches because the rest are filed under
the other kind. E-7 collapses the collision *per paper, before write*.

## What shipped

A normalization step that runs between extraction and integration. For each
paper it detects cross-kind surface collisions across
[Concept]({{ site.baseurl }}/reference/entity-catalog#researchconcept-e-2) /
[Model]({{ site.baseurl }}/reference/entity-catalog#model-e-3) /
[Method]({{ site.baseurl }}/reference/entity-catalog#method-e-4), routes each one
through a single self-validating LLM call, and drops the losing kind(s) so only
the surviving extraction reaches the writers. Every collision is recorded in a
`normalization_audit` JSON property on the `Paper` node. Topic is deliberately
excluded — it is closed-set (E-1) and connects via `BELONGS_TO`, so it never
collides with the free-text kinds.

## Design decisions

**Self-validation gates baked into the response — no critic call.** The routing
LLM returns a single `DisambiguationDecision` that carries both the pick
(`picked_kind`, `confidence`) *and* its own two-gate self-evaluation
(`is_grounded_in_paper_context`, `is_specific_to_one_kind`). Acceptance requires
**both gates True AND `confidence ≥ 0.7`** — one `instructor.extract()` call, no
second adversarial pass. This follows the saved `feedback_llm_self_validation`
memory (from E-6): quality gating rides inside the structured response rather
than a separate critic request, keeping cost at ≤1 LLM call per ambiguous pair.

**Reject keeps BOTH extractions — recall over eager merging.** When the router
*can't* confidently decide (a gate returns False, confidence is below 0.7, the
LLM raises, or it picks a kind that wasn't in the pair) the normalizer changes
nothing — both extractions survive and the integrator writes both nodes exactly
as it did pre-E-7. The reasoning (spec TL Q1 review): the router only fires on
already-suspicious cases, so when *it* also can't decide, discarding a real
extraction throws away signal. The audit row marks the case unresolved
(`picked=null`, `dropped_kinds=[]`) so an operator can re-ingest later or merge by
hand. Drops happen **only** on a clean accept.

**All-in trigger, cheap signals first.** A pair is flagged ambiguous if ANY of:
exact name match (case-insensitive), alias overlap (canonical-in-aliases or
alias-vs-alias), or embedding cosine ≥ `0.85`. The two cheap signals run first
with no I/O; the embedding scan runs *only* over extractions the cheap pass
didn't already pair, and caches each name's vector per paper. Most papers have
zero or one collision, so the expensive layer rarely fires.

**Untrusted-data prompt hardening.** Paper text is treated as untrusted. Quoted
snippets and the wider excerpt are wrapped in pseudo-XML `<paper-excerpt>` /
`<quote-{kind}>` delimiters, and the system prompt carries an explicit security
clause telling the model to treat everything inside those blocks as data, never
instructions. Combined with the `Literal["concept","model","method"]` on
`picked_kind`, this bounds the blast radius of an obvious injection. Documented
as mitigation, not prevention.

## How it works

- **Normalizer module:**
  [`cross_entity_normalizer.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/cross_entity_normalizer.py)
  — `DisambiguationDecision` (Pydantic response model with the
  `passes_self_validation` property), `detect_ambiguous_pairs`
  (`_cheap_collisions` → `_embedding_collisions`), `disambiguate_pair` (one
  routing call), and the async entry point `normalize_cross_entity`.
- **Prompts:** `DISAMBIGUATION_SYSTEM_PROMPT_V1` (definitions + rules + the
  untrusted-data security clause) and `DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1` in
  [`prompts/templates.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/prompts/templates.py).
- **Where it runs:** the ingestion loop in
  [`ingestion.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/ingestion.py)
  calls `normalize_cross_entity(...)` right after extraction and **before**
  `integrate_paper_entities`. It mutates `extraction_result.concepts/.models/
  .methods` **in place**, so the integrator's existing writer blocks simply loop
  over whatever survives — no writer change needed.
- **Audit persistence:**
  [`kg_integration_v2.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/kg_integration_v2.py)
  receives the `NormalizationResult`, serializes it with `audit_to_json`, and
  writes `SET p.normalization_audit = $audit_json` only when non-empty. Clean
  papers leave the property `NULL`, so the operator query
  `MATCH (p:Paper) WHERE p.normalization_audit IS NOT NULL` returns exactly the
  papers that hit a collision.
- **Injected clients — the L-1 swap point:** both the `EmbeddingService` and the
  `BaseLLMClient` are constructed once per ingestion run and injected. When L-1
  (low-cost SLM) lands, swapping in a cheaper client is a one-line change at the
  construction site — no normalizer code touched.
- **Operator toggle:** a `normalize_cross_entity_collisions` flag (default `True`)
  threads through the CLI / ingestion so a run can opt out.

For the colliding node types in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** seven `test_cross_entity_*` modules under
  `packages/core/tests/extraction/` — schemas, cheap detection, embedding
  detection, the excerpt composer, prompts, routing, and end-to-end
  normalize + integrator wiring (accept-drops-loser, reject-keeps-both, triple
  collision = one call, zero-collision = no call, embedder-failure degrades only
  the fuzzy layer, audit lands on the Paper node).
- **Calibration caveat:** per the spec's Open Questions, no hand-labeled
  cross-entity eval set exists yet, so routing precision/recall is unverified
  beyond mocked unit tests. First real-data shakedown is the deferred follow-up.
- **Status:** VERIFIED.

### Spec-vs-shipped divergences

- **Call site moved up a level.** The spec diagram had the *integrator* call
  `normalize_cross_entity` as its first step. Shipped, the **ingestion loop**
  makes the call and passes the `NormalizationResult` into
  `integrate_paper_entities` purely for audit persistence. Same net effect (the
  in-place mutation lands before any write), one level higher.
- **`disambiguate_pair` returns `(kind, reason)`.** The spec sample returned a
  bare `Optional[EntityKind]`; the shipped helper returns a tuple so the real
  rejection reason (LLM error text, which gate failed, the confidence value, or
  "picked kind not in pair") is threaded into the audit instead of a fixed
  string.
- **AC-18 resolved toward keep-both.** The spec's AC-18 body says an out-of-pair
  pick should drop "both kinds," which contradicts the governing TL Q1
  "keep both on reject" decision. Shipped code follows keep-both
  (`dropped_kinds=[]`), resolving the spec's internal inconsistency.
- **Per-kind quotes folded into the detected-as block.** The user prompt renders
  each kind's `quoted_text` inside its `<quote-{kind}>` tag within the kinds
  block, rather than as a separate "excerpts grounding each extraction" section.
  The pseudo-XML delimiters and security contract are preserved.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) ·
  [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
- Builds on: [E-2]({{ site.baseurl }}/design/e2-research-concepts) ·
  [E-3]({{ site.baseurl }}/design/e3-model-entities) ·
  [E-4]({{ site.baseurl }}/design/e4-method-entities)
- Deferred: cross-paper canonicalization, a labeled eval set, and an audit
  retention/TTL policy (spec Open Questions)
