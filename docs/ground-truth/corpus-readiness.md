---
title: Corpus Readiness
nav_exclude: true
---

# Corpus readiness: when is a paper valid for a recall comparison?

**Created:** 2026-09-18
**Companion to:** [`segmenter-findings.md`](segmenter-findings.html)
**Harness:** `scripts/measure_segmentation.py --corpus committed`
**Guard:** `packages/core/tests/extraction/test_segmenter_frozen_corpus.py`

A recall number is a ratio between what gold says is in a paper and what the
importer found. It is only interpretable when the *input the importer read* is
the input gold describes. Every SEG-n root cause breaks exactly that
precondition, which is why a migration-quality claim made before they are fixed
would measure segmentation, not extraction, and report the result as extractor
recall.

This document records how readiness is measured, what it measured, and what it
cannot see.

> ## Read this before quoting a number
>
> **"VALID" does not mean "correctly segmented".** All four keep-list types are
> concatenated into one blob before the extractors see it, so a span labelled
> `introduction` that is really the methods section keeps every character and
> lands in the same prompt. Character recall is therefore **structurally blind
> to confusion between two keep-list types**.
>
> Measured: `cskg` — one of the two reconciled-gold papers — produces **no
> `methods` section at all**. Its 14,988-character gold methods span
> `[6728,21716)` is absorbed into a 20,271-character `introduction`, and the
> paper still scores **99.9% recall**.
>
> So the readiness table reports **two** verdicts, and neither substitutes for
> the other:
>
> | | means | count |
> |---|---|---|
> | **CONCAT** | valid for a legacy-vs-new comparison over the concatenated extractor blob — what today's pipeline actually feeds the extractors | **7/8** |
> | **TYPED** | *also* produces every wanted section type gold records for that paper, under the right label | **4/8** |
>
> Three of the seven CONCAT-valid papers are TYPED-invalid: `cskg` and
> `fact_completion` (missing `methods`), `kg_validation_hitl` (missing
> `experiments`). All three are SEG-5 misses — a heading named after the
> paper's contribution, or an over-strict anchor.
>
> **This is harmless today and will not stay harmless.** For a single
> concatenated prompt, intra-keep-list confusion costs nothing. It starts
> costing the moment anything becomes section-aware — per-section prompting,
> or **SEG-7's keep-list inversion, which is the stated next step**. Quote
> CONCAT for character-recall comparisons. Do not quote it for "segmentation is
> correct".

---

## The two corpora, and why there are two

| | PDF corpus | Committed corpus |
|---|---|---|
| Input | `ground-truth-papers/*.pdf` | `fixtures/ground_truth_chain/paper_<slug>.txt` |
| In the repo | **no** (`.gitignore:75`) | **yes** |
| Needs PyMuPDF | yes | no |
| Runs in CI | **no** | **yes** |
| Pinned by | `scripts/seg_baseline.json` | `fixtures/segmenter_frozen/<slug>.json` |
| Sees | the whole paper, furniture and all | only gold-wanted prose |

### What `paper_<slug>.txt` actually is

Not raw PDF text. `scripts/segment_ground_truth.py` produced each file from
**hand-verified section boundaries**, keeping only the sections the importer
wants (`abstract` / `introduction` / `methods` / `experiments`, minus those a
given paper genuinely lacks) and concatenating them with `\n\n`.

That makes it a *sharper* instrument than raw text for one specific question,
and a blind one for others.

**Sharper, because the null hypothesis is gone.** Every character in the file
is already gold-wanted. So any character the segmenter fails to route into a
keep-list section is a segmenter recall failure with no competing explanation —
no "it was in the references", no "the keep-list dropped it". The measurement is
an idempotence check: *given text whose boundaries are known correct, does the
segmenter reproduce them?*

**Blind, because gold already threw the hard cases away.** Page furniture,
running headers, reference lists, and the 237 KB of body that the 94-page survey
contributes to the PDF corpus are all absent. Failure modes that live in that
material — SEG-6's unbounded length being the clearest — cannot be reproduced
here and still need the local PDF run.

