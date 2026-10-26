---
title: RQ1 Annotation Guide
nav_exclude: true
---

# RQ1 annotation guide — extraction reliability

**Status:** active harness; annotation set partially populated
**Schema:** `rq1-annotation/v1`
**Code:** `packages/core/src/agentic_kg/evaluation/`
**Entry point:** `python -m agentic_kg.evaluation.rq1`

This guide defines how RQ1 (proposal §V-H1) measures extraction reliability:
precision, recall and F1 of extracted **problems, assumptions, constraints,
datasets, metrics and baselines** against human annotations, overall and
stratified by section type, with inter-annotator agreement.

The harness is deliberately independent of `kg_eval`. It runs on a default
install with no model call, no external service, and no optional extra, so CI
can grade a frozen prediction fixture deterministically.

## What is already annotated (honest coverage)

The eight-paper `ground_truth_chain` corpus is committed under
`packages/core/tests/extraction/fixtures/ground_truth_chain/`. Today:

| Papers | Annotation available | Answer key used |
|---|---|---|
| `cskg`, `cskg2` | human + Claude independent reviews, reconciled | `reconciled` |
| `empire`, `fact_completion` | human review (Claude review exists for `fact_completion`) | `human` |
| the other four | none | ungraded |

All existing gold files annotate **problems** only. Assumptions, constraints,
datasets, metrics and baselines have no gold records yet, so their metrics are
reported as honest nulls — not as `0.0`, and not as false positives. Adding
those fields to a gold file automatically makes them graded; no code change is
needed. Predictions on an unannotated `(paper, field)` cell are excluded from
precision rather than counted as hallucinations.

The converter emits one **coverage** record per paper/reviewer. That is what
distinguishes "the reviewer looked for datasets and found none" from "nobody
looked". A field is graded only when a reviewer marked it as attempted.

## File format: versioned JSONL

One JSON object per line. Every record carries
`"schema_version": "rq1-annotation/v1"`. Unknown versions are rejected at load
time. Two record types share the file.

### Annotation record

```json
{
  "schema_version": "rq1-annotation/v1",
  "record_type": "annotation",
  "paper_doi": "10.1007/978-3-031-19433-7_39",
  "paper_slug": "cskg",
  "section": "abstract",
  "field_type": "problem",
  "text": "Existing scholarly knowledge graphs cover only a small number of articles...",
  "aliases": [],
  "char_span": {"start": 225, "end": 446},
  "evidence_quote": "A common limitation of these solutions is that they address...",
  "implicit": false,
  "annotator_id": "human",
  "provenance": "gold:human",
  "present": true,
  "notes": null
}
```

