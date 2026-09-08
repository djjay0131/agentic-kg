# Feature: SEG-4 — Journal Vocabulary, Positional Abstract, and an Under-Segmentation Detector

**Status:** SPECIFIED
**Date:** 2026-09-08
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SEG-4
**Source:** [`docs/ground-truth/segmenter-findings.md`](../../docs/ground-truth/segmenter-findings.md) root cause (4), plus the no-label case inherited from root cause (3)

> **Ships as a pair with SEG-3** (`seg3-run-in-abstracts.md`). SEG-3 is gated
> on this spec existing; together they close root cause (3) and (4). Every
> corpus number below is measured **on top of SEG-3**, because SEG-4 alone
> produces misleading results — see Edge Cases.

## Problem

Section spans run from one recognized heading to the next
(`_extract_sections`), so a heading the pattern table doesn't know is not a
missed section — it is **absorbed into its predecessor**, silently. A journal
whose vocabulary is absent therefore produces one enormous mislabelled section
rather than an obviously broken result.

`cskg2` is Nature *Scientific Data*, whose top-level headings are
`Background & Summary`, `Methods`, `Data Records`, `Technical Validation`,
`Usage Notes`. Only `Methods` is in `SECTION_PATTERNS`, so it absorbs
**5,385 words — 100% of the recognized body** — through to the references,
while the paper's real introduction (`Background & Summary`) sits in no section
at all and is dropped entirely.

Measured share of body words held by the largest section in each paper (tail
types excluded), post-SEG-1:

| Share | Paper | Section | Cause |
|---:|---|---|---|
| **100.0%** | `cskg2` | `methods` `'Methods'` | **SEG-4** — journal vocabulary |
| 70.2% | `cskg` | `related_work` | SEG-5 — `The Computer Science Knowledge Graph` |
| 49.1% | `kg_validation_hitl` | `related_work` | SEG-5 — descriptive-sentence heading |
| 45.6% | `kg_construction_survey` | `introduction` | SEG-5 / SEG-11 |
| 43.4% | `fact_completion` | `experiments` | SEG-5 — `V. USE CASE: AI-KG` |
| 42.3% | `fact_completion` | `related_work` | SEG-5 — `III. SciCheck` |
| 31.4% | `hypothesis_generation` | `methods` | *healthy* |

There is a clean gap between the swallowed spans (≥42.3%) and the largest
healthy section (31.4%), which is what makes a detector viable.

### The finding that reframes this item

**Fixing the segmentation makes the extractor input smaller.** Measured on
`cskg2`:

| | chars fed to extractors | gold entities visible |
|---|---:|---:|
| today (`Methods` swallows everything) | 39,226 | 23 / 30 |
| correctly segmented | **34,223** (−5,003) | **25 / 30** (+2, **0 lost**) |

Today the swallowed block is labelled `methods`, and `methods` is in the
four-section keep-list, so *all* of it is kept — including `Data Records` and
`Usage Notes`, which the keep-list is supposed to discard. Segment it properly
and those are correctly excluded, while `Background & Summary` is recovered —
and it contains `scientific knowledge graph`, the chain's spine concept, plus
`knowledge-centric paradigm`.

So SEG-4 is a correctness fix that the char-based harness reports as a
**regression**. That is not a reason to avoid it; it is a reason the harness
needs a second metric (Decision 3).

### Why it matters now

`cskg2` is one of only three papers with a reconciled gold record, and the
findings doc records it as losing **0 of 34** chain entities — a figure that is
an artifact of the swallowing, not a clean bill of health. Diffing importer
output against gold while a paper's introduction is invisible and its
`Data Records` is mislabelled as methods would produce numbers nobody can
interpret.

## Goals

- `cskg2` segments into the seven sections its **hand-verified gold** records:
  abstract, introduction (`Background & Summary`), methods, results
  (`Data Records`), experiments (`Technical Validation`), discussion
  (`Usage Notes`), references.
- `cskg2` gold-entity visibility **23/30 → 25/30**, zero lost, at a deliberate
  cost of −3,797 chars.
- **8 of 8 papers have an abstract** (with SEG-3), closing root cause (3).
- A standing detector: the four remaining swallowed spans each produce one
  actionable warning naming the section, its share, and the next recognized
  heading.
- No paper regresses on entity visibility where gold exists.

## Non-Goals

