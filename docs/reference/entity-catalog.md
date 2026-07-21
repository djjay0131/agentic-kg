---
title: Entity Catalog
parent: Reference
nav_order: 1
---

# Entity Catalog

The authoritative definition of every **node type** in the knowledge graph.
Each entry follows a fixed template: *Definition*, *Purpose*, *Key attributes*,
*Relationships*, and *Notes* (vocabulary style, lifecycle, provenance). Downstream
docs should link here rather than redefine these types.

Source of truth: `packages/core/src/agentic_kg/knowledge_graph/models/entities.py`
and the constraints/indexes in `schema.py`. Uniqueness constraints exist for every
node label below.

- [Core research nodes](#core-research-nodes) — `Problem`, `ProblemMention`, `ProblemConcept`
- [Bibliographic nodes](#bibliographic-nodes) — `Paper`, `Author`
- [Classification & concept nodes](#classification--concept-nodes) — `Topic`, `ResearchConcept`, `Model`, `Method`
- [Metadata & operational nodes](#metadata--operational-nodes) — `SchemaVersion`, review queue

---

## Core research nodes

### `Problem`

**Definition.** A research problem as a first-class entity — an open question,
challenge, or research direction extracted from a paper.

**Purpose.** The central modeling bet of the project: problems are nodes, not
strings. Everything else (papers, concepts, models) exists to describe, source,
or relate problems.

**Key attributes.** `id` (UUID); `statement` (≥ 20 chars); `status`
(`open` / `in_progress` / `resolved` / `deprecated`); structured lists of
`assumptions`, `constraints`, `datasets`, `metrics`, `baselines`; `evidence`
(source paper) and `extraction_metadata` (provenance); `embedding` (1536-dim);
`version` and timestamps.

**Relationships.** `EXTRACTED_FROM` → `Paper`; problem-to-problem edges
`EXTENDS` / `CONTRADICTS` / `DEPENDS_ON` / `REFRAMES` → `Problem`;
`BELONGS_TO` → `Topic`.

**Notes.** A `resolved` or `deprecated` problem is *required* to carry
`evidence` with a `source_doi` (enforced by a model validator) — you cannot
close a problem without pointing at the paper that closed it.

### `ProblemMention`

**Definition.** A paper-specific mention of a research problem — the problem
exactly as it appears in one paper.

**Purpose.** Preserves original wording and per-paper context (section, quoted
text, paper DOI) so the canonical layer can deduplicate without losing
provenance.

**Key attributes.** `id`; `statement` (as stated in this paper); `paper_doi`
(must start with `10.`); `section`; `quoted_text`; the same structured metadata
lists as `Problem`; matching fields (`concept_id`, `match_confidence`,
`match_score`, `match_method`); review-workflow fields (`review_status`,
`reviewed_by`, `agent_consensus`); `embedding`.

**Relationships.** `INSTANCE_OF` → `ProblemConcept` (its canonical concept).
Its source paper is referenced by the `paper_doi` property.

**Notes.** Mentions are matched to concepts by vector similarity and routed by
confidence (HIGH auto-link / MEDIUM single-agent / LOW multi-agent consensus /
escalation to human review).

### `ProblemConcept`

**Definition.** The canonical representation of a research problem that may be
mentioned across many papers.

**Purpose.** Deduplicates mentions into one durable node with an AI-synthesized
canonical statement and aggregated metadata, so the graph reflects *distinct*
problems rather than every restatement.

**Key attributes.** `id`; `canonical_statement` (≥ 20 chars, LLM-synthesized);
`status`; aggregated `assumptions` / `constraints` / `datasets` / `metrics`;
`verified_baselines` vs. `claimed_baselines`; synthesis provenance
(`synthesis_method`, `synthesis_model`, `synthesized_by`, `human_edited`);
aggregation stats (`mention_count`, `paper_count`, `first/last_mentioned_year`);
`embedding`; refinement tracking; `version`.

**Relationships.** `INSTANCE_OF` ← `ProblemMention` (inbound); `INVOLVES_CONCEPT`
→ `ResearchConcept`; `BELONGS_TO` → `Topic`.

**Notes.** `first_mentioned_year ≤ last_mentioned_year` is validated. Baselines
are split into reproducible (`verified`) vs. unverified (`claimed`).

---

## Bibliographic nodes

### `Paper`

**Definition.** A scientific paper — the source node most other entities are
extracted from.

**Purpose.** Anchors provenance for the whole graph and forms the citation
network.

**Key attributes.** `doi` (primary key, must start with `10.`); `title`;
`authors`; `venue`; `year` (optional); `abstract`; external identifiers
(`arxiv_id`, `openalex_id`, `semantic_scholar_id`); `pdf_url`, `full_text`;
`ingested_at`. **Citation-graph fields (E-5):** `is_stub` (placeholder created
from another paper's reference list before ingestion — monotone true → false),
`citation_count` (inbound `CITES`), `reference_count` (outbound `CITES`).

**Relationships.** `AUTHORED_BY` → `Author`; `CITES` → `Paper` (self-reference);
`RESEARCHES` → `Topic`; `DISCUSSES` → `ResearchConcept`; `USES_MODEL` → `Model`;
`APPLIES_METHOD` → `Method`; `EXTRACTED_FROM` ← `Problem`.

**Notes.** `title` min length was relaxed (10 → 2) and `year` made optional to
admit reference-list stubs with partial metadata.

### `Author`

**Definition.** A paper author.

**Purpose.** Attribution and author-level graph queries.

**Key attributes.** `id`; `name`; `affiliations`; `orcid` (validated to start
`0000-`); `semantic_scholar_id`.

**Relationships.** `AUTHORED_BY` ← `Paper` (carries `author_position`).

---

## Classification & concept nodes

### `Topic`  *(E-1)*

**Definition.** A research topic or area, forming a three-level hierarchy
(`domain → area → subtopic`).

**Purpose.** The **closed-set controlled vocabulary** for classifying problems
and papers. See the dedicated [Topic Taxonomy](topic-taxonomy) page.

**Key attributes.** `id`; `name`; `description` (feeds richer embeddings);
`level` (`domain` / `area` / `subtopic`); `parent_id` (None for root domains);
`source` (`manual` / `openalex` / `migrated` / `llm_proposed`); `openalex_id`;
`embedding`; denormalized `problem_count` / `paper_count`.

**Relationships.** `SUBTOPIC_OF` → `Topic` (parent); `BELONGS_TO` ←
problem-side nodes; `RESEARCHES` ← `Paper`.

**Notes.** Domain-level topics must not have a parent (validated). New topics
enter only via the seed taxonomy — extraction cannot invent off-taxonomy topics.

### `ResearchConcept`  *(E-2)*

**Definition.** A named intellectual building block — e.g. "attention
mechanism", "transfer learning", "graph neural networks".

**Purpose.** Captures the ideas papers are about, independent of the specific
problem or model.

**Key attributes.** `id`; `name`; `description`; `aliases` (surface forms);
`embedding`; denormalized `mention_count` (`INVOLVES_CONCEPT`) and `paper_count`
(`DISCUSSES`).

**Relationships.** `INVOLVES_CONCEPT` ← `ProblemConcept`; `DISCUSSES` ← `Paper`;
`BELONGS_TO` → `Topic` (optional).

**Notes.** *Open-set* vocabulary — any name may become a concept; duplicates are
merged at **0.90** cosine similarity.

### `Model`  *(E-3)*

**Definition.** A named ML model / architecture — e.g. "BERT", "GPT-4",
"ResNet-50".

**Purpose.** Tracks the concrete models papers use, benchmark against, or build
on.

**Key attributes.** `id`; `name`; `description`; `aliases`; `architecture`
(transformer / cnn / rnn …); `model_type` (language_model / vision_model …);
`year_introduced`; `introducing_paper_doi`; **`is_canonical`** (curated seed,
write-protected during merges); `embedding`; denormalized `usage_count`.

**Relationships.** `USES_MODEL` ← `Paper`.

**Notes.** *Hybrid* vocabulary: open-set, but ~19 seed entries are flagged
`is_canonical=True` and cannot be renamed or downgraded by incoming
non-canonical names. Dedup threshold **0.95** cosine (+ canonical seed).

### `Method`  *(E-4)*

**Definition.** A research method / methodology — e.g. "fine-tuning",
"contrastive learning".

**Purpose.** Captures *how* work is done, distinct from the `Model` it is done
with.

**Key attributes.** `id`; `name`; `description`; `aliases` (max 20);
`method_type` (training / evaluation / data_processing / optimization …);
`embedding`; denormalized `usage_count`.

**Relationships.** `APPLIES_METHOD` ← `Paper`.

**Notes.** *Pure open-set* (like `ResearchConcept`, not `Model`) — method names
are conceptual phrases, not identities, so no canonical-protection machinery.
Dedup threshold **0.90** cosine.

---

## Metadata & operational nodes

### `SchemaVersion`

Records the applied Neo4j schema version (unique constraint on `version`,
currently **7**). Managed by `SchemaManager`; not part of the research graph.

### Review-queue nodes (operational)

`PendingReview` and its embedded structures (`SuggestedConceptForReview`,
`AgentContextForReview`) back the human review queue for disputed
mention→concept matches (`review_queue.py`). They carry SLA, priority, and
resolution fields and sit outside the core research schema.
