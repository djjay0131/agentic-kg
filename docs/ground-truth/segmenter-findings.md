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

None to the labels, by construction. `scripts/segment_ground_truth.py` uses
hand-verified boundaries, so the gold files describe what the importer *intends*
to read. Baking today's segmenter output into gold would have invalidated all
eight files the moment the segmenter is fixed.

It does mean the eventual importer-vs-gold diff will show large recall gaps that
are **segmenter** failures, not extractor failures. Fix this first, or the diff
will be read wrong.
