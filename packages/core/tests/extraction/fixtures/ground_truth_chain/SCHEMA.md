# Gold-file schema — `paper_<slug>.gold.yml`

One file per paper per reviewer. Same schema in all three subdirectories; only
`reviewer:` and the presence of `disagreements:` differ.

The file is a **superset**: keys in the "scored" block are exactly the ones
`test_e8_eval.py` reads, so a future runner consumes them unchanged. Keys in the
"unscored" block carry the human-review payload the brief asks for. The scorer reads
`g["canonical"]` / `g.get("acceptable_aliases")` per entry and ignores sibling keys, so
`quoted_text` and `confidence` can sit **inline on each entity** rather than in a separate
grounding block.

## Template

```yaml
# ---------------------------------------------------------------- identity
paper_id: "doi:10.1007/978-3-031-19433-7_39"   # "doi:<doi>" | "arxiv:<id>"
slug: "cskg"
title: "CS-KG: A Large-Scale Knowledge Graph of Research Entities and Claims in Computer Science"
doi: "10.1007/978-3-031-19433-7_39"
s2_paper_id: null            # Semantic Scholar paperId; null until looked up
year: 2022
source_pdf: "ground-truth-papers/Large_Scale_KG_CS.pdf"
reviewer: "human"            # "human" | "claude" | "reconciled"
reviewed_at: "2026-07-27"

# ------------------------------------------------- scored (e8_eval-compatible)
expected_topics:
  - name: "Knowledge Graphs" # CLOSED SET — must appear in seed_taxonomy.yml
    level: "subtopic"        # domain | area | subtopic. NOT scored; for humans.

expected_concepts:
  - canonical: "scientific knowledge graph"
    acceptable_aliases: ["scholarly knowledge graph", "scientific knowledge graphs"]
    quoted_text: "..."       # unscored; >=10 chars, verbatim
    confidence: 0.9          # unscored; reviewer's own confidence, 0-1

expected_models: []          # same entry shape; [] is valid but see "Empty lists"

expected_methods:
  - canonical: "entity linking"
    acceptable_aliases: ["entity resolution"]
    quoted_text: "..."
    confidence: 0.8

# ------------------------------------------------------------------ unscored
problems:                    # ExtractedProblem is a LIST per paper, not one
  - statement: "..."         # >=20 chars (importer's own floor)
    quoted_text: "..."       # >=10 chars
    involves_concepts: ["scientific knowledge graph"]

cites_within_set: ["cskg", "fact_completion"]   # slugs only, this set only

notes: "..."                 # ambiguity, low confidence, judgment calls
```

`reconciled/` files add two keys:

```yaml
disagreements:
  - field: "expected_methods"
    item: "entity linking"
    human: "present"
    claude: "absent"
    resolution: "kept"
    rationale: "Sec. 3.2 names it explicitly; Claude's miss is a recall gap."

acceptable_extras:           # correct-if-emitted, not required. No scorer reads this.
  topics:
    - name: "Ontology Engineering"
      why: "Not the paper's focus, but it does build a 179-relation ontology."
  concepts: []
  models: []
  methods: []
```

## Field rules

### `expected_topics`

- `name` **must** be one of the 29 names in
  `packages/core/src/agentic_kg/knowledge_graph/data/seed_taxonomy.yml`. Do not invent,
  abbreviate, or pluralize. `TopicExtractor` binds a pydantic `Literal` to that snapshot.
- The prompt instructs "the SMALLEST number… usually one or two", hard cap 5. Match that
  restraint — over-labeling topics inflates apparent recall and depresses real precision.
- `level` is recorded but **never read** by `topic_precision`. A level mismatch is not
  penalized.
- If the paper fits nothing cleanly, an empty list is correct; the extractor is told to
  return one rather than guess.

### `expected_concepts` / `expected_models` / `expected_methods`

- `canonical` — prefer the paper's own terminology. Lowercase unless the term is a proper
  noun or established acronym.