- **Vocabulary for journals not in the corpus.** I enumerated every unmatched
  plausible heading line across all eight papers — 31 to 278 per paper — and
  roughly 90% is noise (author names, table cells, `Keywords:`, figure labels,
  running headers). The only genuine journal-vocabulary gap is `cskg2`'s.
  Adding PLOS/BMC/ACM conventions now would be unmeasured speculation.
- **Fixing the other four swallowed spans.** They are SEG-5 (contribution-named
  and descriptive-sentence headings) and SEG-11 (phantom single-word headings).
  SEG-4 **detects** them; it cannot fix them, and pretending otherwise would
  mean absorbing two unspecified items.
- **Phantom-heading detection.** The survey's phantom `Approach` (27.7%) and
  `Model` (23.1%) spans sit *below* any threshold that avoids false-firing on
  healthy 31.4% sections. Swallowing and phantoms are different signals, and
  the second one is SEG-11's actual subject. This adjusts the pre-spec D5
  expectation, which assumed one rule could catch both.
- **Splitting an under-segmented section.** The detector warns; it does not
  guess where the missing boundary was. Guessing is SEG-5's positional-fallback
  option, which needs its own measurement.
- **Gating on entity visibility.** `--entities` reports. Gold covers 3 of 8
  papers, and a partial metric should not hold a green/red verdict.

## User Stories

- As the importer-vs-gold diff, I want `cskg2`'s introduction visible and its
  `Data Records` correctly discarded, so its recall numbers mean something.
- As an operator, I want to be told when one section holds most of a paper, so
  I learn about a missing heading from a log line rather than from a bad
  extraction weeks later.
- As whoever implements SEG-5 or SEG-11, I want a detector already firing on
  the spans I am about to fix, so my work has a before/after signal.
- As a reviewer of this PR, I want a metric that can tell "recovered content"
  from "swallowed the next section", because character count cannot.

## Design Approach

### Decision 1 — Nature *Scientific Data* vocabulary only

Four patterns, no speculation. See Non-Goals.

### Decision 2 — Map to the semantics the keep-list rewards honestly

| Heading | Mapped to | Why |
|---|---|---|
| `Background & Summary` | **introduction** | It *is* that journal's introduction. Mapping it to `background` reads the name literally and costs **−15,932 chars**, because `background` is not in the keep-list — and it hides the chain's spine concept. |
| `Technical Validation` | **experiments** | That journal's evaluation section. |
| `Data Records` | **results** | Describes the released artifact, not the method. |
| `Usage Notes` | **discussion** | Guidance and caveats. |

Rejected: `Data Records` → `methods`, which measures **+6,138 chars** but is a
false label chosen to game the keep-list. If the keep-list is too narrow —
and SEG-7's data says it is — that is SEG-7's decision to make openly, not
something to smuggle in via a mis-mapping here.

### Decision 3 — Extend the harness with gold-entity visibility

`scripts/measure_segmentation.py --entities` loads the reconciled (falling back
to human) gold record for each paper that has one, and reports how many gold
entity groups are visible in the extractor input. A group is visible when its
canonical name **or any acceptable alias** appears under word-boundary
matching — the same rule the findings doc used for its coverage table, so the
numbers are comparable.

Chars remain the verdict where no gold exists; where gold exists, entity
visibility is the number that matters. Reusable by SEG-5, SEG-7 and the
eventual importer-vs-gold diff.

### Decision 4 — Positional abstract: prose run plus two complementary guards

When no abstract label is found anywhere, the abstract is the **maximal run of
consecutive prose-width lines immediately preceding the first recognized
heading**. Title, author and journal-header lines are short and irregular, so
they break the run:

```
   0| ( 76) Scientific Data | (2025) 12:964 | https://doi.org/...
   1| ( 29) www.nature.com/scientificdata
   2| ( 34) CS-KG 2.0: A Large-scale Knowledge
   3| ( 25) Graph of Computer Science
   4| ( 86) Danilo Dessí1, Francesco Osborne 2,3 ✉, ...
   5| ( 15) & Enrico Motta2              <-- breaks the run
   6| (106) The rapid evolution of AI and ...   <-- run starts
  ...
  17| ( 85) ... and scientific question-answering.
```

Measured: lines 6–17, **1,210 chars** — byte-identical to the hand-verified
gold boundary, and inside SEG-3's AC-16 band (500–4,000). No length cap.

