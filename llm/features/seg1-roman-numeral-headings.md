# Feature: SEG-1 — Roman-Numeral Section Headings Never Match

**Status:** VERIFIED
**Date:** 2026-09-06 (specified) / 2026-09-07 (implemented + verified)
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SEG-1

> **Implementation note (2026-09-07).** All 15 ACs met; +118 tests. Measured
> outcome reproduced against the real corpus: `fact_completion` 0 → **25,586**,
> `empire` 12,405 → **17,953**, six papers byte-identical including their
> `(type, title)` sequences. Two changes to what this spec prescribed, both
> from the Phase 5 adversarial review:
>
> 1. **`_found_section_types` treats an empty-content section as not found.**
>    Not in the spec. Without it a document carrying an empty `abstract`
>    section would suppress the AC-13 warning while contributing nothing to
>    the extractor input — the two helpers would disagree about what the
>    extractors will see.
> 2. **AC-5 gained two assertions** beyond the prescribed 14-row table: every
>    `SectionType` except `UNKNOWN` has ≥1 pattern, and the class-level
>    `_compiled_patterns` cache covers the source table (the stale-cache edge
>    case this spec documents).
>
> AC-11's structural assertion was kept, not downgraded — see Open Questions.
> The three rejected variants (`[A-Z]`, hierarchical `4.4.`, optional
> separator) were each re-introduced as a mutation to confirm the suite fails:
> 4, 3 and 7 tests respectively.

> **Verification (2026-09-07).** All four Constellize gates pass.
> `section_segmenter.py`, `ingestion.py` and `measure_segmentation.py` are all
> at **100% line coverage** (542 statements, 0 missed), 229 tests across the
> feature's suites. Three fixes were applied *during* verification:
>
> 1. **Gate 1** initially left 4 lines uncovered — `segment_with_abstract`'s
>    dead `if abstract_match:` branch, i.e. the SEG-2 defect. Rather than
>    pragma it or touch the code AC-12 protects, two tests now reach the
>    branch with a synthetic document whose first characters are the abstract
>    label. That is the *only* input shape that reaches it, which pins the
>    SEG-2 defect precisely: `^` without `re.MULTILINE` anchors to offset 0,
>    and every real PDF opens with a title block.
> 2. **Gate 1** also covered the pre-existing `_paper_has_footprint` helper
>    (3 lines, previously untested anywhere) to reach 100% on `ingestion.py`.
> 3. **Gate 2** found an unvalidated external input: the baseline JSON is
>    hand-editable, and a wrong shape (a list, or a slug mapped to a scalar)
>    produced a raw `AttributeError` from `compare`. `_load_baseline` now
>    validates the shape and names the offending slugs.
>
> Deliberately **not** fixed, as pre-existing and outside this feature: 16
> suite failures and one `agentic-kg ingest --help` traceback, all Windows-host
> issues in test/CLI code (cp1252 decode of em dashes, POSIX path assumptions,
> bash path mangling) — verified identical on clean `HEAD` and green on the
> Linux CI runner; 25 ruff findings in `packages/api/src`, which no CI gate
> lints; one E501 in this file's pre-existing test-tree lines (SM-5 debt).
**Source:** [`docs/ground-truth/segmenter-findings.md`](../../docs/ground-truth/segmenter-findings.md) root cause (1)

## Problem

Every heading pattern in `SectionSegmenter.SECTION_PATTERNS`
(`packages/core/src/agentic_kg/extraction/section_segmenter.py:112`) admits only
Arabic section numbering:

```python
SectionType.INTRODUCTION: [
    r"^(?:\d+\.?\s*)?introduction\s*$",
    ...
]
```

IEEE-format papers number their sections with Roman numerals, so **not one
top-level heading in an IEEE paper is recognized**. Two of the eight
ground-truth-chain papers are IEEE-formatted. On `fact_completion` (IEEE Access)
the effect is total: the only heading matching anything is the unnumbered
`REFERENCES`, so `_build_extractor_section_text` (`ingestion.py:198`) returns
`""`.

Measured with the real `SectionSegmenter.segment()` over the eight PDFs in
`ground-truth-papers/`, counting only the four types the extractor input keeps
(`abstract` / `introduction` / `methods` / `experiments`):