- `acceptable_aliases` — surface forms of **the same concept** as they appear in this
  paper, plus obvious variants. Include singular/plural: matching is exact string
  equality, so `"knowledge graphs"` will not match `"knowledge graph"`.

  **Do not pad this list with merely related terms.** Precision counts predictions that
  hit any gold surface form, so every alias widens the hit set; recall needs only one
  match per gold entry and is unaffected. Padding aliases inflates precision without
  improving anything real.
- `quoted_text` — verbatim from the paper, ≥10 characters, and it must make clear *why*
  the entity is in the paper. No entity without a grounding quote.
- `confidence` — the reviewer's own 0–1 confidence that this belongs in the answer key.
  Not the importer's. Low-confidence entries are the interesting ones at reconciliation.

### Model vs. Method vs. Concept

The three extractors have overlapping-looking briefs; these are the importer's own lines,
and gold must follow them or the diff measures our inconsistency rather than the
importer's:

| | Test | Examples | Not |
|---|---|---|---|
| **Model** | A *named artifact with weights* and an architecture family | BERT, GPT-2, ResNet-50, T5, CLIP | "transformer architecture", "attention mechanism", "CNNs" — these are concepts |
| **Method** | A named *recipe* — something you do, often to a model | fine-tuning, contrastive learning, RLHF, LoRA | "training", "evaluation", "running experiments" — too generic |
| **Concept** | A technique, theory, framework, or named idea the paper relies on | attention mechanism, retrieval augmented generation | "machine learning", "neural network", "deep learning", "AI", "model", "algorithm" — too generic |

Model entries may also carry `architecture`, `model_type`, `year_introduced`; method
entries may carry `method_type` (`training` | `evaluation` | `data_processing`). All
optional, all unscored — fill them when the paper states them, leave them out otherwise.

### `acceptable_extras` (reconciled files only)

The scored lists are binary: an entry either carries a recall obligation, or its absence
turns a matching prediction into a false positive. Some items are honestly neither —
correct if the importer emits them, not required of it. Those go here instead of being
forced into one side of the binary.

- **No scorer reads this key.** It is documentation for whoever interprets the diff. If
  the eval runner is written, the right treatment is to subtract these names from the
  precision *denominator* — never to count them as recall misses.
- Buckets mirror the scored keys (`topics`, `concepts`, `models`, `methods`), plus any
  ad-hoc bucket a paper needs (`ground_truth_chain/reconciled/paper_cskg.gold.yml` adds
  `named_resources` for an open schema question). Each entry is `{name, why}`; `why` is
  required — an extra without a reason is just an entry someone couldn't decide on.
- Use it for: a generic surface form of a scored entry (`"filtering"` where
  `"entity filtering"` is scored); a defensible topic the paper isn't really about; a real
  step that shouldn't be a recall obligation; entities blocked on a schema decision.
- Do **not** use it to avoid a judgment call on a well-grounded entity. If the rubric
  answers the question, score it and record the reasoning in `disagreements`.

### Empty lists

`[]` is structurally valid, and a paper that genuinely uses no named models should have
`expected_models: []`. Be aware of the consequence: `concept_recall` and
`model_method_recall` return **1.0 vacuously** when gold is empty, so an empty list
silently passes its recall gate. Use `[]` because it is true, never because labeling was
hard — and say so in `notes` if it was a close call.

### `cites_within_set`

Slugs from this set only, per `README.md`'s edge list. Curation used OpenAlex; the
importer uses Semantic Scholar. Where they differ, record what the importer will populate
and note the discrepancy.

## Validation checklist

Before a file is considered done:

- [ ] every `expected_topics[].name` appears verbatim in `seed_taxonomy.yml`
- [ ] every concept/model/method entry has a `quoted_text` of ≥10 characters
- [ ] every quote is verbatim — copy-paste from the PDF text, not retyped
- [ ] no generic terms from the "Not" column above
- [ ] plural/singular variants present in `acceptable_aliases` where relevant
- [ ] `problems[].statement` ≥20 chars, `problems[].quoted_text` ≥10 chars
- [ ] `cites_within_set` uses slugs, and only slugs from this set
- [ ] `reviewer` matches the subdirectory the file is in