Both arms are kept. Neither replaces the other.

---

## The readiness criteria

Three per-paper conditions, all computed by `recall_validity()` in
`scripts/measure_segmentation.py`:

1. **An abstract is present.** Gold gives all eight papers one, and it is the
   densest gold-entity source in a paper. A missing abstract guarantees a recall
   shortfall that is a segmentation failure wearing an extractor's clothes.
2. **Gold char recall ≥ 95 %.** See above: a shortfall is dropped content, full
   stop. The floor sits below 100 % because `_extract_sections` consumes the
   heading line itself — a few hundred structurally unrecoverable characters per
   paper.
3. **No single section holds ≥ 90 % of the kept text** (where more than one
   section is expected). This is SEG-4's under-segmentation signature. One
   mislabelled span absorbing the rest of the paper *keeps* its characters, so
   criteria 1 and 2 are both blind to it.

Those three produce the **CONCAT** verdict. A fourth, reported separately as
**TYPED**, is what they cannot see:

4. **Every wanted section type gold records for the paper is produced, under
   the right label.** Read from `segment_ground_truth.py`'s per-paper `wanted`
   list, not assumed to be all four — `kg_construction_survey` is a 94-page
   survey with no methods and no experiments sections and gold says so, and
   `llm_ontology_gen`'s approach lives in its experiments section. Holding
   either to four types would manufacture a failure out of an honest answer.

`test_missing_types_agrees_with_ingestions_own_warning` keeps criterion 4 in
step with `ingestion._missing_wanted_sections`, the warning production already
emits. Before this was added, the repo shipped two instruments that disagreed:
the runtime warning fired on papers the readiness report called VALID.

### A correction to the "0 of 8" framing

The pre-work analysis reported **0 of 8** papers valid. Measured on the
committed corpus, the count at the start of this work was **1 of 8**:
`kg_construction_survey` passes all three criteria (99.6 % recall, an abstract,
no swallowed span).

The two numbers are not in conflict — they are computed over different corpora.
On the **PDF** corpus that same paper feeds **244,742 characters** into a single
extractor prompt against a 7,152-character gold, which is a disqualifying defect
under any sane bound. That defect is SEG-6, it is real, and it is invisible to
the committed corpus because gold discarded the 94 pages that cause it.

Recorded rather than smoothed over: the committed corpus is not a superset of
the PDF corpus, and a paper can be valid on one and not the other.

---

## Measured: state at the start of this work (`7108c7d`)

`python scripts/measure_segmentation.py --corpus committed --entities`

```
=== Committed corpus (gold text -> extractor input) ===
  paper                        gold     kept   recall  secs  abstract
  cskg                       24,959   23,505    94.2%     2  NO
  cskg2                      35,485   23,317    65.7%     1  NO
  kg_construction_survey      7,140    7,115    99.6%     2  yes
  llm_ontology_gen           17,353   15,680    90.4%     2  NO
  fact_completion            33,352   31,960    95.8%     2  NO
  kg_validation_hitl         34,744   33,298    95.8%     4  NO
  hypothesis_generation      43,818   42,122    96.1%     3  NO
  empire                     20,385   15,260    74.9%     2  NO

  abstracts found: 1/8
```

Gold-entity visibility, for the four papers that have a gold record:

```
  paper                      gold  reach   seen  of reach  record
  cskg                         19     13     13    100.0%  reconciled
  cskg2                        30     25     23     92.0%  reconciled
  fact_completion              21     15     15    100.0%  human
  empire                        5      2      2    100.0%  human
    cskg2 unreachable: knowledge-centric paradigm, scientific knowledge graph
```

`reach` is the ceiling — entity groups findable in the gold text *itself*. A
group below the ceiling is a gold-curation artifact (an alias spelled
differently, a quote from a section the keep-list drops) and is not something a
segmenter change can win back. Reporting `seen / total` without it would blame
the segmenter for curation noise.

