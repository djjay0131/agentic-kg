# E-8 Eval Set — Paper Selection

**Status:** PLACEHOLDER — to be populated during `/constellize:feature:verify`.

This directory holds the 5 hand-labeled fixture papers used to gate E-8
extraction precision and concept recall. The spec (AC-12) requires:

- **Exactly one paper per area:**
  1. NLP
  2. CV (Computer Vision)
  3. IR (Information Retrieval)
  4. ML / general
  5. DM (Data Mining) or AI Agents
- **External review:** Selection must be reviewed by someone other than the
  spec author (cheapest path: knowledge-steward persona during the next
  `/constellize:memory:update` after fixtures land).
- **Documentation per paper:** For each paper, this file (or a sibling
  `SELECTION_<area>.md`) must capture *why this paper* — what made it
  cleanly labelable, any caveats, the source URL.

If a confident hand-label cannot be produced for one of the five areas,
the eval set drops to 4 papers and the missing area is flagged as a known
gap in the verification record. No substitute from a covered area is
allowed.

## Fixture file shape

Each paper ships as two files in this directory:

```
paper_<slug>.txt          # abstract + introduction + methodology concatenated
paper_<slug>.gold.yml     # hand-labeled expected output (schema below)
```

`paper_<slug>.gold.yml` schema:

```yaml
paper_id: "arxiv:2401.12345"
title: "Paper title"
expected_topics:
  # Each must be one of the 29 seed taxonomy nodes.
  - name: "NLP"
    level: "area"
  - name: "Large Language Models"
    level: "subtopic"
expected_concepts:
  - canonical: "attention mechanism"
    # Any of these surface forms matching the extractor's output counts.
    acceptable_aliases: ["self-attention", "scaled dot-product attention"]
  - canonical: "retrieval augmented generation"
    acceptable_aliases: ["RAG"]
# E-8 V2 additions (AC-17). Empty lists are valid; the eval test
# short-circuits the relevant precision / recall calculation per paper
# when its expected list is empty, but the implementation phase MUST
# fill these in for the verify gate to run meaningfully.
expected_models:
  - canonical: "BERT"
    acceptable_aliases: ["bert-base", "bert-large", "BERT-base-uncased"]
expected_methods:
  - canonical: "fine-tuning"
    acceptable_aliases: ["finetuning", "supervised fine-tuning"]
```

## AC-12 / AC-17 gates

The eval is opt-in via `pytest -m costly`. Gates (V1 + V2 combined):

- **Topic precision:** average ≥ 0.80 AND no paper below 0.60.
- **Concept precision:** average ≥ 0.70 AND no paper below 0.50.
- **Concept recall (anti-gaming tripwire):** average ≥ 0.50 (no per-paper
  floor). Catches confidence-threshold gaming — recall floor must hold any
  time `MIN_TOPIC_CONFIDENCE` or `MIN_CONCEPT_CONFIDENCE` changes.
- **Model precision (V2):** average ≥ 0.70 AND no paper below 0.50.
- **Method precision (V2):** average ≥ 0.65 AND no paper below 0.45.
- **Combined Model+Method recall tripwire (V2):** average ≥ 0.45. Same
  governance: threshold changes must re-clear precision + recall.

V2 floors are *draft* (set slightly below V1's). The implementation phase
runs the extractors against the 5-paper set once before verify; if the
draft floors are infeasible, the implementation report flags the gap
and the verify gate decides whether to lower with documented
justification, tune prompts, or defer. See spec AC-17 for the
calibration step contract.

Per-paper scores are printed in the verify report for regression tracking.

## Selected papers (to fill in at verify)

| Area | Paper title | arxiv / DOI | Why this paper | Reviewer | Caveats |
|------|-------------|-------------|----------------|----------|---------|
| NLP  |             |             |                |          |         |
| CV   |             |             |                |          |         |
| IR   |             |             |                |          |         |
| ML   |             |             |                |          |         |
| DM/Agents |        |             |                |          |         |
