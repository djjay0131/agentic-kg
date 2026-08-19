---
title: Segmenter Findings
nav_exclude: true
---

# `SectionSegmenter` mis-segments every paper in the ground-truth set

**Found:** 2026-07-27, while preparing extractor input for the ground-truth
curation task (`docs/ground-truth/README.md`).
**Affects:** `packages/core/src/agentic_kg/extraction/section_segmenter.py`,
and therefore every entity extracted by `ingest_papers`.
**Severity:** High. One of eight papers yields **zero** extractor input and
therefore zero entities, silently and with no error.

**Update 2026-08-09:** a seventh, independent cause was found — see
[(7)](#7-the-four-section-keep-list-loses-content-even-when-boundaries-are-perfect).
Causes (1)–(6) are about locating section boundaries; (7) is about which
sections are kept once located, and it survives fixing all of them. It is also
the only one that reaches the ground-truth labels.

## Summary

`ingest_papers` feeds all four entity extractors a single text block built by
`_build_extractor_section_text` (`ingestion.py:198`), which keeps only the
sections typed `abstract`, `introduction`, `methods`, `experiments`. Running the
current segmenter over the 8-paper ground-truth chain, **not one paper produced
a clean abstract + introduction + methods + experiments block**, only one of
eight produced an abstract at all, and only two of the six papers that have a
methods section had it recognized.

This was found by preparing labeling input, not by looking for bugs. The
labeling task is now unblocked via a hand-written segmenter
(`scripts/segment_ground_truth.py`) — this document records the underlying
defect so it can be fixed on its own terms.

## Observed output

Chars fed to the extractors, per paper, via `SectionSegmenter.segment()`:

| Slug | Chars | Sections detected | Problem |
|---|---:|---|---|
| `fact_completion` | **0** | `references` only | Extractors receive `""` and short-circuit — **no entities at all** |
| `cskg` | 8,919 | intro (822w), experiments (517w) | No abstract, no methods |
| `empire` | 12,405 | one `methods` block | Abstract, intro, and most sections missed |
| `kg_validation_hitl` | 23,938 | 5 fragments | No abstract |
| `cskg2` | 39,226 | one `methods` block | `Methods` swallows everything to the references |
| `llm_ontology_gen` | 43,250 | 7 fragments | No abstract |
| `hypothesis_generation` | 49,517 | 4 fragments | No abstract |
| `kg_construction_survey` | **244,748** | `introduction` = 16,715 words | Whole body swallowed into "introduction" |

For comparison, hand-verified boundaries over the same PDFs yield 1,130–6,456
words per paper — all eight plausible, all eight with a real abstract.

## Root causes

### 1. Roman-numeral section headings never match

Every pattern in `SECTION_PATTERNS` admits only Arabic numbering:

```python
SectionType.INTRODUCTION: [
    r"^(?:\d+\.?\s*)?introduction\s*$",
    ...
]
```

IEEE-format papers number sections with Roman numerals. Confirmed present in
the PDFs and unmatched by every pattern:

```
fact_completion: I. INTRODUCTION, II. RELATED WORK, III. SciCheck,
                 IV. EVALUATION, V. USE CASE: AI-KG, VI. CONCLUSION
empire:          I. INTRODUCTION, II. BACKGROUND, III. RELATED WORK,
                 IV. RESEARCH APPROACH, V. RESULTS, VI. THREATS TO VALIDITY,
                 VII. DISCUSSION, VIII. CONCLUSION
```

In `fact_completion` the only heading that matched anything was the unnumbered
`REFERENCES`, which is why that paper produces an empty extractor input.

**Fix:** extend the numeric prefix group to accept Roman numerals and letters,
e.g. `(?:(?:\d+|[IVXLC]+|[A-Z])[\.\)]?\s*)?`.

### 2. `segment_with_abstract` can never match an abstract

`section_segmenter.py:377`:

```python
abstract_match = re.search(
    r"^(?:abstract\s*[:\-]?\s*)(.*?)(?=\n\s*(?:\d+\.?\s*)?(?:introduction|1\.|I\.))",
    text,
    re.IGNORECASE | re.DOTALL,      # <-- no re.MULTILINE
)
```

Without `re.MULTILINE`, `^` anchors to the start of the entire document. Every
real PDF begins with a title and author block, so this never fires.

Empirically confirmed: `segment()` and `segment_with_abstract()` returned
byte-identical output on all 8 papers.

This is currently moot in production — `pipeline.py:475` calls plain
`.segment()`, so `segment_with_abstract` is dead code on the ingest path. Worth
deciding whether to fix it or delete it.

**Fix:** add `re.MULTILINE`, and note the lookahead also needs the Roman-numeral
fix from (1) to terminate correctly.

### 3. Run-in abstracts defeat the standalone-line pattern

`r"^abstract\s*$"` requires `Abstract` alone on a line. Real formats run the
abstract into the same line:

```
IEEE ESEM:    Abstract—[Background.] Empirical research in requirements...
IEEE Access:  ABSTRACT In the last few years, we have witnessed...
Springer:     Abstract. In recent years, we saw the emergence of...
Elsevier:     A B S T R A C T          (letter-spaced by the PDF extractor)
Nature SD:    (no "Abstract" label at all — it is the lead paragraph)
```

Five distinct conventions across eight papers, and the pattern matches none.

**Fix:** allow a run-in delimiter (`[—\-–.:]` or whitespace) after the keyword,
and handle the letter-spaced `A B S T R A C T` form that Elsevier PDFs produce.

### 4. An unrecognized heading lets the previous section swallow the rest

Section spans run from one recognized heading to the next, so a journal whose
headings aren't in the pattern table produces one enormous section.

`cskg2` is Nature *Scientific Data*, whose top-level headings are
`Background & Summary`, `Methods`, `Data Records`, `Technical Validation`,
`Usage Notes`. Only `Methods` matches, so it absorbs 39k chars through to the
references. The same mechanism inflated the survey's `introduction` to 16,715
words.

Note the semantic mapping is also missing: `Background & Summary` is that
journal's introduction and `Technical Validation` its evaluation, but neither
name appears in `SECTION_PATTERNS`.

**Fix:** add the Nature/`Scientific Data` heading vocabulary, and consider a
sanity check that flags any single section exceeding some word count as
probable under-segmentation rather than passing it downstream.

### 5. Methods and experiments sections are usually not named "Methods" or "Experiments"

The pattern table assumes generic section names. Real papers name the methods
section after *the thing they built*, or write a descriptive sentence. Testing
the live patterns against the actual headings of this set:

| Paper | Wanted | Actual heading | Classified as |
|---|---|---|---|
| `cskg` | methods | `The Computer Science Knowledge Graph` | **unmatched** |
| `cskg` | experiments | `Evaluation` | experiments ✓ |
| `cskg2` | methods | `Methods` | methods ✓ |
| `cskg2` | experiments | `Technical Validation` | **unmatched** |
| `llm_ontology_gen` | experiments | `4. Experiments` | experiments ✓ |
| `fact_completion` | methods | `III. SciCheck` | **unmatched** |
| `fact_completion` | experiments | `IV. EVALUATION` | **unmatched** |
| `kg_validation_hitl` | methods | `4. Integrating LLMs and HiL into the SCICERO validation` | **unmatched** |
| `kg_validation_hitl` | experiments | `5. Experiment design and implementation` | **unmatched** |
| `hypothesis_generation` | methods | `3. Methodology` | methods ✓ |
| `hypothesis_generation` | experiments | `4. Evaluation` | experiments ✓ |
| `empire` | methods | `IV. RESEARCH APPROACH` | **unmatched** |

**7 of 12 unmatched. Only 2 of 6 methods sections are found.**

This is a separate defect from (1), and the more stubborn one: only
`IV. EVALUATION` is explained by Roman numerals. **Five of the seven misses
survive a perfect Roman-numeral fix**, for four different reasons:

- **Named after the contribution** — `The Computer Science Knowledge Graph`,
  `III. SciCheck`. No keyword list can catch these; the section is named after
  the system the paper introduces.
- **Journal-specific vocabulary** — `Technical Validation` is Nature *Scientific
  Data*'s evaluation section. Fixable by adding vocabulary (see (4)).
- **Descriptive sentence headings** — `4. Integrating LLMs and HiL into the
  SCICERO validation`. Contains no method keyword at all.
- **Keyword present but pattern too strict** — `5. Experiment design and
  implementation` fails because the pattern is
  `experiment(?:s|al)?\s*(?:setup|settings)?\s*$`, which allows only `setup` or
  `settings` as a suffix. `IV. RESEARCH APPROACH` fails because the approach
  pattern admits only `our` or `proposed` as a prefix.

The last category is cheap to fix: relax the anchors so a heading *containing* a
method/experiment keyword matches, rather than requiring near-exact equality.
That alone would recover `5. Experiment design and implementation` and
`IV. RESEARCH APPROACH`.

The first and third categories cannot be fixed by pattern-matching. Options
worth weighing:

- **Positional fallback** — if no methods section is found, treat the span
  between the last recognized front-matter section (related work / background)
  and the first recognized results/evaluation section as methods. This works for
  `cskg`, `fact_completion`, and `kg_validation_hitl`.
- **Don't filter at all** — feed everything except references, acknowledgments,
  and appendices. Given (4) already lets unrecognized headings silently merge
  into their predecessor, the section allowlist is providing less filtering
  precision than it appears to.
- **LLM-assisted heading classification** for the residue, which is more
  machinery than this probably warrants.

Whatever is chosen, note that the current failure is silent: an unmatched
methods heading doesn't error, it just quietly removes the paper's core
technical content from every extractor's input.

### 6. Unbounded section length reaches the LLM call

Independent of the above: 244,748 chars would be sent as a single extractor
prompt. There is no length guard between `_build_extractor_section_text` and
the extractor calls, only `MIN_USABLE_CHARS = 250` on the lower end.

### 7. The four-section keep-list loses content even when boundaries are perfect

**Found:** 2026-08-09, while reconciling `fact_completion`. This is a different
defect from (1)–(5) and survives fixing all of them.

Causes (1)–(5) are about *finding* section boundaries. This one is about which
sections are kept once found. `_build_extractor_section_text`
(`ingestion.py:198`) keeps exactly four types — `abstract`, `introduction`,
`methods`, `experiments` — and discards everything else.

`scripts/segment_ground_truth.py` uses **hand-verified** boundaries, so it
isolates the variable: any content missing from `paper_<slug>.txt` is lost to
the keep-list policy, not to boundary detection. Measuring that loss across all
eight papers:

| Slug | Body chars | Fed to extractors | Coverage | Chain entities in body | Lost |
|---|---:|---:|---:|---:|---:|
| `hypothesis_generation` | 61,088 | 43,771 | 71.7% | 17 | 2 |
| `fact_completion` | 47,804 | 33,340 | 69.7% | 32 | 5 |
| `cskg2` | 51,847 | 35,470 | 68.4% | 34 | **0** |
| `cskg` | 37,639 | 24,955 | 66.3% | 18 | 1 |
| `kg_validation_hitl` | 73,055 | 34,693 | 47.5% | 12 | 4 |
| `empire` | 57,871 | 20,380 | 35.2% | 2 | 0 |
| `llm_ontology_gen` | 84,401 | 17,347 | **20.6%** | 12 | **8** |
| `kg_construction_survey` | 181,256 | 7,139 | **3.9%** | 22 | **18** |

"Body" is PDF text up to the last references heading. "Chain entities" are the
53 distinct gold entities (canonical + aliases, word-boundary matched, grouped
so an alias hit counts as the entity being visible) drawn from the gold files
that existed on 2026-08-09. "Lost" means the entity appears in the body under
some gold surface form and under **none** of them in the extractor's input.

**Two papers are effectively unlabelable, for defensible per-paper reasons.**

- `kg_construction_survey` gets 3.9% of its body. The keep-list is
  `["abstract", "introduction"]`, which is the honest call for a 94-page survey
  with no methods or experiments sections — but the consequence is that 18 of
  the 22 chain entities in the paper are invisible, and the paper contributes
  almost nothing to a validation set built around cross-paper concept
  accumulation. Worth asking whether it earns its place in the set at all.
- `llm_ontology_gen` gets 20.6%, losing 8 of 12 — including
  `scientific knowledge graph`, the chain's spine concept, and `fine-tuning`.
  Cause: its `3. Background` is background (excluded) and its approach lives in
  `4. Experiments`, so the method content straddles a kept and an excluded span.

**`fact_completion` is the sharpest case, because the loss is targeted.** Its
Section V (`Use Case: AI-KG`, 6,502 chars) is excluded as a `use_case` type, and
it is the single densest source of chain-recurring entities in the document:

- `support score` — "the authors adopted a support score deﬁned as the number
  of research papers where the fact was extracted from". This is the concept
  `cskg` and `cskg2` were reconciled to share one canonical for, and it occurs
  **zero** times in the extractor input. The five `support` hits there are the
  verb "supporting" and the AI-KG relation names `supportsTask`/`supportsMethod`.
- `TransR` (a scored model on `cskg2`), the CSO topic classifier (a scored model
  on both predecessors), verb clustering (`predicate mapping` on both), and the
  exact phrase `information extraction pipeline` (a scored concept on both).

A "use case" or "application" section is where a paper says what its artifact
is *for* — on this paper that is where it connects to the rest of the chain.

**Why this matters more than a coverage number.** The set exists to watch a
concept accumulate evidence across a citation chain. Where a recurring entity is
present in the paper and absent from the extractor's input, the diff will show a
recall gap that is a keep-list decision, not an importer failure. Gold cannot
compensate: labeling an entity the importer cannot read just manufactures a
guaranteed miss.

**Fix options**, in increasing order of change:

1. Add `use_case` / `application` to the keep-list. Cheapest, and recovers the
   `fact_completion` case specifically.
2. Add `results` and `background`, which would recover `empire` (35.2%, `V.
   RESULTS` excluded) and `llm_ontology_gen`.
3. Invert the filter — keep everything except references, acknowledgments,
   appendices and author bios. This is the "don't filter at all" option already
   raised under (5), and this data strengthens it: the allowlist's precision
   benefit is unmeasured, while its recall cost is now measured and large. Note
   it interacts with (6) — the survey would then send 181k chars to one prompt,
   so a length guard becomes a prerequisite rather than a nice-to-have.

**Caveats on the numbers.** Body includes tables, figure captions and running
headers, so coverage is a floor rather than an exact reading ratio. One of
`fact_completion`'s five losses (`paraphrase-distilroberta-base-v2`) is a
surface-form artifact rather than a section exclusion — the extractor breaks it
as `paraphrase-distilrobertabase-v2` here and as `paraphrasedistilroberta-base-v2`
in `cskg`, so the gold alias from one paper does not match the other. `empire`'s
zero is not a clean bill of health: only 2 chain entities appear in it at all,
because it is the one paper from a different research community. And the probe
set is drawn from the three papers reviewed so far, so the unreviewed papers are
being measured with their neighbours' vocabulary and their "in body" counts
understate what is actually there.

## Reproduction

```bash
python -m venv .venv-gt
.venv-gt/Scripts/python.exe -m pip install PyMuPDF httpx tenacity pydantic PyYAML neo4j
.venv-gt/Scripts/python.exe scripts/segment_ground_truth.py   # hand-verified boundaries
```

To observe the defect itself, call `SectionSegmenter().segment(full_text)` on
any PDF in `ground-truth-papers/` and inspect `doc.sections`.

## Suggested test coverage

The current tests appear to exercise Arabic-numbered, standalone-heading papers
only. Worth adding fixtures for:

- Roman-numeral headings (IEEE)
- Each of the five abstract conventions listed in (3)
- A paper with journal-specific headings (Nature `Scientific Data`)
- A methods section named after the contribution rather than "Methods"
  (e.g. `III. SciCheck`) — see (5)
- A descriptive sentence heading containing no method keyword
- An assertion that no single section exceeds a plausible word count
- An assertion that a paper with a known methods section actually yields one —
  the current failure mode is silent, so a test that only checks "segmentation
  returned some sections" would pass on every paper in this set

## Impact on the ground-truth task

None to the labels from causes (1)–(6), by construction.
`scripts/segment_ground_truth.py` uses hand-verified boundaries, so the gold
files describe what the importer *intends* to read. Baking today's segmenter
output into gold would have invalidated all eight files the moment the segmenter
is fixed.

It does mean the eventual importer-vs-gold diff will show large recall gaps that
are **segmenter** failures, not extractor failures. Fix this first, or the diff
will be read wrong.

**Cause (7) is the exception, and it does reach the labels** — added 2026-08-09.
Hand-verified boundaries do not help when the content is inside a section type
the keep-list discards. Coverage ranges from 3.9% to 71.7% of body text, and on
`fact_completion`, `llm_ontology_gen` and `kg_construction_survey` the discarded
spans hold chain-recurring entities that appear nowhere in the extractor's
input. Gold correctly omits them — an entity the importer cannot read is not a
labeling target — but that means those papers under-contribute to the concept
accumulation the set was built to measure, silently. Decide the keep-list
question before diffing, and if it changes, the affected `paper_<slug>.txt`
fixtures must be regenerated and their reviews redone rather than patched.