**Prose width is measured relative to the document, not in absolute
characters** (review, QA #2). Line width is a property of column layout: the
median body-line width is 106 on `cskg2` (single column) but **59** on
`fact_completion`, **60** on `empire` and **69** on `hypothesis_generation`
(all two-column). An absolute threshold of 70 would work on `cskg2` and
silently find nothing on an entire class of paper. The threshold is therefore
`0.75 × median body-line width`, verified to land on `cskg2`'s lines 6–17 at
exactly 1,210 chars — the 15-character author line breaks the run either way.

**Two guards, covering different failures.** A span must pass both:

1. **Furniture denylist** — reject if it contains an email, URL, DOI,
   `Contents lists available`, `ScienceDirect`, or `received`/`accepted`.
2. **Prose test** — require ≥2 sentence terminators and digit density <5%.

They are not redundant, and not for the reason first assumed. Measured:

| Span | Sentences | Digits | Rejected by |
|---|---:|---:|---|
| `cskg2` lead paragraph (**real**) | 7 | 0.8% | *neither — accepted* |
| `cskg2` author/journal block | 0 | 13.7% | **prose test** |
| `cskg` title page | 8 | 1.4% | furniture only |
| `empire` title page | 12 | 1.0% | furniture only |
| `fact_completion` title page | 10 | 4.3% | furniture only |
| `llm_ontology_gen` title page | 11 | 0.8% | furniture only |

The title-page spans contain the paper's *actual abstract prose* (they run from
the document start to the first recognized heading), so they read as prose and
the prose test cannot reject them — only the denylist can. Conversely a pure
metadata block fails the prose test decisively (0 sentences, 13.7% digits),
which is what covers the long-author-line limitation the denylist would miss.

Measured separation for the denylist:

| Span | Furniture hits | Outcome |
|---|---|---|
| `cskg2` real lead paragraph (1,210 c) | *none* | **accepted** |
| `cskg` title page (2,058 c) | `@ @ @` | rejected |
| `cskg2` pre-SEG-3 (12,428 c) | `https:// doi.org www. @` | rejected |
| `llm_ontology_gen` (2,254 c) | `Contents lists available`, `ScienceDirect` | rejected |
| `fact_completion` (2,532 c) | `Received`, `accepted`, `@` | rejected |
| `kg_validation_hitl` (2,413 c) | `ScienceDirect`, `www.`, `https://` | rejected |
| `hypothesis_generation` (2,649 c) | `ScienceDirect`, `www.`, `@` | rejected |
| `empire` (2,370 c) | `@ @ @ @` | rejected |

Without the guard, SEG-4 running before SEG-3 builds 2,058–2,649-char
"abstracts" out of title pages on **six** papers — and because they fall inside
SEG-3's 500–4,000 band, **AC-16 would not catch them**. The guard makes the
rule order-independent and safe for future label-less papers, not just this
corpus.

### Decision 5 — Under-segmentation detector at 40% of body words

In `segment()`, warn when any non-tail section holds ≥40% of body words.
Denominator excludes `references` / `acknowledgments` / `appendix`, which are
legitimately large (`references` reaches 31.8% of *total* words on
`fact_completion`). Silent below 500 body words, so short documents and test
fixtures don't trip it.

Measured behaviour post-SEG-3+4 — five warnings, zero false positives, and a
~9-point margin between the lowest firing span (41.3%) and the largest healthy
one (~31%):

```
[cskg]                   'Related Work' (related_work) 67.6% of body words
                         next recognized heading is 'Evaluation'
[kg_construction_survey] '1. Introduction' (introduction) 45.6%
[kg_validation_hitl]     '2. Related work' (related_work) 48.1%
[fact_completion]        'IV. EVALUATION' (experiments) 42.3%
[fact_completion]        'II. RELATED WORK' (related_work) 41.3%
[cskg2]                  (silent — fixed by this feature)
```

**Placement: the segmenter, not `ingestion.py`.** Under-segmentation is a
judgement about segmentation quality and needs the section list, so it belongs
where that list is built — and it then fires for every caller (CLI, API, Cloud
Run Job, the harness), not only `ingest_papers`. SEG-1's AC-13 warning stays
where it is; that one is about *extractor input*, which is an ingestion
concern.

The message names the section, its type, its share, and **the next recognized
heading** — the last being the actionable part, since the missing heading lies
between them. It does not attempt to name a root cause: attribution would be a
guess, and the two facts together let a human read it in seconds.