| Paper | Format | Chars fed to extractors | Headings recognized |
|---|---|---:|---|
| `fact_completion` | IEEE Access | **0** | `REFERENCES` only |
| `empire` | IEEE ESEM | 12,405 | one phantom `Method` block (see Notes) |

`empire`'s real headings — `I. INTRODUCTION`, `II. BACKGROUND`,
`III. RELATED WORK`, `V. RESULTS`, `VI. THREATS TO VALIDITY`, `VII. DISCUSSION`,
`VIII. CONCLUSION` — are all unmatched, so the paper has no recognized
introduction at all.

### Correction to the findings doc: the consequence is a *dropped paper*, not a silent zero

The findings doc (2026-07-27) states this yields "zero entities, silently and
with no error." **That is now stale for the production ingest path.** SM-1 moved
`_build_extractor_section_text` to *before* the usability gate
(`ingestion.py:291-296`), so today `fact_completion` is:

```
WARNING  PDF source too thin (<url>): 0 chars
```

counted as `failed_thin` in `result.acquisition_failures`, recorded in
`extraction_errors[doi]` as `No usable full text (failed_thin)`, and the paper
is **skipped** (`ingestion.py:553-565`). So the real cost of SEG-1 is not a
silent zero — it is that a paper carrying **32 of the 53 chain entities** is
discarded from the run entirely, and would read in the diff as an acquisition
failure rather than a segmentation failure.

**What is still silent is the partial case.** `cskg` yields 8,917 chars with no
abstract and no methods, clears the 250-char gate, logs
`INFO Full text acquired`, and is indistinguishable from a healthy paper. That
gap is addressed by AC-13 below.

**Why it matters now.** SEG-1 gates the importer-vs-gold diff. The gold labels
come from hand-verified boundaries (`scripts/segment_ground_truth.py`), so they
describe what the importer *intends* to read. Diffing before this is fixed
reports `fact_completion`'s whole entity set as a failure of the wrong
component. SEG-1 is also a prerequisite for SEG-2: `segment_with_abstract`'s
terminating lookahead needs Roman-numeral support to find the end of an abstract
in an IEEE paper.

## Goals

- `fact_completion` goes from **0** to **25,586** chars of extractor input
  (measured), so the paper is ingested at all instead of being dropped as
  `failed_thin`.
- `empire` gains a correctly typed `introduction` and goes from 12,405 to
  **17,953** chars (measured).
- The other six papers are **byte-identical** — a pure recall fix with no
  collateral resegmentation.
- The enumerator rule lives in exactly **one** place, so the vocabulary
  additions planned in SEG-4 and SEG-5 inherit numbering support and cannot
  forget it.
- The partial-segmentation case stops being silent (AC-13), and the segmenter
  can be asked what it ignored (AC-14).

## Non-Goals

- **Letter-prefixed headings (`A.` / `B.` / `C.`).** The backlog proposed
  `(?:(?:\d+|[IVXLC]+|[A-Z])[\.\)]?\s*)?`. The `[A-Z]` half is measurably
  harmful and is **explicitly excluded** — see Decision 1.
- **Hierarchical numbering (`4.4.`, `II.B.`).** Same failure mode as letters;
  excluded for the same reason — see Decision 3.
- **SEG-2** (`segment_with_abstract` fix-or-delete). SEG-1 leaves that method's
  regex literal untouched; SEG-2 owns the decision.
- **SEG-3** (run-in abstracts). SEG-1 recovers no abstract for either IEEE
  paper — `ABSTRACT In the last few years...` and `Abstract—[Background.]` are
  run-in forms and stay unmatched. Both IEEE papers still have no abstract after
  SEG-1, by design. **SEG-3 is the next feature to be specified** (decided in
  review).
- **SEG-5** (headings named after the contribution). SEG-1 fixes only 2 of the 7
  SEG-5 misses. `III. SciCheck` and `IV. RESEARCH APPROACH` remain unmatched, so
  **neither IEEE paper has a methods section after SEG-1.**
- **Phantom single-word headings.** Discovered while measuring this fix; filed
  as SEG-11 rather than absorbed here. See Notes.

## User Stories

- As the ground-truth diff, I want IEEE papers to yield extractor input, so that
  a recall gap I report is an extractor failure and not a segmenter failure.
- As an operator running `ingest_papers`, I want a paper that segments only
  partially to say so in the log, so that a 66%-coverage paper is not
  indistinguishable from a healthy one.