The `cskg2` row independently reproduces the **23/30** figure recorded in the
SEG-4 spec, and names the two groups that spec predicts SEG-4 recovers.

---

## Measured: state after SEG-3, SEG-6 and SEG-4

```
=== Committed corpus (gold text -> extractor input) ===
  paper                        gold     kept   recall  secs  abstract
  cskg                       24,959   24,925    99.9%     3  yes
  cskg2                      35,485   35,435    99.9%     4  yes
  kg_construction_survey      7,140    7,115    99.6%     2  yes
  llm_ontology_gen           17,353   17,306    99.7%     3  yes
  fact_completion            33,352   33,312    99.9%     3  yes
  kg_validation_hitl         34,744   34,695    99.9%     5  yes
  hypothesis_generation      43,818   43,757    99.9%     4  yes
  empire                     20,385   17,177    84.3%     3  yes

  abstracts found: 8/8

=== Recall-comparison readiness ===
  paper                     CONCAT   TYPED  notes
  cskg                       VALID INVALID  missing type(s): methods
  cskg2                      VALID   VALID  -
  kg_construction_survey     VALID   VALID  -
  llm_ontology_gen           VALID   VALID  -
  fact_completion            VALID INVALID  missing type(s): methods
  kg_validation_hitl         VALID INVALID  missing type(s): experiments
  hypothesis_generation      VALID   VALID  -
  empire                   INVALID INVALID  gold recall 84.3% (3,208 dropped);
                                            missing type(s): methods

  7/8 valid for a concatenated-blob recall comparison;
  4/8 also correctly section-typed
```

Gold-entity visibility:

```
  paper                      gold  reach   seen  of reach  record
  cskg                         19     13     13    100.0%  reconciled
  cskg2                        30     25     25    100.0%  reconciled
  fact_completion              21     15     15    100.0%  human
  empire                        5      2      2    100.0%  human
```

Both reconciled-gold papers — `cskg` and `cskg2` — are now CONCAT-valid and
both sit at their gold-entity ceiling. Only `cskg2` is also TYPED-valid;
`cskg`'s methods span is mislabelled `introduction` (see the box at the top).

### Why `empire` is still invalid

`Threats to Validity` is a genuine **subsection** inside gold's methods span
(`IV. RESEARCH APPROACH`). The segmenter promotes it to top level and types it
`limitations`, which the keep-list drops, so 3,208 characters of gold-wanted
text never reach the extractors. Two separate items own that:

- **SEG-11** — `_find_headings` requires nothing heading-shaped of a line, so a
  subsection heading is indistinguishable from a top-level one. A
  heading-context heuristic is the fix, and it needs its own measurement.
- **SEG-7** — if the keep-list is inverted, `limitations` stops being dropped
  and the loss disappears without any heading work at all.

Both are out of scope here. `empire` has a `human` gold record but no
reconciled one, so it is not one of the papers a defensible migration claim
would rest on today.

### The under-segmentation detector reads differently on this corpus

The SEG-4 spec records five warnings and zero false positives on the PDF
corpus. That figure is **not reproducible here** and this work cannot verify
it, because the PDFs are gitignored. Measured on the committed corpus: **8
warnings across 7 papers**, of which roughly half are real —

| Paper | Span | Share | Real cause |
|---|---|---:|---|
| `cskg` | `Introduction` | 81.0% | SEG-5a — `The Computer Science Knowledge Graph` |
| `empire` | `I. INTRODUCTION` | 76.1% | SEG-5d — `IV. RESEARCH APPROACH` |
| `kg_validation_hitl` | `1. Introduction` | 66.2% | SEG-5c + a SEG-11 phantom (`Model`) |
| `fact_completion` | `I. INTRODUCTION` | 57.1% | SEG-5a — `III. SciCheck` |
| `llm_ontology_gen` | ×2 | 49.2 / 41.5% | corpus artifact |
| `cskg2` | `Methods` | 44.6% | corpus artifact — correctly segmented |
| `hypothesis_generation` | `3. Methodology` | 40.9% | corpus artifact — healthy |