## Delivery — three PRs, one spec

The three parts have genuinely different risk profiles (review, TL #1), so they
ship separately against this one spec. The order puts the safe, measured pieces
first and isolates the heuristic:

| PR | Scope | Risk | Delivers |
|---|---|---|---|
| **PR-1** | Nature vocabulary (4 patterns, 0 constants) | lowest — settled by measurement | `cskg2` correct labels: −5,003 chars, +2 gold entities. AC-1, AC-2, AC-3 |
| **PR-2** | Under-segmentation detector | none — observability, no behaviour change | 5 warnings + span-count tracking. AC-8..AC-13, AC-21 |
| **PR-3** | Positional abstract | highest — two guards, three constants, layout-sensitive | `cskg2` +1,210-char abstract, closes cause (3). AC-4..AC-7 |

PR-3 is separable precisely because it is the one that could need reverting;
PR-1 and PR-2 stand without it. Only PR-1 and PR-3 change the baseline, so
`--update-baseline` runs twice, not three times.

## Sample Implementation

Validated against all eight PDFs, combined with SEG-3, before being written
here: 8/8 abstracts, `cskg2` matching gold, 25/30 entities, five warnings.

```python
# section_segmenter.py

# SEG-4 D1/D2: Nature Scientific Data vocabulary, added to SECTION_PATTERNS.
#   Background & Summary -> INTRODUCTION   (that journal's introduction;
#                                           mapping it to BACKGROUND costs
#                                           -15,932 chars, see spec D2)
#   Technical Validation -> EXPERIMENTS
#   Data Records         -> RESULTS
#   Usage Notes          -> DISCUSSION
SectionType.INTRODUCTION: [..., r"^background\s*(?:&|and)\s*summary\s*$"],
SectionType.EXPERIMENTS:  [..., r"^technical\s+validation\s*$"],
SectionType.RESULTS:      [..., r"^data\s+records\s*$"],
SectionType.DISCUSSION:   [..., r"^usage\s+notes\s*$"],

# SEG-4 D4: a positional abstract must read as prose, not as a title page.
# Prose width is RELATIVE to the document. Median body-line width is 106 on
# cskg2 (single column) but 59-69 on the two-column papers, so an absolute
# threshold works on one layout and silently finds nothing on the other.
_PROSE_WIDTH_FACTOR = 0.75
_MIN_SENTENCES = 2
_MAX_DIGIT_DENSITY = 0.05
_TITLE_PAGE_FURNITURE = re.compile(
    r"@|https?://|doi\.org|Contents lists available|ScienceDirect"
    r"|\baccepted\b|\breceived\b|www\.", re.IGNORECASE)

# SEG-4 D5: a section holding this much of the body is probable
# under-segmentation. Swallowed spans measure 41-100%; the largest healthy
# section is ~31%.
_UNDER_SEGMENTATION_SHARE = 0.40
_MIN_BODY_WORDS_FOR_CHECK = 500
_TAIL_TYPES = {SectionType.REFERENCES, SectionType.ACKNOWLEDGMENTS,
               SectionType.APPENDIX}


def segment(self, text: str) -> SegmentedDocument:
    doc = ...  # unchanged
    if doc.detected_structure:
        self._add_positional_abstract(text, doc)
        self._warn_under_segmentation(doc)
    return doc


def _add_positional_abstract(self, text, doc) -> None:
    """No abstract label anywhere (Nature Scientific Data prints none) -> the
    abstract is the run of prose-width lines just before the first heading.

    Runs AFTER normal segmentation so it can only fire when SEG-3's label
    patterns found nothing -- the two never both produce an abstract.
    """
    if any(s.section_type is SectionType.ABSTRACT for s in doc.sections):
        return
    first = min((s.start_char for s in doc.sections), default=None)
    if first is None:
        return

    widths = [len(l.strip()) for l in text.split("\n") if len(l.strip()) > 20]
    if not widths:
        return
    min_width = _PROSE_WIDTH_FACTOR * statistics.median(widths)

    lines = text[:first].split("\n")
    # `text[:first]` ends at a newline, so the split leaves a blank tail that
    # would break the run on its first step. Measured: dropping this is the
    # difference between finding cskg2's abstract and finding nothing.
    while lines and not lines[-1].strip():
        lines.pop()

    i = len(lines)
    while i > 0 and len(lines[i - 1].strip()) >= min_width:
        i -= 1
    span = "\n".join(lines[i:]).strip()

    if len(span.split()) < self.min_section_words:
        return
    # Guard 1 -- publisher furniture. Catches a span running from the document
    # start through the title block. Measured on 7 spans; each hits 3-4 terms.
    if _TITLE_PAGE_FURNITURE.search(span):
        logger.debug("positional abstract rejected (title-page furniture)")
        return
    # Guard 2 -- does it read as prose? Catches a pure metadata block, which
    # the denylist cannot see: cskg2's author block scores 0 sentences and
    # 13.7% digits. This covers a long author line absorbed into the run.
    sentences = len(re.findall(r"\.(?:\s|$)", span))
    digit_density = sum(c.isdigit() for c in span) / len(span)
    if sentences < _MIN_SENTENCES or digit_density >= _MAX_DIGIT_DENSITY:
        logger.debug("positional abstract rejected (not prose: %d sentences, "
                     "%.1f%% digits)", sentences, 100 * digit_density)
        return

    doc.sections.insert(0, Section(
        section_type=SectionType.ABSTRACT, title="(positional)",
        content=span, start_char=len("\n".join(lines[:i])), end_char=first))


def _warn_under_segmentation(self, doc) -> None:
    """One section holding most of the body means an unrecognized heading let
    its predecessor swallow the rest. Warn; do not guess the boundary."""
    body = [s for s in doc.sections if s.section_type not in _TAIL_TYPES]
    total = sum(s.word_count for s in body)
    if total < _MIN_BODY_WORDS_FOR_CHECK:
        return
    for index, section in enumerate(body):
        share = section.word_count / total
        if share < _UNDER_SEGMENTATION_SHARE:
            continue
        following = (body[index + 1].title if index + 1 < len(body)
                     else "(end of document)")
        logger.warning(
            "under-segmentation: %r (%s) holds %.1f%% of body words "
            "(%d of %d); next recognized heading is %r",
            section.title, section.section_type.value, 100 * share,
            section.word_count, total, following,
        )
```

## Edge Cases & Error Handling

### Trailing blank line breaks the prose run
- **Scenario**: `text[:first]` ends at a newline, so `split("\n")` yields a
  final `""`.
- **Behavior**: blank tail entries are dropped before walking backwards.
- **Test**: assert `cskg2`'s positional abstract is found and is 1,210 chars.
  **This is a real bug found while validating the sample implementation** — the
  first version silently produced no abstract at all.

### SEG-4 running without SEG-3 ("correct by accident")
- **Scenario**: `kg_validation_hitl`'s `A B S T R A C T` label is unmatched
  until SEG-3, so the positional rule fires and returns a 1,395-char span —
  which happens to be very nearly the right answer (SEG-3's label-based
  abstract is 1,411 chars).
- **Behavior**: accepted but **not relied upon**. Once SEG-3 lands, the label
  matches, an `ABSTRACT` section already exists, and the positional rule
  returns early. Every corpus number in this spec is measured with SEG-3
  applied for exactly this reason.
- **Test**: assert the positional rule does not fire on a document that already
  has a labelled abstract.

### A long author-block line adjacent to the abstract
- **Scenario**: the prose run walks back from the first heading and stops at
  the first line under 70 chars. On `cskg2`, line 5 (`& Enrico Motta2`,
  15 chars) breaks it. A paper whose author block *ends* with a long line would
  see that line absorbed into the abstract.
- **Behavior**: known limitation. The furniture guard catches most such cases
  (author lines usually carry emails or affiliations) but not all.
- **Test**: a fixture with a long trailing author line, asserting the guard
  rejects the span; recorded in Open Questions if it cannot.

### Abstract label exists but the abstract is short
- **Scenario**: a matched label with fewer than `min_section_words` of content.
- **Behavior**: normal segmentation already filters it, so no ABSTRACT section
  exists and the positional rule may fire. Accepted — a span of real prose is
  better than nothing.
- **Test**: assert no crash and a sane result.

### Documents with no recognized headings at all
- **Scenario**: `segment()` returns the single-UNKNOWN-section path.
- **Behavior**: neither addition runs — `detected_structure` is `False`.
- **Test**: assert an unstructured document produces no positional abstract and
  no warning.

### Single-section documents and short fixtures
- **Scenario**: a section legitimately holds 100% of body words because it is
  the only one, or a test fixture has 60 words.
- **Behavior**: the 500-body-word floor keeps the check silent on fixtures. A
  genuine single-section paper *does* warn at 100%, which is correct — that is
  exactly `cskg2`'s pre-fix state.
- **Test**: both directions.

### Gold record missing for a paper under `--entities`
- **Scenario**: five of eight papers have no gold file.
- **Behavior**: reported as `no gold` and scored on chars only; never counted
  as zero visible entities, which would look like catastrophic loss.
- **Test**: assert a paper without gold is skipped, not scored 0.

### Gold record present but empty of entities
- **Scenario**: a gold file with `expected_concepts: []`.
- **Behavior**: reported as `0/0` rather than dividing by zero.
- **Test**: assert no `ZeroDivisionError`.

## Acceptance Criteria

### AC-1: Nature *Scientific Data* headings classify
- **Given** `Background & Summary`, `Background and Summary`, `Data Records`,
  `Technical Validation`, `Usage Notes`
- **When** each is passed to `_classify_heading`
- **Then** `introduction`, `introduction`, `results`, `experiments`,
  `discussion` respectively. Must be **red** pre-fix.

### AC-2: `Background & Summary` maps to introduction, not background
- **Given** the mapping table
- **Then** `_classify_heading("Background & Summary")` is
  `SectionType.INTRODUCTION` and **not** `BACKGROUND`.
- **Rationale**: `background` is outside the keep-list; the literal reading
  costs 15,932 chars and hides the chain's spine concept. Asserted explicitly
  so a future "tidy-up" cannot silently flip it.

### AC-3: `cskg2` segments into the sections its gold records (corpus, local)
- **Given** the **real** `cskg2` PDF, via `scripts/measure_segmentation.py`
- **When** `segment()` runs
- **Then** the ordered types are `abstract`, `introduction`, `methods`,
  `results`, `experiments`, `discussion`, `references`, `acknowledgments`,
  with titles `(positional)`, `Background & Summary`, `Methods`,
  `Data Records`, `Technical Validation`, `Usage Notes`, … — matching
  `reconciled/paper_cskg2.gold.yml`'s hand-verified boundaries.
- **Note** (review, QA #1): this is asserted against the full PDF, **not** the
  committed excerpt. An excerpt with elided bodies would drop sections below
  `min_section_words` and could not produce this list. AC-19 states what the
  excerpt can honestly assert in CI.

### AC-4: The positional abstract lands on the prose run
- **Given** `cskg2`, which prints no abstract label
- **Then** an `abstract` section exists, is **1,210 chars**, starts with
  `The rapid evolution of AI`, and ends with `scientific question-answering.`

### AC-5: Title-page furniture is rejected
- **Given** each of the seven measured title-page spans (emails, DOIs, URLs,
  `Contents lists available`, `Received`/`accepted`)
- **When** offered to the positional rule
- **Then** no abstract is created, and one DEBUG line records the rejection.
- **Rationale**: without this, SEG-4-before-SEG-3 fabricates abstracts on six
  papers that SEG-3's AC-16 bounds would not catch.

### AC-6: The positional rule yields to a labelled abstract
- **Given** a document with a matched abstract label
- **Then** the positional rule returns early and exactly one `abstract`
  section exists.

### AC-7: The prose run survives a trailing blank line
- **Given** a head region ending at a newline
- **Then** blank tail entries are dropped and the run is found.
- **Rationale**: regression test for the bug found while validating the sample
  implementation — the first version produced no abstract at all.

### AC-8: The detector fires at or above 40% of body words
- **Given** a document whose largest non-tail section holds ≥40%
- **Then** exactly one warning per qualifying section is emitted at WARNING.

### AC-9: The detector is silent on healthy documents
- **Given** `hypothesis_generation`, whose largest section is 31.4%
- **Then** no under-segmentation warning is emitted.

### AC-10: The warning names the section, share, and next heading
- **Given** `cskg`'s `Related Work` at 67.6%
- **Then** the message contains the title, the type, the share to one decimal,
  the word counts, and `next recognized heading is 'Evaluation'`.

### AC-11: Tail types are excluded from the denominator
- **Given** a document whose `references` section is larger than its body
- **Then** `references` neither triggers a warning nor inflates the
  denominator.
- **Rationale**: `references` reaches 31.8% of total words on
  `fact_completion`; counting it would mask real swallowing.

### AC-12: The floor keeps the detector quiet on short documents
- **Given** a document with fewer than 500 body words
- **Then** no warning, even if one section holds 100%.

### AC-13: The detector lives in the segmenter
- **Given** `segment()` is called by any caller
- **Then** the warning is emitted from
  `agentic_kg.extraction.section_segmenter`, not from `ingestion.py`, and
  SEG-1's AC-13 partial-segmentation warning is unchanged.

### AC-14: `--entities` reports gold-entity visibility
- **Given** `scripts/measure_segmentation.py --entities`
- **Then** for each paper with a gold record it reports visible/total entity
  groups, matching canonical **or** any acceptable alias under word-boundary
  matching, and papers without gold are reported `no gold` rather than `0`.

### AC-15: `--entities` does not gate the exit code
- **Given** a run where entity visibility falls
- **Then** the exit code is decided by the char/section comparison as today,
  and the entity figures are reported for a human to read.

### AC-16: `cskg2` entity visibility improves and loses nothing
- **Given** the `--entities` run
- **Then** `cskg2` reports **25/30** visible, up from 23/30, with
  `scientific knowledge graph` and `knowledge-centric paradigm` newly visible
  and **zero** entities lost.

### AC-17: The corpus reaches 8 of 8 abstracts
- **Given** SEG-3 and SEG-4 both applied
- **Then** every paper has exactly one `abstract` section, and the combined
  per-paper char deltas against the SEG-1 baseline are: `cskg` +1,418,
  `cskg2` **−3,797**, `kg_construction_survey` +0, `llm_ontology_gen` +1,624,
  `fact_completion` +1,350, `kg_validation_hitl` +1,395,
  `hypothesis_generation` +1,633, `empire` +1,915 — total **+5,538**.

### AC-18: The baseline is re-based with a negative delta, justified
- **Given** `cskg2` legitimately loses 5,003 chars from correct labelling
- **Then** `scripts/seg_baseline.json` is updated via `--update-baseline`, and
  the PR description records the negative delta alongside the entity gain.
- **Rationale**: the first time a SEG item re-bases downwards. The harness will
  call it `REGRESSED`; the entity figures are the counter-evidence, and this AC
  exists so that is stated rather than waved through.

### AC-19: A committed fixture covers what an excerpt can assert
- **Given** `packages/core/tests/extraction/fixtures/segmenter/` gains a real
  extracted-text excerpt of `cskg2` — the journal header, title and author
  block, the lead paragraph, and all five Nature headings — with provenance in
  the directory README
- **Then** CI asserts against it, without needing the PDF: the five headings
  classify per AC-1; the positional abstract is created, is 1,210 chars, and
  begins `The rapid evolution of AI`; and **no part of the author or journal
  block appears in the abstract's content**.
- **Not asserted here**: the full ordered section list, which needs the whole
  PDF (AC-3). Stating that boundary is the point (review, QA #1).

### AC-20: The under-segmentation span count is tracked, not just logged
- **Given** the detector emits five expected warnings on this corpus, and will
  until SEG-5 and SEG-11 land
- **Then** `scripts/seg_baseline.json` records the expected per-paper count
  (`cskg` 1, `kg_construction_survey` 1, `fact_completion` 2,
  `kg_validation_hitl` 1, `cskg2` 0), and the harness reports
  `5 under-segmented spans (expected 5)` — flagging any deviation in either
  direction.
- **Rationale** (review, TL #2): an expected-to-fire warning is noise unless
  the count is the metric. This turns five warnings into a number SEG-5 and
  SEG-11 must drive down, and makes a *new* swallowing defect visible as 6
  rather than disappearing into a familiar wall of text.

### AC-21: The entity matcher is a shared primitive
- **Given** `--entities` needs a surface-form matching rule, and the
  importer-vs-gold diff (D-2 / V-1) will need the same one
- **Then** the rule — canonical plus acceptable aliases, word-boundary matched,
  grouped so one alias hit counts as the entity being visible — lives in a
  single importable helper that both can use, not inline in the script.
- **Rationale** (review, TL #3): the harness asks "could the importer have
  seen this?" and the diff asks "did the importer extract it?" — different
  questions, and the gap between the two answers is precisely the extractor's
  recall. That subtraction is only meaningful if both use the same definition
  of visible.

### AC-22: The docs record both causes closing
- **Given** SEG-4 lands with SEG-3
- **Then** `docs/ground-truth/segmenter-findings.md` marks cause (3) **fixed**
  (all five abstract conventions) and cause (4) **fixed for vocabulary**, notes
  that the four remaining swallowed spans are SEG-5/SEG-11 with the detector
  now watching them, and corrects the `cskg2` "0 of 34 entities lost" figure as
  an artifact of the swallowing; and `llm/features/BACKLOG.md` records that the
  detector does **not** cover phantom headings (SEG-11 keeps that half).

## Technical Notes

- **Affected components**:
  - `extraction/section_segmenter.py` — four patterns, `_MIN_PROSE_LEN`,
    `_TITLE_PAGE_FURNITURE`, `_UNDER_SEGMENTATION_SHARE`, `_TAIL_TYPES`,
    `_add_positional_abstract`, `_warn_under_segmentation`, and the two calls
    in `segment()`.
  - `packages/core/tests/extraction/test_section_segmenter.py` — AC-1..AC-13.
  - `fixtures/segmenter/` (+ README) — AC-19.
  - `scripts/measure_segmentation.py` — `--entities` (AC-14, AC-15);
    `scripts/seg_baseline.json` — AC-18.
  - `docs/ground-truth/segmenter-findings.md`, `llm/features/BACKLOG.md`,
    `reconciled/paper_cskg2.gold.yml` (notes only) — AC-20.
- **Patterns to follow**: SEG-1 set them — normalization and now these checks
  live in the segmenter; corpus claims go through `measure_segmentation.py`
  against a committed baseline; CI coverage comes from committed real-text
  excerpts because the PDFs are gitignored; warnings inform and never fail
  (SM-9).
- **New import**: `statistics` (stdlib) for the median line width. No new
  third-party dependency.
- **Data model changes**: none. `Section`/`SegmentedDocument` are unchanged;
  the positional abstract is an ordinary `Section` inserted at index 0.
- **Downstream**: `ingestion.py` is untouched. Six papers stop emitting SEG-1's
  partial-segmentation warning for `abstract`; `cskg2` stops emitting it
  entirely.
- **Fixture regeneration**: **not** required — the keep-list is unchanged. Note
  though that `cskg2`'s `paper_cskg2.txt` was built from hand-verified
  boundaries that this feature now reproduces, so the two should finally agree.

## Dependencies

- **Depends on SEG-1** (VERIFIED 2026-09-07) for the harness, baseline and
  fixture apparatus this extends.
- **Pairs with SEG-3** (SPECIFIED). SEG-3 should be implemented **first**: its
  label patterns make the positional rule's early-return the normal path, and
  every number here is measured with SEG-3 applied. The furniture guard means
  the reverse order is safe, not that it is intended.
- **Feeds SEG-5 and SEG-11**: the detector is the before/after signal for both.
  SEG-11 in particular must supply the phantom-heading half of the detection
  this feature deliberately leaves out.
- **Informs SEG-7**: the `Data Records` → `methods` temptation is direct
  evidence that the four-section keep-list is too narrow. SEG-4 declines to
  game it; SEG-7 should decide it openly.
- **Interacts with SEG-6**: an upper length guard would be the natural
  companion to the detector — the detector says "this section is implausibly
  large", SEG-6 says "and it must not be sent to one prompt".

## Open Questions

- **Is 40% the right threshold long-term?** It has a ~9-point margin today
  (41.3% lowest firing, ~31% largest healthy). As SEG-5 fixes the swallowing,
  sections shrink and the margin grows; as papers get shorter, healthy shares
  rise. Revisit if a healthy section ever false-fires.
- **The long-author-line limitation** in the prose run is now covered by the
  prose test rather than left open: a metadata block scores 0 sentences and
  13.7% digits on `cskg2`. What remains untested is a paper whose author block
  is *both* long-lined and prose-like; not reachable on this corpus.
- **Should `--entities` fall back from `reconciled/` to `human/`?** Proposed
  yes, so `empire` (human-only) contributes. Confirm the precedence and whether
  `claude/` should ever be read (proposed: never — it is an input to
  reconciliation, not an answer key).
- **`(positional)` as a title.** It marks a section whose heading does not
  exist, which is honest but unlike every other title. An alternative is the
  first few words of the span. Left to implementation.
- **Does the detector belong in the smoke assertions?** `scripts/smoke_assert.py`
  checks graph shape; an under-segmentation count could become a CI signal once
  the four known spans are fixed. Premature while five warnings are expected.