- As the next person adding heading vocabulary (SEG-3, SEG-4, SEG-5), I want
  numbering handled outside the pattern table, so that my new pattern supports
  `IV.` and `4.` without my having to know that.
- As whoever debugs the next segmentation complaint, I want to see which
  heading-shaped lines were rejected, so that I do not have to write a script to
  find out.

## Design Approach

### Decision 1 — Roman and Arabic only; no letter prefixes

The backlog's proposed `[A-Z]` alternative was measured and rejected. IEEE uses
letters for **subsections**, so admitting them promotes children to top level.
In `fact_completion`, `B. RESULTS AND DISCUSSION` is a subsection of
`IV. EVALUATION`:

| Variant | `fact_completion` extractor input |
|---|---:|
| current | 0 |
| Roman + Arabic | **25,586** |
| Roman + Arabic + `[A-Z]` | 12,449 |

With `[A-Z]` admitted, `B. RESULTS AND DISCUSSION` becomes a top-level `results`
heading. That truncates `experiments` from 3,045w to 1,032w and diverts 2,009w
into a type the four-section keep-list discards — a **net loss of 13,137 chars**
against the Roman-only fix. Lettered subsections staying `UNKNOWN` is the
correct outcome: an unrecognized subsection heading remains inside its parent
span, which is where its content belongs.

### Decision 2 — Strip the enumerator before matching, not in 34 patterns

The prefix group `(?:\d+\.?\s*)?` is copy-pasted into 34 of the 40 pattern
literals. Rather than editing 34 sites, normalize once in `_classify_heading`
and **delete the prefix groups entirely**, leaving a single mechanism:

- One testable rule instead of 34 copies.
- SEG-3/SEG-4/SEG-5 vocabulary additions get numbering for free.
- `Section.title` keeps the **raw** heading (`'I. INTRODUCTION'`), because only
  the classification input is normalized. Character offsets are untouched.

**Deliberate side effect.** Six patterns currently carry *no* prefix group —
`abstract`, `summary`, both `acknowledgment(s)` spellings, the three
`references` variants, and the `appendix` pair. Under a single mechanism they
now accept numbering too, so `VII. ACKNOWLEDGMENT` and `1. References` begin to
match. This is intended and harmless: those keywords are unambiguous, and a
numbered acknowledgments section is real. It is called out here so it is not
read as an accident in review.

