# Feature: SEG-6 — No Upper Length Guard Between the Segmenter and the LLM Call

**Status:** IMPLEMENTED 2026-09-18
**Date:** 2026-09-18
**Author:** Segmenter / Corpus Readiness Implementer (AI-assisted)
**Backlog ID:** SEG-6
**Source:** [`docs/ground-truth/segmenter-findings.md`](../../docs/ground-truth/segmenter-findings.md) root cause (6)

## Problem

`_build_extractor_section_text` (`ingestion.py:198`) joins the keep-list
sections and hands the result to five or six extractor calls. Nothing bounds
the result. `MIN_USABLE_CHARS = 250` guards the lower end; the upper end is
open.

Measured over the eight ground-truth papers (`scripts/seg_baseline.json`,
PDF corpus):

| Paper | Extractor input | Hand-verified gold | Ratio |
|---|---:|---:|---:|
| **`kg_construction_survey`** | **244,742** | **7,152** | **34.2×** |
| `hypothesis_generation` | 49,511 | 43,818 | 1.13× |
| `llm_ontology_gen` | 43,238 | 17,353 | 2.49× |
| `cskg2` | 39,226 | 35,485 | 1.11× |
| `fact_completion` | 25,586 | 33,352 | 0.77× |
| `kg_validation_hitl` | 23,930 | 34,744 | 0.69× |
| `empire` | 17,953 | 20,385 | 0.88× |
| `cskg` | 8,917 | 24,959 | 0.36× |

There is a clean gap: the survey is **4.9× larger than the next largest
paper**, and 34× its own gold. The cause is root cause (5)/(8) — an
unrecognized heading lets `introduction` run for 94 pages — and the
consequence is that the amount of text reaching the extractors is a function
of *how badly the segmenter failed*, not of how long the paper is.

### Why this blocks a recall comparison, not merely a budget

The framing in the backlog is "cheap on its own and a prerequisite for SEG-7
option 3". That undersells it. Two arms of a legacy-vs-new comparison can be
fed inputs differing by 30× with no signal that anything is wrong: the
oversized arm is scored on a haystack, the other on a needle, and the
difference is attributed to the extractor. An input with no upper bound is not
a reproducible experimental condition.

Making the bound explicit converts a silent 34× variance into a logged,
counted event.

## Goals

- Extractor input is **bounded** by a single documented constant.
- Truncation is **deterministic** — same input, same output, always — and cuts
  at a paragraph boundary so no sentence is severed mid-clause.
- Truncation is **loud**: a WARNING naming the paper, the original size, the
  cap, and how much was dropped.
- **No healthy paper is affected.** The cap must not fire on any correctly
  segmented paper in the corpus.

## Non-Goals

- **Fixing the survey's segmentation.** That is SEG-5/SEG-11 (the unrecognized
  headings) and SEG-10 (whether the paper belongs in the set at all). SEG-6
  bounds the blast radius; it does not pretend to have fixed the cause.
- **Choosing *which* content to drop.** A smarter policy — drop the lowest
  priority section, sample across sections, summarize — is a quality question
  that needs its own measurement. Tail truncation is the honest default: it is
  what an over-long prompt would suffer anyway, made visible.
- **Token counting.** Tokens are model-specific and require the tokenizer at
  ingestion time. Characters are a deterministic proxy, and the cap is set far
  enough below any context limit that the difference does not matter.
- **Dropping the paper.** A truncated paper still yields entities; a dropped
  one yields none. `failed_thin` exists for input that is unusably *small*.

## Design Approach

### Decision 1 — The guard lives in `_build_extractor_section_text`

It is the single place the extractor input is assembled, and every caller goes
through it. The segmenter stays ignorant of LLM budgets, which is right: a
`Section` is a fact about a document, not about a prompt.

### Decision 2 — `MAX_EXTRACTOR_CHARS = 120_000`

Two independent constraints, and the number satisfies both:

- **Above every healthy paper.** The largest correctly-segmented extractor
  input in the corpus is 49,511 characters (`hypothesis_generation`). The cap
  sits at **2.4× that**, so a paper has to be badly mis-segmented to reach it.
  Only `kg_construction_survey` does, and only because `introduction` swallows
  94 pages.
- **Well inside any context the extractors run against.** ~120,000 characters
  is roughly 30,000 tokens, about a quarter of a 128k-token context, leaving
  ample room for the prompt, the response schema and the output.

The constant is deliberately a single module-level literal rather than a
parameter with a default in three places; SEG-7 option 3 will want to revisit
it, and it should have exactly one place to look.

### Decision 3 — Cut at a paragraph boundary, then a word boundary, then hard

```
1. the last "\n\n" at or before the cap   -- sections are joined with "\n\n",
                                             so this usually ends on a section
2. else the last whitespace before it     -- never split a token
3. else a hard slice                      -- a 120,000-character single word
```

Every step is a pure function of the input string, so the result is
byte-stable. The fallbacks are not decoration: step 1 can fail on a document
with no blank lines, and step 2 on pathological extractor output.

### Decision 4 — Truncation is warned, not silent

`logger.warning` with the label, the original length, the cap and the number of
characters dropped. This is the same reasoning as SEG-1's AC-13 partial
segmentation warning: the failure mode that stayed hidden for months was the
*silent* one.

## Acceptance Criteria

- **AC-1** Input at or below the cap is returned byte-identical, and no warning
  is logged. Must be red if the guard truncates unconditionally.
- **AC-2** Input above the cap comes back at or below the cap.
- **AC-3** Truncation is idempotent and deterministic: truncating the same
  input twice yields the same string, and truncating an already-truncated
  string is a no-op.
- **AC-4** The cut lands on a paragraph boundary when one exists at or before
  the cap.
- **AC-5** With no paragraph boundary, the cut lands on a word boundary — no
  token is split.
- **AC-6** With neither, a hard slice still respects the cap.
- **AC-7** Truncation logs one WARNING naming the label, original size, cap and
  dropped count.
- **AC-8** No paper in the committed corpus is truncated — the guard must not
  fire on healthy input, and the frozen fixtures are therefore unchanged.
- **AC-9** The cap exceeds the largest healthy extractor input in
  `scripts/seg_baseline.json` by a stated margin, asserted against the file so
  the margin cannot silently erode.
- **AC-10** `kg_construction_survey`'s 244,742-character PDF-corpus input is
  bounded by the guard, asserted against `seg_baseline.json` rather than
  against a PDF, so it runs in CI.

## Technical Notes

- **Affected**: `packages/core/src/agentic_kg/ingestion.py`
  (`MAX_EXTRACTOR_CHARS`, `_truncate_extractor_input`,
  `_build_extractor_section_text`).
- **Tests**: `packages/core/tests/extraction/test_seg6_length_guard.py`.
- **Downstream**: none. The call site at `ingestion.py:328` is unchanged; the
  `MIN_USABLE_CHARS` check still runs on the returned text, and a truncated
  paper is far above that floor by construction.
- **Interaction with the frozen corpus**: the committed corpus's largest
  extractor input is 43,757 characters, so `--freeze` output is unchanged.
  Asserted, not assumed (AC-8).

## Dependencies

- **Blocks SEG-7 option 3** (invert the keep-list). Keeping everything except
  the tail types would send the survey's whole body to one prompt; the backlog
  records this as a hard prerequisite.
- **Does not fix** SEG-5/SEG-11, which are why the survey is oversized in the
  first place, or SEG-10, which asks whether it belongs in the set.
