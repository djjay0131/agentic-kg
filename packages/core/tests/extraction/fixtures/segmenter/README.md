# Segmenter fixtures

Real extracted-text excerpts for `SectionSegmenter` tests. Created for
**SEG-1** (`llm/features/seg1-roman-numeral-headings.md`); **SEG-8** should
extend this directory rather than start a second one.

## Why real text and not a hand-written skeleton

A clean skeleton of heading lines over synthetic paragraphs passes while the
real paper fails. PyMuPDF's output carries noise a skeleton does not:

- **Running headers mid-body** — `A. Borrego et al.: Completing Scientific
  Facts in Knowledge Graphs of Research Concepts` is 88 characters, under the
  segmenter's `max_heading_length` of 100, so it reaches `_classify_heading`
  on every page. `VOLUME 10, 2022` does too.
- **Ligatures** — `Artiﬁcial`, `scientiﬁc`, `conﬁdence` arrive as single
  codepoints (U+FB01), and words break across lines as `classi-` / `ﬁer`.
- **Real subsection lettering** — `A. EVALUATION PROTOCOL` and
  `B. RESULTS AND DISCUSSION` sit inside `IV. EVALUATION`, which is the case
  SEG-1's Decision 1 turns on.

## `ieee_roman_excerpt.txt`

| | |
|---|---|
| Source | Borrego et al., *Completing Scientific Facts in Knowledge Graphs of Research Concepts*, IEEE Access vol. 10 (2022) |
| License | CC BY 4.0 (stated in the excerpt itself, line 24) |
| Slug in the ground-truth chain | `fact_completion` |
| PDF | `ground-truth-papers/Completing_Scientific_Facts_in_Knowledge_Graphs_of_Research_Concepts.pdf` (**gitignored** — `.gitignore:75`) |
| Extracted by | `agentic_kg.extraction.pdf_extractor.PDFExtractor` (PyMuPDF) |
| Size | 125 lines / ~6.7 KB, from 1,155 lines of source |

Line windows were taken around each top-level heading and the rest elided
with explicit `[... N lines elided ...]` markers, so the file is a set of
heading neighbourhoods rather than a redistribution of the paper. The elision
markers are inert — they match no section pattern.

Headings retained, and what the segmenter makes of them **after SEG-1**:

| Line | Heading | Classified | Note |
|---|---|---|---|
| 6 | `I. INTRODUCTION` | `introduction` | recovered by SEG-1 |
| 33 | `II. RELATED WORK` | `related_work` | recovered by SEG-1 |
| 59 | `III. SciCheck` | *unmatched* | **expected** — named after the contribution, SEG-5 |
| 76 | `IV. EVALUATION` | `experiments` | recovered by SEG-1 |
| 82 | `A. EVALUATION PROTOCOL` | *unmatched* | **expected** — lettered subsection, SEG-1 Decision 1 |
| 97 | `B. RESULTS AND DISCUSSION` | *unmatched* | **expected** — same; stays inside `IV. EVALUATION` |
| 115 | `VI. CONCLUSION` | `conclusion` | recovered by SEG-1 |

Note the abstract is absent: this paper's is the run-in form
`ABSTRACT In the last few years...`, which SEG-3 covers, not SEG-1.

### Regenerating

Requires the gitignored PDF. Extract with `PDFExtractor`, split on newlines,
and keep the windows `(28,45) (57,66) (127,140) (161,168) (220,234)
(462,480) (582,597) (809,820)`, joining them with the elision marker. Prefer
*not* to regenerate — the byte content is the point of the fixture, and the
line indices are only valid for this PyMuPDF version's output.