| Field | Required | Meaning |
|---|---|---|
| `paper_doi` | yes | DOI of the paper. Case-folded on load; `slug` is optional metadata. |
| `section` | no | Section type (`abstract`, `introduction`, `methods`, `experiments`, ...). Defaults to `unknown`. Normalized to lower snake case. |
| `field_type` | yes | One of `problem`, `assumption`, `constraint`, `dataset`, `metric`, `baseline`. |
| `text` | yes | The annotated statement/name. This is what the matcher compares. |
| `aliases` | no | Other surface forms of the same item, e.g. `"entity resolution"` for `"entity merging"`. |
| `char_span` | no | Half-open `{start, end}` offsets into the paper text. Optional; string matching still works without it. |
| `evidence_quote` | no | Verbatim grounding quote. The converter fills this from the existing `quoted_text`. |
| `implicit` | no | Use `true` for an assumption that is inferred rather than stated. Missed implicit assumptions are their own error bucket. |
| `annotator_id` | yes | Stable id (`human`, `claude`, `reconciled`, or a person's name). |
| `provenance` | no | Where the record came from (`gold:human`, `prediction:importer-output`, ...). |
| `present` | no | Default `true`. Set `false` only for an explicit negative annotation. |
| `notes` | no | Free text for judgment calls. |

### Coverage record

```json
{
  "schema_version": "rq1-annotation/v1",
  "record_type": "coverage",
  "paper_doi": "10.1007/978-3-031-19433-7_39",
  "paper_slug": "cskg",
  "annotator_id": "human",
  "fields": ["problem"]
}
```

Add a field to `fields` when the annotator actually reviewed that field. Add it
even when the result is zero items — that is a measured zero, not a gap. Do not
add a field the annotator did not review.

## How to annotate

1. Work from the committed `paper_<slug>.txt` fixture (the extractor's own
   input) or the PDF; the committed text is authoritative for quotes and spans.
2. For each item, decide the **field type** using the extraction schema
   (`packages/core/src/agentic_kg/extraction/schemas.py`):
   - **problem** — the research problem/limitation/open question, not a method
     or a result.
   - **assumption** — a premise the work relies on. Mark `implicit: true` when
     the paper never states it in one sentence.
   - **constraint** — a computational/data/methodological/theoretical limit.
   - **dataset** — a named dataset/corpus used or released.
   - **metric** — a named evaluation measure (F1, BLEU, nDCG, ...).
   - **baseline** — a named comparison system, not a generic family.
3. Copy the **evidence quote verbatim**. Whitespace and ligatures are allowed
   to differ from the PDF transcription; the converter locates the quote
   modulo whitespace and NFKC. If the quote cannot be located the span is left
   null rather than guessed.
4. Record the **section** the quote comes from. Section is a stratification
   label, not a matching key: a correct item in the wrong section is still a
   true positive, and it is also counted in the `wrong_section` error bucket.
5. Add **aliases** only for genuine surface variants of the same item. Do not
   pad aliases with related-but-different terms; aliases widen the precision
   hit set.

### What counts as a match

Deterministic by default, in this priority order:

1. **Normalized string** — NFKC (ligatures fold), case-fold, whitespace
   collapse, Unicode dashes unified, edge punctuation stripped. Two items match
   when their canonical/alias surface sets intersect. No stemming, no edit
   distance.
2. **Span overlap** — when both items have a char span, IoU must be at least
   `--span-iou` (default `0.5`).
3. **Embedding similarity** — only when `--embedding-similarity` is passed;
   then cosine similarity must be at least `--embedding-threshold` (default
   `0.8`). CI never enables it, and the default install does not include a
   model.

Alignment is one-to-one and greedy over the best pair, with deterministic tie
breaks. A duplicate correct prediction is a false positive after the first, by
design.

### Error buckets

| Bucket | Meaning |
|---|---|
| `missed_implicit_assumption` | An implicit assumption in gold was not extracted. |
| `missed_field` | Any other gold item not extracted. |
| `wrong_section` | Matched item attributed to a different section than gold. |
| `bad_evidence_link` | Matched item whose evidence quote/span does not overlap gold's grounding, or has no evidence when gold does. |
| `hallucinated_field` | A prediction with no gold counterpart in a graded cell. |

A pair can appear in more than one bucket (for example, wrong section *and* bad
evidence link).

## Metrics

- **Micro** pools TP/FP/FN across papers within a slice.
- **Macro** computes P/R/F1 per paper, then averages; papers with no denominator
  for a metric are skipped for that metric and the report says so.
- Slices: overall, each field type, each section type.
- **95% CIs** are a clustered bootstrap over papers (the independent unit),
  seeded (`--seed`, default `20261026`) and reproducible. Fewer than 20 defined
  resamples withholds the interval rather than printing a misleading one.
- **Agreement**: pairwise Cohen's kappa and nominal Krippendorff's alpha over
  the union of items in cells both reviewers covered. Uncovered cells are
  missing, never negative labels. With fewer than two annotators, null.

## Commands

Evaluate:

```bash
python -m agentic_kg.evaluation.rq1 \
  --gold packages/core/tests/evaluation/fixtures/rq1/gold.jsonl \
  --pred packages/core/tests/evaluation/fixtures/rq1/predictions.jsonl \
  --corpus packages/core/tests/evaluation/fixtures/rq1/corpus.json \
  --out report.json --markdown report.md
```

`--gold` and `--pred` also accept a directory, a `.gold.yml` file, or the
importer-output YAML directory (converted on the fly). `--pred` may be a run id
resolved under `--runs-dir` (`<id>.jsonl` or `<id>/predictions.jsonl`), so a
live extraction run can be graded without copying it into the fixture tree.

Regenerate the frozen fixtures:

```bash
python -m agentic_kg.evaluation.rq1 convert \
  --gold-yaml packages/core/tests/extraction/fixtures/ground_truth_chain \
  --out packages/core/tests/evaluation/fixtures/rq1/gold.jsonl

python -m agentic_kg.evaluation.rq1 convert \
  --importer-output docs/ground-truth/importer-output \
  --out packages/core/tests/evaluation/fixtures/rq1/predictions.jsonl
```

or `make rq1-eval`. The CI job
`.github/workflows/rq1-eval.yml` reruns the harness on the frozen fixtures with
no LLM call and uploads `rq1-report.json` / `rq1-report.md`.

## Provenance-scoring bug (`kg_eval`)

The brief names a bug in `kg_eval.metrics._unsupported_assertions`: it counted
only `SUPPORTS` refs while KGIS producers emit `DERIVED_FROM`, so real
extraction output scored 100% unsupported. **That bug is already fixed at this
repository's pinned `agentic-kgis` v0.6.0** (`285ca1d`): the module now uses
`_GROUNDING_RELATIONSHIPS = {SUPPORTS, DERIVED_FROM}`, and the upstream fix
landed in agentic-kgis #60/#62. There is therefore no separate upstream PR to
open. `packages/core/tests/migration/evaluation/test_kg_eval_grounding_support.py`
pins the resolved dependency's behaviour, including `SUPPORTS` vs
`DERIVED_FROM` and the deliberate exclusion of `CONTRADICTS`/`CONTEXTUALIZES`.
That test runs in the migration CI job, which installs the `migration` extra;
default-install jobs skip it cleanly.

The RQ1 harness does not depend on `kg_eval`, so this bug cannot distort its
numbers either way.

## Open questions / limitations

- Only **problems** have gold annotations today. The five structured fields are
  supported by the schema and metrics but have no data. Annotating them is the
  next data task, not a code task.
- Only four of the eight corpus papers have any annotation. The report states
  this explicitly (corpus size vs gold papers vs graded papers) and never
  pretends the other four were measured.
- Section inference is heading-based. A quote in an unrecognised section falls
  back to `unknown`; section-stratified numbers reflect that rather than a
  guess.
- The existing gold files label only abstract/introduction/methods/experiments
  scope. Items in related work or conclusions are deliberately absent; the
  harness cannot recover a label that was never in the answer key.