The cause of the artifacts is structural: the committed fixtures are
keep-list-filtered, so the denominator is 2–4 sections rather than a whole
paper's 7–13 and shares are mechanically higher. The threshold was calibrated
against full-paper section lists and **should be read off the PDF arm**.
Retuning it against a corpus it was not designed for would be worse than
recording the limitation.

### Heading recognition over the hand-labelled table

`scripts/segment_ground_truth.py` carries 48 hand-verified boundaries, which is
a ready-made eval set. Replaying it through `_classify_heading`, `_match_run_in`
and the positional rule:

| | recognized |
|---|---|
| `7108c7d` (base) | **27 / 48 = 56%** |
| after SEG-3 + SEG-4 | **38 / 48 = 79%** |

The residue is 10: five named after the paper's contribution or written as
descriptive sentences (SEG-5 a/c, which no name-based method can classify),
three over-strict anchors (SEG-5 d — measured and rejected, see
`segmenter-findings.md`), and two that gold labels but the importer's
vocabulary has no type for (`use_case`, `statistics`).

*(The backlog's SEG-12 entry quoted 27/42; the numerator was right and the
denominator was not — that table holds 48 boundaries. Corrected there.)*

---

## Why the fixtures pin output rather than a commit

`fixtures/segmenter_frozen/<slug>.json` stores, per paper:

| Field | Asserted? | Why |
|---|---|---|
| `source_sha256`, `source_chars` | **yes** | the input must not move under the pinned output |
| `sections` (type, title, chars) | **yes** | catches a resegmentation that preserves the char total |
| `extractor_input` + sha + chars | **yes** | the artifact both arms of a comparison must agree on, stored in the clear so a diff is reviewable |
| `segmenter_sha256` | no — provenance | a refactor leaving the output byte-identical is **not** drift |
| `keeplist` + `keeplist_sha256` | no — provenance | reported in the drift header so a chars delta names which of the two moved (SEG-7 will move it) |

Pinning the commit instead would be simultaneously too strict (any refactor goes
red) and too weak (PyMuPDF and the PDF bytes drift independently of this repo,
so a matching commit proves nothing about the text).

Re-pin deliberately, with a reviewable diff:

```
python scripts/measure_segmentation.py --freeze
```

---

## What this harness still cannot tell you

- **Anything gold excluded.** Furniture, references, and the survey's 94 pages.
  SEG-6's 244,742-character prompt cannot be reproduced here.
- **Extractor quality.** It measures what reaches the extractors, never what
  they do with it.
- **Precision.** By construction every character in the corpus is wanted, so
  the corpus cannot score a segmenter that keeps too much. Only the PDF corpus
  can.
- **Confusion between keep-list types.** See the box at the top. Char recall
  cannot see it; the TYPED verdict is the instrument for it.
- **The positional abstract on real PDF text.** `segment_ground_truth.py` cut
  the title page away, so the rule's entire risk surface — running backwards
  into publisher furniture — is absent from this corpus. Covered by synthetic
  guard tests and by a reconstruction of cskg2's recorded title-page line
  widths; **not** by a measurement of a PDF. One known limitation is pinned by
  `test_a_prose_width_metadata_line_IS_absorbed_known_limitation`: a single
  prose-width author line adjacent to the abstract *is* absorbed, because it
  carries no denylisted term and the span still reads as prose. The guards
  bound the blast radius; they do not guarantee a clean leading edge.
- **Five of eight papers' entity recall.** `kg_construction_survey`,
  `llm_ontology_gen`, `kg_validation_hitl` and `hypothesis_generation` have no
  gold record at all; `empire` and `fact_completion` have only a single
  (`human`) review, not a reconciled one.