**Blast-radius mitigation (review TL #2).** Editing 34 literals across 15
section types — four of which (`background`, `discussion`, `acknowledgments`,
`appendix`) have no test coverage at all — risks exactly the silent breakage
this document is about. AC-5 therefore adds a 15-row table-integrity test as
part of this change, converting the untested types into tested ones.

### Decision 3 — Flat enumerators only

Stripping hierarchical numbers reproduces the Decision-1 failure mode: with
`(?:\.\d+)*` in the enumerator, `hypothesis_generation` loses **5,585 chars**
because `4.4. Results` is promoted out of `4. Evaluation`. `llm_ontology_gen`
loses 25 and `kg_construction_survey` 15 for the same reason. Flat only.

This also preserves one existing behaviour that looks accidental but is right:
`3.1 Methods` matches nothing today (the old group consumes `3` then `.`, then
requires the keyword and finds `1 Methods`), and it must keep matching nothing.

### Decision 4 — Verification without the PDFs

`ground-truth-papers/` is gitignored (`.gitignore:75`), so no PDF corpus test
can run in CI. Three artifacts:

1. **`scripts/measure_segmentation.py`** — committed sibling to
   `scripts/segment_ground_truth.py`. Prints the per-paper before/after table
   against a committed `scripts/seg_baseline.json` and exits non-zero if any
   paper regresses. Run locally by an operator who has the PDFs.
2. **A committed real excerpt fixture** — ~150 lines of actual PyMuPDF output
   from `fact_completion`, running headers and ligatures intact, so the
   document-level test exercises real noise rather than a clean skeleton.
3. **Unit tests** — AC-1 through AC-5, PDF-free, guarding the Decision-1 and
   Decision-3 boundaries in CI.

**Baseline ownership (review TL #3).** SEG-3, SEG-4, SEG-5 and SEG-7 will each
legitimately change the baseline numbers, and SEG-7 will change them a lot. A
guard that must be hand-edited by four downstream features is a guard that gets
commented out, so the baseline lives in JSON and the script prints the exact
re-baseline command on failure. Re-baselining is one command with a reviewable
diff, and each subsequent SEG spec inherits an AC to re-baseline *with
justification*.

## Sample Implementation

Validated against 42 heading assertions and all 8 PDFs before being written
here — 2 papers improved, 6 byte-identical, 0 regressed.

```python
# section_segmenter.py

# SEG-1: Roman numerals I..XXXVIII. The lookahead guarantees a non-empty match,
# so "C. Data Analysis" cannot match an empty numeral and then eat the ".".
# L/C/D/M are deliberately excluded — a paper with 40+ top-level sections is
# out of scope, and admitting them would widen the false-positive surface.
_ROMAN = r"(?=[IVX])X{0,3}(?:IX|IV|V?I{0,3})"

# A separator is REQUIRED after the numeral, which is what keeps single letters
# out: "B. RESULTS" offers no numeral, and "Vision" offers no separator.
# Flat only — no (?:\.\d+)* — so "4.4. Results" is left inside its parent span.
_ENUMERATOR = re.compile(rf"^(?:\d+|{_ROMAN})(?:[.)]\s*|\s+)", re.IGNORECASE)


class SectionSegmenter:
    SECTION_PATTERNS = {
        # SEG-1: patterns no longer carry a numeric prefix group. Numbering is
        # stripped in _classify_heading — add vocabulary here without it.
        SectionType.INTRODUCTION: [
            r"^introduction\s*$",
            r"^overview\s*$",
            # (the old r"^1\.?\s*introduction\s*$" duplicate is removed)
        ],
        SectionType.METHODS: [
            r"^method(?:s|ology)?\s*$",
            r"^approach\s*$",
            r"^(?:our\s+)?(?:proposed\s+)?(?:method|approach|framework|model)\s*$",
            ...
        ],
        ...
    }

    def _classify_heading(self, heading_text: str) -> SectionType:
        """Classify a heading, ignoring any leading section number.

        The raw text is preserved by the caller as Section.title; only the
        classification input is normalized. count=1 strips one enumerator, so
        "II.B. Results" becomes "B. Results" and stays UNKNOWN — a Roman-then-
        letter subsection is still a subsection.
        """
        cleaned = _ENUMERATOR.sub("", heading_text.strip(), count=1)

        for section_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.match(cleaned):
                    return section_type

        return SectionType.UNKNOWN

    def _find_headings(self, text):
        ...
            section_type = self._classify_heading(stripped)
            if section_type != SectionType.UNKNOWN:
                headings.append((current_pos, current_pos + len(line), stripped, section_type))
            else:
                # SEG-1 (review QA #3): the segmenter's rejections are the only
                # record of SEG-3/4/5-class misses. Cheap to keep, off by default.
                logger.debug("unmatched candidate heading: %r", stripped)
        ...
```

```python
# ingestion.py — AC-13. NOT a duplicate of the failed_thin path: that fires
# only below MIN_USABLE_CHARS. This one fires on papers that PASS the gate
# while missing wanted sections, which is the case that is still silent.
_missing = [w for w in _EXTRACTOR_WANTED_SECTIONS if w not in found_types]
if _missing:
    logger.warning(
        "[%s] %s: partial segmentation — missing %s "
        "(found %s, %d chars); entity recall will be limited",
        trace_id, doi, _missing, sorted(found_types), len(section_text),
    )
```

Observed behaviour:

```
recovered:   'I. INTRODUCTION'          -> introduction
             'IV. EVALUATION'           -> experiments
             'VIII. CONCLUSION'         -> conclusion
             'XI) Results'              -> results
             'iv. evaluation'           -> experiments
unchanged:   '1. Introduction'          -> introduction
             'Threats to Validity'      -> limitations
             'Appendix A'               -> appendix
still unknown (locked by Decisions 1 and 3):
             'B. RESULTS AND DISCUSSION'-> unknown
             '4.4. Results'             -> unknown
             'II.B. Results'            -> unknown
             '3.1 Methods'              -> unknown
             'Vision'                   -> unknown
             'Civil Approach'           -> unknown
```

## Edge Cases & Error Handling

### Lettered subsection under a Roman parent
- **Scenario**: `IV. EVALUATION` followed by `A. EVALUATION PROTOCOL` and
  `B. RESULTS AND DISCUSSION` (real, `fact_completion`).
- **Behavior**: parent classifies as `experiments`; both children stay
  `UNKNOWN` and their content remains inside the parent span.
- **Test**: AC-3 + AC-6 — assert no returned `Section.title` starts with `B.`,
  and that the `experiments` content contains text from beneath the `B.` line.

### Hierarchical Arabic numbering
- **Scenario**: `4. Evaluation` followed by `4.4. Results`
  (real, `hypothesis_generation`).
- **Behavior**: `4. Evaluation` → `experiments`; `4.4. Results` → `UNKNOWN`.
- **Test**: AC-4.

### Roman-looking English word
- **Scenario**: `Vision`, `Civil Approach`, or a body line beginning `I have`.
- **Behavior**: `Vision` — no separator after `V`, nothing stripped, `UNKNOWN`.
  `Civil Approach` — `C` is not a supported numeral value, nothing stripped,
  `UNKNOWN`. `I have ...` — `I ` strips, remainder matches no pattern,
  `UNKNOWN`.
- **Test**: AC-4 table.

### Numeral with no separator
- **Scenario**: `Vresults`, `Xmethods` — the shape the backlog's `[.\)]?\s*`
  (both optional) would have admitted.
- **Behavior**: `UNKNOWN`. The enumerator requires `[.)]` or whitespace.
- **Test**: AC-4 table.

### Running headers in real PDF text
- **Scenario**: PyMuPDF interleaves `A. Borrego et al.: Completing Scientific
  Facts in Knowledge Graphs of Research Concepts` (88 chars, under the 100-char
  `max_heading_length`) roughly every 40 lines of `fact_completion`.
- **Behavior**: `UNKNOWN` — it ends in no section keyword. It does not become a
  heading, and after AC-14 it appears in the DEBUG stream as noise (which
  SEG-11 addresses).
- **Test**: AC-6 — the committed excerpt keeps these lines in place.

### Lowercase Roman numerals
- **Scenario**: `iv. evaluation`.
- **Behavior**: matched (`experiments`). `_ENUMERATOR` carries `re.IGNORECASE`,
  consistent with the pattern table.
- **Test**: AC-1.

### Numeral beyond XXXVIII
- **Scenario**: a heading numbered `XL.` or `L.`.
- **Behavior**: not stripped, `UNKNOWN`. Deliberate; see Open Questions.
- **Test**: none — asserting this would lock in a limitation.

### Class-level pattern cache
- **Scenario**: `_compiled_patterns` is a **class** attribute guarded by
  `if not self._compiled_patterns`, so patterns compile once per process and
  never recompile.
- **Behavior**: unchanged by SEG-1, but any test that monkeypatches
  `SECTION_PATTERNS` must clear `_compiled_patterns` **and** call
  `reset_section_segmenter()`, or it silently tests the previous pattern set —
  a test-order-dependent pass.
- **Test**: new tests follow the existing `TestGetSectionSegmenter`
  setup/teardown pattern.

## Acceptance Criteria

### AC-1: Roman-numeral headings classify correctly
- **Given** `I. INTRODUCTION`, `II. BACKGROUND`, `II. RELATED WORK`,
  `IV. EVALUATION`, `V. RESULTS`, `VI. THREATS TO VALIDITY`, `VII. DISCUSSION`,
  `VIII. CONCLUSION`, `XI) Results`, `I Introduction`, `iv. evaluation`
- **When** each is passed to `_classify_heading`
- **Then** they return `introduction`, `background`, `related_work`,
  `experiments`, `results`, `limitations`, `discussion`, `conclusion`,
  `results`, `introduction`, `experiments` respectively.
- **Must be red pre-fix** (all `unknown`).

### AC-2: Arabic and unnumbered headings are unchanged
- **Given** every heading asserted in the existing
  `tests/extraction/test_section_segmenter.py`
- **When** the suite runs against the new implementation
- **Then** every assertion passes **with no edits to existing test bodies** —
  including `1. Introduction`, `1 Introduction`, `3. Approach`, `Our Method`,
  `Proposed Framework`, `Threats to Validity`, `Experimental Setup`,
  `Results and Analysis`, and both `unknown` cases.

### AC-3: Letter prefixes remain unrecognized (Decision 1 lock)
- **Given** `B. RESULTS AND DISCUSSION`, `A. EVALUATION PROTOCOL`,
  `C. Data Analysis`
- **When** each is classified
- **Then** all return `SectionType.UNKNOWN`.

### AC-4: Hierarchical numbering and near-misses remain unrecognized (Decision 3 lock)
- **Given** `4.4. Results`, `4.4. Experimental setup`, `3.1 Methods`,
  `II.B. Results`, `Vresults`, `Vision`, `Civil Approach`,
  `Introductory Remarks`
- **When** each is classified
- **Then** all return `SectionType.UNKNOWN`.

### AC-5: Pattern-table integrity across all section types
- **Given** the post-refactor `SECTION_PATTERNS`
- **When** a 14-row parametrized test classifies one representative heading per
  type (`Abstract`, `IV. Introduction`, `II. Related Work`, `II. Background`,
  `III. Methods`, `IV. Experiments`, `V. Results`, `VI. Discussion`,
  `VII. Limitations`, `Future Work`, `VIII. Conclusion`, `Acknowledgments`,
  `References`, `Appendix A`)
- **Then** each returns its expected `SectionType`, **and** a separate assertion
  confirms every `SectionType` except `UNKNOWN` has at least one pattern.
- **Rationale**: catches a keyword dropped during the 34-literal edit on a type
  that has no other coverage.

### AC-6: A real IEEE excerpt yields the recovered section types (document level)
- **Given** a committed fixture
  (`tests/extraction/fixtures/segmenter/ieee_roman_excerpt.txt`) of ~150 lines
  of **actual PyMuPDF output** from `fact_completion`, spanning
  `I. INTRODUCTION` through `VI. CONCLUSION`, retaining the running headers,
  ligatures and the `A.`/`B.` subsections, with body paragraphs elided
- **When** `segment()` runs on it
- **Then** the returned types include `introduction` and `experiments`; no
  returned `Section.title` begins with `A.` or `B.` or with `A. Borrego`; and
  the `experiments` section's content contains text drawn from beneath the
  `B. RESULTS AND DISCUSSION` line.
- **Note**: this is the assertion that catches the *silent* failure mode. A test
  that only checks "some sections were returned" passes on every paper in the
  set today.

### AC-7: `fact_completion` produces non-zero extractor input (corpus, local)
- **Given** the eight PDFs in `ground-truth-papers/`
- **When** `scripts/measure_segmentation.py` runs
- **Then** `fact_completion` reports **25,586** chars across the four kept
  types, up from 0, and is no longer counted as `failed_thin`.

### AC-8: `empire` gains a real introduction (corpus, local)
- **Given** the same run
- **Then** a section of type `introduction` titled `I. INTRODUCTION` is present
  and the paper reports **17,953** chars, up from 12,405.

### AC-9: The other six papers are byte-identical (corpus, local)
- **Given** the same run
- **When** `cskg`, `cskg2`, `kg_construction_survey`, `llm_ontology_gen`,
  `kg_validation_hitl` and `hypothesis_generation` are compared to the baseline
- **Then** each reports an unchanged char count **and** an unchanged
  `(section_type, title)` sequence.

### AC-10: The harness guards the baseline and makes re-baselining explicit
- **Given** `scripts/measure_segmentation.py` and `scripts/seg_baseline.json`
- **When** any paper's kept-char count falls below its baseline
- **Then** the script prints the offending paper with its delta and exits
  non-zero, and prints the `--update-baseline` command.
- **And** `--update-baseline` rewrites `seg_baseline.json` so the change lands
  as a reviewable diff rather than an edited literal.

### AC-11: One enumerator mechanism, not two
- **Given** the post-fix `SECTION_PATTERNS`
- **When** the table is inspected
- **Then** no pattern literal contains a numeric prefix group, and the redundant
  `^1\.?\s*introduction\s*$` duplicate is gone.

### AC-12: `segment_with_abstract` is untouched (SEG-2 boundary)
- **Given** `segment_with_abstract`
- **When** the diff is reviewed
- **Then** its regex literal at `section_segmenter.py:377` is unchanged, and
  `segment()`/`segment_with_abstract()` still return identical output on the
  corpus — the SEG-2 defect is preserved intact for SEG-2 to decide.

### AC-13: Partial segmentation is reported, not just thin segmentation
- **Given** a paper whose extractor text clears `MIN_USABLE_CHARS` but is
  missing one or more of `abstract` / `introduction` / `methods` / `experiments`
  (e.g. `cskg`, which has neither abstract nor methods)
- **When** `ingest_papers` processes it
- **Then** a single WARNING names the DOI, the missing types, the types found,
  and the char count.
- **And** the existing `failed_thin` path is **not** duplicated — this fires
  only on papers that pass the gate.
- **Test**: unit test on the built text + a `caplog` assertion; assert no
  warning fires for a paper with all four types.

### AC-14: Rejected heading candidates are observable
- **Given** `_find_headings` encounters a line that survives the length filter
  but classifies `UNKNOWN`
- **When** the logger is at DEBUG
- **Then** one `unmatched candidate heading: <repr>` line is emitted per
  occurrence, and nothing is emitted at INFO or above.
- **Test**: `caplog` at DEBUG over the AC-6 fixture shows
  `'III. SciCheck'` and `'B. RESULTS AND DISCUSSION'`; the same run at INFO
  shows neither.

### AC-15: SEG-11 is recorded
- **Given** the phantom-heading evidence gathered during this work
- **When** SEG-1 lands
- **Then** `llm/features/BACKLOG.md` carries a **SEG-11** row with the measured
  per-paper counts, and `docs/ground-truth/segmenter-findings.md` records it as
  root cause (8) — noting explicitly that it is tracked as SEG-11 rather than
  SEG-8, because SEG-8 is already the test-fixture item, so the
  "`SEG-n` ↔ cause *(n)*" convention stated at the top of the backlog section
  stops holding past (7) and must say so.
- **And** the same doc's "zero entities, silently and with no error" claim is
  corrected per the Problem section above (SM-1 made it `failed_thin`).

## Technical Notes

- **Affected components**:
  - `packages/core/src/agentic_kg/extraction/section_segmenter.py` — add
    `_ROMAN` / `_ENUMERATOR`, normalize in `_classify_heading`, DEBUG-log
    rejections in `_find_headings`, strip the prefix group from 34 pattern
    literals, drop 1 duplicate pattern.
  - `packages/core/src/agentic_kg/ingestion.py` — AC-13 warning at the
    `_acquire_full_text` success path / per-paper loop.
  - `packages/core/tests/extraction/test_section_segmenter.py` — AC-1, AC-3,
    AC-4, AC-5, AC-6, AC-11, AC-14.
  - `packages/core/tests/extraction/fixtures/segmenter/ieee_roman_excerpt.txt`
    (new) — AC-6.
  - `scripts/measure_segmentation.py`, `scripts/seg_baseline.json` (new) —
    AC-7..AC-10.
  - `llm/features/BACKLOG.md`, `docs/ground-truth/segmenter-findings.md` —
    AC-15.
- **Patterns to follow**: `scripts/segment_ground_truth.py` is the model for the
  harness — `sys.path` insert into `packages/core/src`, hard-fail rather than
  silent skip, per-paper table to stdout. It must import `section_segmenter`
  **by file path** (`importlib.util.spec_from_file_location`) or run under a
  venv with full deps: `agentic_kg.extraction.__init__` pulls `feedparser` in
  via `data_acquisition`, which `.venv-gt` does not have.
- **Data model changes**: none. No new `SectionType`, no `Section` field change,
  no offset semantics change. (An `unmatched_headings` field on
  `SegmentedDocument` was considered for AC-14 and rejected in favour of the
  DEBUG log, to keep the promise of no data-model change.)
- **Downstream**: `pipeline.py:475` calls `.segment()`; `ingestion.py:198`
  filters to four types. Neither changes. The behavioural effect is that
  `fact_completion` stops returning `""` and therefore stops being skipped.
- **Fixture regeneration**: **not** required. `paper_<slug>.txt` comes from
  hand-verified boundaries and SEG-1 does not change the keep-list, so no gold
  file or completed review is invalidated. (Contrast SEG-7, which does force
  regeneration.)

## Dependencies

- **Blocks**: SEG-2 — `segment_with_abstract`'s lookahead
  `(?=\n\s*(?:\d+\.?\s*)?(?:introduction|1\.|I\.))` needs Roman-numeral support
  to terminate on an IEEE paper; it currently carries a hardcoded `I\.`
  alternative that covers section I only by accident.
- **Blocks**: the importer-vs-gold diff, and through it D-2 / V-1.
- **Blocked by**: nothing. SEG-1 is independent and lands first.
- **Followed by**: **SEG-3** (run-in abstracts) is the next feature to be
  specified, per review. Together SEG-1 + SEG-3 give both IEEE papers an
  abstract; methods still awaits SEG-5.
- **Adjacent**: SEG-8 (segmenter test fixtures) should extend the
  `fixtures/segmenter/` directory created here rather than start a second one.
- **Inherited by SEG-3/4/5/7**: each must re-baseline
  `scripts/seg_baseline.json` with justification (see Decision 4).

## Open Questions

- **Numerals above XXXVIII.** `_ROMAN` stops at `XXXVIII` (no `L`/`C`/`D`/`M`).
  No paper in the corpus comes close. Revisit only if a real document needs it;
  the tradeoff is a wider false-positive surface.
- **Excerpt size and provenance for AC-6.** ~150 lines of a published paper's
  extracted text goes into the repo. Keep it to the heading neighbourhoods with
  bodies elided, and note the source in a fixture README, consistent with how
  `fixtures/ground_truth_chain/README.md` documents its inputs. Flag if the
  repo has a stricter policy on committing third-party text.
- **Hierarchical numbering as a subsection signal.** Decision 3 keeps `4.4.`
  unrecognized. If subsection detection is ever implemented (the
  `detect_subsections` constructor flag is accepted and never used), the
  hierarchical enumerator is its natural input — deferred, not rejected.
- **AC-11's structural assertion.** A test that greps the pattern table for
  `\d` will annoy whoever legitimately needs a digit in a pattern later.
  Implementation may downgrade it to a comment on `SECTION_PATTERNS` if it
  proves brittle.
- **AC-14 noise before SEG-11.** The DEBUG stream will be dominated by running
  headers and table cells until SEG-11 lands. Acceptable at DEBUG; revisit if it
  makes the stream unreadable in practice.

## Notes

### Discovered while measuring: phantom single-word headings (→ SEG-11)

`_find_headings` accepts any line under `max_heading_length` (100 chars) that
matches a pattern, with no requirement that the line *look* like a heading — no
blank-line context, no capitalization or position check. Because the METHODS
patterns include bare `model`, `method`, `approach` and `technique`, single-word
lines in tables and figure labels classify as top-level method sections:

| Paper | Phantom `methods` sections | Titles |
|---|---:|---|
| `llm_ontology_gen` | 5 | `MODEL` ×5 (a table column header) |
| `kg_validation_hitl` | 4 | `Model` ×3, `Method` ×1 |
| `kg_construction_survey` | 2 | `Model`, `Approach` |
| `empire` | 1 | `Method` |

This is a **new root cause**, not among the findings doc's seven, and it matters
for reading SEG-1's own numbers honestly: `empire`'s pre-fix 12,405 chars are
**not** a real methods section. Its only `methods` block is a phantom `Method`
line, and its actual method section (`IV. RESEARCH APPROACH`) is a SEG-5 miss.
So `empire`'s +5,548 is entirely introduction/background recovery, and the paper
still has no true methods section after SEG-1.

Out of scope here per the interview: the fix is a heading-context heuristic
(blank-line delimitation, caps/title-case check, or a position/frequency filter
to drop repeated running headers), which needs its own measurement. Filed as
SEG-11.

### Interaction with SM-1

SM-1's acquisition gate is why the Problem section corrects the findings doc.
Two consequences worth carrying into implementation:

- `fact_completion` is currently a **`failed_thin` skip**, so SEG-1 will change
  run metrics as well as entity counts: `pdf_ok` +1 and
  `acquisition_failures["failed_thin"]` −1 on any batch containing it.
- AC-13 exists because the gate is a *floor*, not a completeness check. A paper
  at 66% coverage with no abstract and no methods clears it and logs
  `INFO Full text acquired`.

### Measurement provenance

Every number here was produced by running the real `SectionSegmenter` against
the eight PDFs in `ground-truth-papers/` via `.venv-gt`, with candidate prefix
variants substituted into `SECTION_PATTERNS`. Four prefix variants were compared
(current; Roman-only loose; Roman + `[A-Z]`; canonical Roman with required
separator) plus flat-vs-hierarchical enumerator stripping, and the chosen
implementation was validated against 42 heading assertions before being written
into this spec. `scripts/measure_segmentation.py` (AC-7..AC-10) makes that
reproducible instead of ad-hoc.
