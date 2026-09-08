# Feature: SEG-3 — Run-In and Letter-Spaced Abstracts Defeat the Standalone-Line Pattern

**Status:** SPECIFIED
**Date:** 2026-09-07
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SEG-3
**Source:** [`docs/ground-truth/segmenter-findings.md`](../../docs/ground-truth/segmenter-findings.md) root cause (3)

> **⚠️ Sequencing decision (review, TL #3): spec SEG-4 before implementing
> this.** SEG-3 covers four of the five abstract conventions; `cskg2`'s
> no-label case is deferred to SEG-4 because a positional rule without SEG-4's
> terminator is *measurably worse than nothing* (12,157 chars, swallowing the
> paper's introduction). Rather than leave root cause (3) "partially fixed"
> for an indefinite period behind a **Needs Decision** item, SEG-4 gets a spec
> next and the two ship as a pair so cause (3) closes in one release. This spec
> is complete and implementable on its own; the gate is deliberate, not
> technical.

## Problem

`SectionType.ABSTRACT` has exactly two patterns, and both require the keyword
alone on its own line:

```python
SectionType.ABSTRACT: [
    r"^abstract\s*$",
    r"^summary\s*$",
]
```

Publishers do not typeset abstracts that way. Across the eight
ground-truth-chain papers there are **five distinct conventions and the pattern
matches one of them**, so only `kg_construction_survey` yields an abstract at
all. The abstract is one of the four types
`_build_extractor_section_text` (`ingestion.py:198`) keeps, so on the other
seven papers the single densest, highest-signal section of the paper reaches the
extractors as nothing.

Measured against the real PyMuPDF output (line indices are from the extracted
text, not the PDF page):

| Paper | Publisher | Label line, verbatim | Len | Today |
|---|---|---|---:|---|
| `kg_construction_survey` | SSRN preprint | `Abstract` | 8 | ✅ matches |
| `llm_ontology_gen` | Elsevier IPM | `A B S T R A C T` | 15 | ❌ |
| `kg_validation_hitl` | Elsevier IPM | `A B S T R A C T` | 15 | ❌ |
| `hypothesis_generation` | Elsevier KnoSys | `A B S T R A C T` | 15 | ❌ |
| `cskg` | Springer LNCS | `Abstract. In recent years, we saw the emergence of several approaches` | 69 | ❌ |
| `fact_completion` | IEEE Access | `ABSTRACT In the last few years, we have witnessed the emergence of several knowledge graphs that` | 96 | ❌ |
| `empire` | IEEE ESEM | `Abstract—[Background.] Empirical research in requirements` | 57 | ❌ |
| `cskg2` | Nature *Scientific Data* | *(no label — the abstract is the lead paragraph)* | — | ❌ |

### Three things the findings doc got slightly wrong

The findings doc groups all four failures under "run-in abstracts." Measuring
them separately changes the shape of the fix:

1. **The Elsevier form is not run-in.** `A B S T R A C T` is a *standalone*
   line that the PDF extractor letter-spaced. It needs de-spacing, not
   delimiter handling — and de-spacing alone fixes **three of the eight
   papers**, independent of any run-in work.
2. **Run-in matching would silently discard the abstract's opening words.**
   `_extract_sections` starts a section's content at the end of the heading
   *line*, so classifying a run-in line as a heading throws away the body text
   sharing it: 60 chars on `cskg`, 87 on `fact_completion`, 48 on `empire`.
   Worse than the character count, the kept abstract would then begin
   mid-sentence — `empire`'s would open
   `engineering (RE) is a constantly evolving topic, with...`.
3. **`fact_completion` clears the length guard by four characters.**
   `_find_headings` skips any line longer than `max_heading_length` (100)
   *before* classifying it. That line is 96. Line wrapping is a property of the
   PDF and the extractor version, so a re-extraction could silently un-fix this
   paper with no error.

### Why it matters now

SEG-1 landed the Roman-numeral fix, so `fact_completion` and `empire` are
ingested rather than dropped — but neither has an abstract, and the
importer-vs-gold diff would read those as extractor recall failures. Gold was
built from hand-verified boundaries in which every one of the eight papers has a
real abstract, so every gold entity drawn from an abstract is currently
unreachable.

## Goals

- **7 of 8 papers yield an abstract**, up from 1 of 8 (measured).
- **+9,335 chars** of extractor input across six papers (measured):
  `empire` +1,915, `hypothesis_generation` +1,633, `llm_ontology_gen` +1,624,
  `cskg` +1,418, `kg_validation_hitl` +1,395, `fact_completion` +1,350.
- Every recovered abstract **begins at a sentence boundary**, because the
  run-in remainder is kept.
- **No paper regresses**, and `cskg2` is byte-identical.
- The length guard stops being a four-character cliff.

## Non-Goals

- **The no-label positional case (`cskg2`) — deferred to SEG-4.** Nature
  *Scientific Data* prints no `Abstract` label; its abstract is the lead
  paragraph (lines 6–17), ending immediately before `Background & Summary` at
  line 18. Catching it needs a positional rule, and a positional rule needs a
  terminator. `Background & Summary` is unrecognized until SEG-4, so **measured
  today an uncapped positional rule runs 109 lines to `Methods` and produces a
  12,157-char "abstract"** that swallows the paper's real introduction plus page
  furniture (`www.nature.com/scientificdata`, `Data Descriptor`, `OPEN`). This
  is a hard ordering, not a preference: add the vocabulary first and the rule
  terminates correctly with no length cap and no tuning constant. Until SEG-4
  ships, **root cause (3) is only partially fixed.**
- **Run-in forms of other section types.** No other type appears in run-in
  form anywhere in this corpus, and `summary` in run-in form is a landmine
  (`Summary of the results ...` is a sentence). The mechanism generalizes if a
  real case turns up; the vocabulary does not grow speculatively.
- **Universal whitespace collapsing.** De-spacing applies *only* to lines that
  are entirely letter-spaced. Collapsing whitespace on every candidate would
  turn `Related Work` into `RelatedWork` and break that pattern.
- **SEG-5 / SEG-11.** `fact_completion` and `empire` still have no methods
  section after SEG-3 (`III. SciCheck`, `IV. RESEARCH APPROACH`), and the
  phantom single-word headings remain.
- **SEG-2.** `segment_with_abstract` stays untouched — its own regex is
  unaffected and SEG-2 owns the fix-or-delete decision. Note the irony that
  SEG-3 makes it *more* redundant, since `segment()` now finds abstracts.

## User Stories

- As the importer-vs-gold diff, I want every paper's abstract in the extractor
  input, so that a gold entity drawn from an abstract is reachable rather than
  a guaranteed miss.
- As an entity extractor, I want the abstract to start at a sentence boundary,
  so I am not asked to read a fragment beginning mid-clause.
- As an operator re-extracting a PDF, I don't want a four-character shift in
  line wrapping to silently remove a paper's abstract.
- As whoever ships SEG-4, I want the positional abstract case handed over with
  its measurement already done.

## Design Approach

### Decision 1 — Labeled conventions only; `cskg2` waits for SEG-4

See Non-Goals. SEG-3 stays a pattern-and-offset change with no new heuristic
and no tuning constant.

### Decision 2 — De-space only fully letter-spaced lines

A second normalization step in `_classify_heading`, alongside SEG-1's
enumerator strip. Applies when every token in the line is a single letter:

```
"A B S T R A C T"        -> "ABSTRACT"     -> abstract      ✅
"A R T I C L E I N F O"  -> "ARTICLEINFO"  -> UNKNOWN       ✅ (correct)
"Related Work"           -> untouched      -> related_work  ✅
```

Elsevier letter-spaces exactly two headings in these papers, verified across
all three of its titles: `A R T I C L E I N F O` and `A B S T R A C T`. The
former collapses to a token matching no pattern, which is the right outcome.

### Decision 3 — Keep the run-in remainder as content

The run-in matcher consumes the label **and its delimiter**, and reports the
offset where it stopped. `_find_headings` then records that offset as the
heading's end, so `_extract_sections` — unchanged — starts the section's
content immediately after the label, on the same line.

```
'Abstract—[Background.] Empirical research in requirements'
 └─ title ─┘└────────────── content starts here ───────────
```

`Section.title` becomes the label (`Abstract—`, `ABSTRACT`), not the whole
line. This is a change to `_find_headings`' offset arithmetic, not to the
tuple shape or to `_extract_sections`, and it generalizes: any future run-in
heading gets the same treatment.

### Decision 4 — Whitespace is a delimiter only after an ALL-CAPS label

The delimiter set is `.` `—` `–` `-` `:` for any case, plus **whitespace only
after `ABSTRACT` in full caps**:

| Line | Loose rule | Shipped rule |
|---|---|---|
| `ABSTRACT In the last few years...` | ✅ | ✅ |
| `Abstract. In recent years...` | ✅ | ✅ |
| `Abstract—[Background.] Empirical...` | ✅ | ✅ |
| `Abstract syntax trees are used to...` | ❌ **false heading** | ✅ rejected |
| `Abstract representations of KGs...` | ❌ **false heading** | ✅ rejected |

Both variants score **zero false positives on these eight papers**, so the
corpus cannot separate them — but this project's domain is computer science,
where "abstract syntax tree" and "abstract representation" are ordinary prose.
A false abstract heading mid-paper is not a harmless mislabel: it opens a new
span and therefore *truncates* whatever section contained that line. The
case constraint costs nothing and removes the whole class.

**On the charge of speculative hardening** (review, TL #2). The findings doc
rightly criticizes the four-section keep-list for having an *unmeasured
precision benefit* against a *measured, large recall cost*. This constraint is
not that shape:

| | keep-list | case constraint |
|---|---|---|
| Precision benefit | unmeasured | unmeasured |
| Recall cost | **measured, large** | **measured, zero** |

Nothing is traded away — the constraint catches all three real run-in forms.
Given a destructive failure mode and no measured cost, pre-empting the
hypothetical is the cheap side of an asymmetric bet.

### Design note — why the run-in table is separate

SEG-1's central win was collapsing numbering into **one** normalization step so
later vocabulary additions could not forget it. A second table is a real
tension with that (review, TL #1), and it is justified on a narrow ground:

```
SECTION_PATTERNS   text -> SectionType
_RUN_IN_HEADINGS   text -> (SectionType, label_end)
```

The second return value is the entire reason the table exists — content
retention (Decision 3) needs the offset where the label stops, and
`SECTION_PATTERNS` has no slot for it. Folding run-in forms in would mean all
40 patterns carry an offset that 39 of them never use, or changing
`_classify_heading`'s return type, which every caller and test touches.

**The constraint that keeps this honest:** `_RUN_IN_HEADINGS` stays minimal —
abstract only. It is a mechanism for a structural property (a heading sharing
its line with body text), not a shadow vocabulary. If a second type ever needs
run-in handling, that is the moment to revisit unification, not now.

### Decision 5 — The length guard tests the label, not the line

`_find_headings` currently discards a long line before classifying it. For a
run-in heading the relevant length is the label; the body text trailing it is
irrelevant. So a long line is skipped only if it does **not** begin with a
run-in label:

```
'ABSTRACT In the last few years...'  (96 chars)  -> abstract  (was: 4 chars from being dropped)
'ABSTRACT In the last few years...' (140 chars)  -> abstract
'We evaluate our approach on seven...' (140)     -> skipped   (unchanged)
```

## Sample Implementation

Validated against all eight PDFs before being written here: 7 of 8 abstracts,
+9,335 chars, zero regressions, `cskg2` byte-identical.

```python
# section_segmenter.py

# SEG-3: a letter-spaced line is one whose every token is a single letter.
# Collapsing whitespace is safe ONLY for these -- doing it universally would
# turn "Related Work" into "RelatedWork" and break that pattern.
_LETTER_SPACED = re.compile(r"^(?:[A-Za-z]\s){2,}[A-Za-z]\s*$")

# Run-in labels. Each pattern consumes the label AND its delimiter, so
# match.end() is where the section's content begins on that same line.
# Whitespace is a delimiter only after an ALL-CAPS label: a mixed-case
# "Abstract syntax trees are..." is ordinary prose in this domain.
_RUN_IN_HEADINGS = (
    (re.compile(r"^ABSTRACT\s+(?=\S)"), SectionType.ABSTRACT),
    (re.compile(r"^abstract\s*[—–\-.:]\s*", re.IGNORECASE), SectionType.ABSTRACT),
)


def _match_run_in(stripped: str) -> Optional[tuple[SectionType, int]]:
    """Return (type, offset-after-label) for a run-in heading, else None.

    A bare "Abstract" line matches neither pattern (the first needs trailing
    text, the second a delimiter) and falls through to the standalone
    pattern -- so this does not double-handle the case that already worked.
    """
    for pattern, section_type in _RUN_IN_HEADINGS:
        match = pattern.match(stripped)
        if match:
            return section_type, match.end()
    return None


class SectionSegmenter:

    def _classify_heading(self, heading_text: str) -> SectionType:
        cleaned = _ENUMERATOR.sub("", heading_text.strip(), count=1)  # SEG-1
        if _LETTER_SPACED.match(cleaned):                             # SEG-3
            cleaned = re.sub(r"\s+", "", cleaned)
        ...  # unchanged pattern loop

    def _find_headings(self, text):
        headings = []
        current_pos = 0
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                current_pos += len(line) + 1
                continue

            run_in = _match_run_in(stripped)

            # SEG-3 Decision 5: the guard applies to the label, not to the
            # body text trailing it. fact_completion's run-in line is 96 of
            # the allowed 100 -- a four-character cliff.
            if run_in is None and len(stripped) > self.max_heading_length:
                current_pos += len(line) + 1
                continue

            if run_in is not None:
                section_type, label_end = run_in
                # SEG-3 Decision 3: content starts after the LABEL, so the
                # abstract keeps its opening words and reads as prose.
                indent = len(line) - len(line.lstrip())
                headings.append((
                    current_pos,
                    current_pos + indent + label_end,
                    stripped[:label_end].strip(),   # title is the label
                    section_type,
                ))
            else:
                section_type = self._classify_heading(stripped)
                if section_type != SectionType.UNKNOWN:
                    headings.append((current_pos, current_pos + len(line),
                                     stripped, section_type))
                else:
                    # SEG-1 AC-14: rejections are the only record of a
                    # SEG-4/5-class miss. Must survive this change.
                    logger.debug("unmatched candidate heading: %r", stripped)

            current_pos += len(line) + 1
        return headings
```

## Edge Cases & Error Handling

### Standalone `Abstract` — the one case that already worked
- **Scenario**: `kg_construction_survey`'s bare `Abstract` line.
- **Behavior**: unchanged. Neither run-in pattern matches (no trailing text,
  no delimiter), so it falls through to `^abstract\s*$`.
- **Test**: classification plus a corpus assertion that the paper's char count
  is unchanged.

### Mixed-case label followed by whitespace
- **Scenario**: `Abstract syntax trees are used to represent...` mid-paper.
- **Behavior**: `UNKNOWN`. Whitespace is a delimiter only after all-caps.
- **Test**: table-driven, including `Abstract representations of KGs...`.

### Plural and suffixed forms
- **Scenario**: `ABSTRACTS` / `Abstracts of the papers were...`.
- **Behavior**: `UNKNOWN` — the patterns require whitespace or a delimiter
  immediately after `abstract`, and `s` is neither.
- **Test**: table-driven.

### Letter-spaced non-abstract heading
- **Scenario**: `A R T I C L E I N F O` (present in all three Elsevier papers).
- **Behavior**: collapses to `ARTICLEINFO`, matches nothing, `UNKNOWN`.
- **Test**: assert `UNKNOWN`, and assert it appears in the AC-14 DEBUG stream.

### De-spacing must not touch ordinary multi-word headings
- **Scenario**: `Related Work`, `Data Records`, `Future Work`.
- **Behavior**: `_LETTER_SPACED` requires every token to be one letter, so
  these are untouched and classify as before.
- **Test**: assert `Related Work` still returns `related_work`; assert
  `_LETTER_SPACED` does not match it.

### Two-letter and degenerate spaced lines
- **Scenario**: `A B`, `I V`, a single letter `A`.
- **Behavior**: `_LETTER_SPACED` needs ≥3 letters, so `A B` is untouched; all
  collapse to nothing matching a pattern anyway. `UNKNOWN`.
- **Test**: table-driven.

### Run-in line at the length boundary
- **Scenario**: the same run-in abstract at 96 chars and at 140 chars.
- **Behavior**: both classify. A 140-char line that is *not* a run-in heading
  is still skipped.
- **Test**: both directions, so the guard is not simply disabled.

### Indented run-in line
- **Scenario**: a run-in label preceded by leading whitespace.
- **Behavior**: the content offset accounts for the indent
  (`current_pos + indent + label_end`), so content starts at the right
  character.
- **Test**: assert the extracted content begins with the expected word for an
  indented fixture line.

### Abstract absorbs trailing keyword blocks
- **Scenario**: `cskg`'s `Keywords: Knowledge graph · Scholarly data ...` and
  `fact_completion`'s `INDEX TERMS ...` sit between the abstract and the
  introduction and are not recognized headings, so they land inside the
  abstract span.
- **Behavior**: **accepted.** A few dozen characters of author-chosen keywords
  is arguably useful extractor signal, and excluding it would need new
  vocabulary that belongs with SEG-4.
- **Test**: none asserting their absence; the spans are pinned by the corpus
  char counts, so a change shows up as a delta.

## Acceptance Criteria

### AC-1: Letter-spaced abstract labels classify
- **Given** `A B S T R A C T` (and `a b s t r a c t`)
- **When** passed to `_classify_heading`
- **Then** `SectionType.ABSTRACT`. Must be **red** pre-fix.

### AC-2: Run-in abstract labels with punctuation classify
- **Given** `Abstract. In recent years, we saw the emergence of several approaches`
  and `Abstract—[Background.] Empirical research in requirements`
- **When** each line is offered to `_find_headings`
- **Then** each yields one `abstract` heading. Must be **red** pre-fix.

### AC-3: Run-in abstract label with caps-and-whitespace classifies
- **Given** `ABSTRACT In the last few years, we have witnessed the emergence of several knowledge graphs that`
- **When** offered to `_find_headings`
- **Then** one `abstract` heading. Must be **red** pre-fix.

### AC-4: The standalone form is unchanged
- **Given** every abstract assertion in the existing test suite, plus a bare
  `Abstract` line
- **When** the suite runs
- **Then** all pass with no edits to existing test bodies.

### AC-5: The run-in remainder is kept, and the title is the label
- **Given** a document whose abstract label line is
  `Abstract—[Background.] Empirical research in requirements`
- **When** `segment()` runs
- **Then** the `abstract` section's `title` is `Abstract—`, and its `content`
  **starts with** `[Background.] Empirical research in requirements` — not with
  the following line.

### AC-6: Mixed-case label plus whitespace stays unrecognized (Decision 4 lock)
- **Given** `Abstract syntax trees are used to represent programs`,
  `Abstract representations of knowledge graphs are common`,
  `Abstracts of the papers were reviewed`, `ABSTRACTS`
- **When** each is offered to `_find_headings`
- **Then** no heading is produced for any of them.

### AC-7: De-spacing is scoped to fully letter-spaced lines (Decision 2 lock)
- **Given** `A R T I C L E I N F O`, `Related Work`, `A B`, `A`
- **When** each is classified
- **Then** `ARTICLEINFO` → `UNKNOWN`; `Related Work` → `related_work`;
  the degenerate cases → `UNKNOWN`.

### AC-8: The length guard tests the label (Decision 5 lock)
- **Given** a run-in abstract line of 140 chars, and a 140-char body sentence
- **When** both are offered to `_find_headings`
- **Then** the first yields an `abstract` heading; the second yields nothing.

### AC-9: Seven of eight papers yield an abstract (corpus, local)
- **Given** the eight PDFs in `ground-truth-papers/`
- **When** `scripts/measure_segmentation.py` runs
- **Then** every paper except `cskg2` has exactly one `abstract` section, up
  from one paper in eight.

### AC-10: The measured per-paper deltas land (corpus, local)
- **Given** the same run
- **Then** the deltas against the SEG-1 baseline are `empire` **+1,915**,
  `hypothesis_generation` **+1,633**, `llm_ontology_gen` **+1,624**, `cskg`
  **+1,418**, `kg_validation_hitl` **+1,395**, `fact_completion` **+1,350**,
  and `kg_construction_survey` **+0** — total **+9,335**.

### AC-11: The run-in remainder is retained, character-exactly
- **Given** the three run-in fixtures
- **Then** each abstract's content starts with the **literal** text following
  its label in the real PDF:

  | Fixture | `content.startswith(...)` |
  |---|---|
  | `cskg` (Springer) | `In recent years, we saw the` |
  | `empire` (IEEE ESEM) | `[Background.] Empirical research` |
  | `fact_completion` (IEEE Access) | `In the last few years, we have` |

- **And** (corpus-level, all seven) each abstract begins with a capital letter
  or an opening bracket, as a sanity check on the four non-run-in papers.
- **Rationale** (review, QA #2): a "starts with a capital" heuristic alone
  would **pass** the exact `empire` failure this decision exists to prevent —
  the discarded-remainder version begins `Empirical research in requirements`,
  capitalized but mid-sentence. The literal assertions also fail on an
  off-by-one in the `indent + label_end` arithmetic, which no proxy catches.

### AC-12: `cskg2` is byte-identical (corpus, local)
- **Given** the same run
- **Then** `cskg2`'s char count **and** its `(type, title)` sequence are
  unchanged, confirming no accidental positional behaviour crept in.

### AC-13: No paper regresses, and the baseline is re-based deliberately
- **Given** `scripts/measure_segmentation.py`
- **Then** no paper reports `REGRESSED`, and `scripts/seg_baseline.json` is
  updated via `--update-baseline` with the per-paper deltas quoted in the PR
  description (the re-baseline obligation SEG-1 established).

### AC-14: SEG-1's DEBUG stream survives
- **Given** `_find_headings` is being restructured
- **When** a document with unmatched candidate headings is segmented at DEBUG
- **Then** one `unmatched candidate heading: <repr>` line per rejection is
  still emitted, and `A R T I C L E I N F O` appears among them.

### AC-15: One committed fixture per convention
- **Given** `packages/core/tests/extraction/fixtures/segmenter/`
- **Then** it gains real extracted-text excerpts for the Springer run-in,
  Elsevier letter-spaced, and IEEE ESEM run-in conventions, each with
  provenance recorded in the directory README alongside SEG-1's IEEE Access
  excerpt; and each is asserted at the document level, not only via
  `_classify_heading`.

### AC-16: An abstract is bounded, and something follows it
- **Given** each of the seven recovered abstract sections
- **Then** its content length is within a plausible range — the measured seven
  span 1,182–1,915 chars, so `500 <= len <= 4000` — **and** the section
  immediately following it is `introduction` or `related_work`.
- **Rationale** (review, QA #1): the harness scores *more* chars as
  `IMPROVED`, so an abstract that swallowed the following section would be
  reported as a success, not a regression. The `(type, title)` sequence in the
  baseline only catches this when the swallowed section was already
  recognized — which is exactly not true of `cskg2`. This is the assertion
  that fails on a 12,157-char span, and **SEG-4's positional rule inherits it
  as its tripwire.**

### AC-17: The docs record what is and is not fixed
- **Given** SEG-3 lands
- **Then** `docs/ground-truth/segmenter-findings.md` cause (3) is marked
  **partially fixed** — four of five conventions — with the no-label case
  explicitly handed to SEG-4 and the 12,157-char measurement recorded there as
  the reason for the ordering; `llm/features/BACKLOG.md`'s SEG-4 row states
  that it inherits the positional abstract; and **`cskg2`'s gold file notes
  record that its abstract is unreachable pending SEG-4**, so a diff run
  against it is read as a known segmenter deferral rather than an extractor
  recall failure.
- **Rationale** (review, QA #3): the production warning stays honest and
  stateless — it will keep reporting `missing ['abstract']` on `cskg2`, which
  is factually true. Disambiguating a *known* gap from a new regression is a
  documentation job, deliberately **not** a per-paper allowlist in production
  logging, which would rot the moment the corpus changes.

## Technical Notes

- **Affected components**:
  - `packages/core/src/agentic_kg/extraction/section_segmenter.py` —
    `_LETTER_SPACED`, `_RUN_IN_HEADINGS`, `_match_run_in`, de-spacing in
    `_classify_heading`, offset arithmetic and the length guard in
    `_find_headings`.
  - `packages/core/tests/extraction/test_section_segmenter.py` — AC-1..AC-8,
    AC-14.
  - `packages/core/tests/extraction/fixtures/segmenter/` (+ README) — AC-15.
  - `scripts/seg_baseline.json` — AC-13.
  - `docs/ground-truth/segmenter-findings.md`, `llm/features/BACKLOG.md` —
    AC-16.
- **Patterns to follow**: SEG-1 set all of them — normalization lives in one
  place in `_classify_heading`; the pattern table carries no prefixes or
  delimiters; `Section.title` keeps raw text; corpus claims go through
  `scripts/measure_segmentation.py` with a committed baseline; CI coverage
  comes from committed real-text excerpts because the PDFs are gitignored.
- **Data model changes**: none. No new `SectionType`, no `Section` field, no
  change to `_find_headings`' tuple shape or to `_extract_sections`.
- **Downstream**: none. `pipeline.py:475` calls `.segment()` and
  `ingestion.py:198` keeps four types; the abstract is already one of them, so
  the gain flows through with no wiring change. Six papers will stop emitting
  SEG-1's AC-13 partial-segmentation warning for `abstract`.
- **Fixture regeneration**: **not** required. The keep-list is unchanged, so no
  `paper_<slug>.txt` or gold file is invalidated.

## Dependencies

- **Depends on SEG-1** (shipped 2026-09-07): the enumerator normalization this
  extends, and the harness/baseline/fixture apparatus this reuses.
- **Gated on SEG-4 being specified** (review, TL #3). Implementation of SEG-3
  waits until SEG-4 has a spec, so root cause (3) closes as a pair rather than
  sitting partially fixed behind a Needs-Decision item. Nothing in SEG-3's
  design depends on SEG-4's outcome — the gate is about not leaving a known
  hole open indefinitely. If SEG-4 stalls, revisit: shipping SEG-3's measured
  +9,335 chars alone is still strictly better than the status quo.
- **Hands off to SEG-4**: the no-label positional abstract, with its
  measurement already recorded and AC-16 as its ready-made tripwire. SEG-4
  must land the Nature *Scientific Data* vocabulary before that rule can
  terminate correctly.
- **Blocks**: the importer-vs-gold diff, jointly with SEG-4/SEG-5/SEG-7 — every
  gold entity drawn from an abstract is unreachable until this ships.
- **Interacts with SEG-2**: SEG-3 makes `segment_with_abstract` more clearly
  redundant, which is evidence for the "delete" side of that decision. Not
  resolved here.
- **Interacts with SEG-11**: Decision 5 loosens the length guard for run-in
  labels only, deliberately *not* raising `max_heading_length` globally, which
  would widen the phantom-heading surface.

## Open Questions

- **Should `summary` gain run-in handling?** It shares the ABSTRACT type but
  `Summary of the results...` is a sentence, so the case constraint that saves
  `abstract` does not obviously transfer. Left out until a real paper needs it.
- **Is `Keywords:` / `INDEX TERMS` worth excluding from the abstract span?**
  Currently absorbed (a few dozen chars). Excluding it needs vocabulary that
  belongs with SEG-4; it may also be useful extractor signal, so it is not
  self-evidently a defect.
- **Should the abstract be bounded?** SEG-6 will add an upper length guard
  between the segmenter and the LLM call. A 12,157-char abstract of the kind
  `cskg2` would produce is exactly what such a guard should flag, which is an
  argument for sequencing SEG-6 before SEG-4's positional rule.
- **Excerpt provenance policy.** AC-15 adds three more third-party text
  excerpts. SEG-1 recorded source and licence per fixture in the directory
  README; confirm that remains sufficient as the count grows.
