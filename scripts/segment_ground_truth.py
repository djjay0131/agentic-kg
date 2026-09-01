#!/usr/bin/env python3
"""Hand-segment the 8 ground-truth-chain papers into extractor-input text.

The repo's ``SectionSegmenter`` mis-segments every paper in this set — see
``docs/ground-truth/segmenter-findings.md``. Until that is fixed, this script
produces the ``paper_<slug>.txt`` fixtures by hand-verified section boundaries
so the ground-truth labels describe what the importer *intends* to read
(abstract + introduction + methods + experiments) rather than what the current
segmenter happens to return.

Boundaries were read off each PDF individually and are asserted at runtime: a
missing marker or an out-of-order span is a hard error, not a silent skip.

Usage:
    .venv-gt/Scripts/python.exe scripts/segment_ground_truth.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "core" / "src"))

from agentic_kg.extraction.pdf_extractor import PDFExtractor  # noqa: E402

PDF_DIR = REPO / "ground-truth-papers"
OUT_DIR = (
    REPO / "packages" / "core" / "tests" / "extraction"
    / "fixtures" / "ground_truth_chain"
)

# Marker kinds:
#   "line"  — heading occupying its own line (anchored with ^...$, MULTILINE)
#   "text"  — run-in heading or bare phrase, matched anywhere
#
# Each paper lists its top-level boundaries in document order. Consecutive
# markers delimit a span; ``wanted`` names the spans kept, mapped to the
# importer's section vocabulary (abstract / introduction / methods / experiments).

PAPERS: dict[str, dict] = {
    "cskg": {
        "pdf": "Large_Scale_KG_CS.pdf",
        "note": "Springer LNCS. Run-in 'Abstract.'; unnumbered headings.",
        "boundaries": [
            ("abstract", "text", r"Abstract\.\s"),
            ("introduction", "line", r"Introduction"),
            ("related_work", "line", r"Related Work"),
            ("methods", "line", r"The Computer Science Knowledge Graph"),
            ("statistics", "line", r"Statistics About CS-KG"),
            ("comparison", "line", r"Comparison Between CS-KG and AI-KG"),
            ("experiments", "line", r"Evaluation"),
            ("conclusion", "line", r"Conclusions"),
        ],
        "wanted": ["abstract", "introduction", "methods", "experiments"],
    },
    "cskg2": {
        "pdf": "CS_KG_2.0_2025.pdf",
        "note": (
            "Nature Scientific Data. No 'Abstract' label — the abstract is the "
            "lead paragraph. 'Background & Summary' is the introduction; "
            "'Technical Validation' is the evaluation."
        ),
        "boundaries": [
            ("abstract", "text", r"The rapid evolution of AI"),
            ("introduction", "line", r"Background & Summary"),
            ("methods", "line", r"Methods"),
            ("data_records", "line", r"Data Records"),
            ("experiments", "line", r"Technical Validation"),
            ("usage_notes", "line", r"Usage Notes"),
        ],
        "wanted": ["abstract", "introduction", "methods", "experiments"],
    },
    "kg_construction_survey": {
        "pdf": "Current_State_Challenges.pdf",
        "note": (
            "94-page SSRN preprint of a survey. No methods/experiments sections "
            "exist — abstract + introduction only, which is the honest answer "
            "for a survey rather than padding the span."
        ),
        "boundaries": [
            ("abstract", "line", r"Abstract"),
            ("introduction", "line", r"1\.\s+Introduction"),
            ("background", "line",
             r"2\.\s+KG background and requirements for KG construction"),
        ],
        "wanted": ["abstract", "introduction"],
    },
    "llm_ontology_gen": {
        "pdf": "LLMs_for_Scholarly_Ontology_Generation.pdf",
        "note": (
            "Elsevier IPM. '3. Background' is background, not methods — the "
            "approach lives in '4. Experiments', so no methods span is kept."
        ),
        "boundaries": [
            ("abstract", "line", r"A B S T R A C T"),
            ("introduction", "line", r"1\.\s+Introduction"),
            ("related_work", "line", r"2\.\s+Related work"),
            ("background", "line", r"3\.\s+Background"),
            ("experiments", "line", r"4\.\s+Experiments"),
            ("results", "line", r"5\.\s+Results and discussion"),
        ],
        "wanted": ["abstract", "introduction", "experiments"],
    },
    "fact_completion": {
        "pdf": (
            "Completing_Scientific_Facts_in_Knowledge_Graphs_of_"
            "Research_Concepts.pdf"
        ),
        "note": (
            "IEEE Access. Roman-numeral headings; run-in 'ABSTRACT'. "
            "'III. SciCheck' is the method section."
        ),
        "boundaries": [
            ("abstract", "text", r"ABSTRACT In the last few years"),
            ("introduction", "line", r"I\.\s+INTRODUCTION"),
            ("related_work", "line", r"II\.\s+RELATED WORK"),
            ("methods", "line", r"III\.\s+SciCheck"),
            ("experiments", "line", r"IV\.\s+EVALUATION"),
            ("use_case", "line", r"V\.\s+USE CASE: AI-KG"),
        ],
        "wanted": ["abstract", "introduction", "methods", "experiments"],
    },
    "kg_validation_hitl": {
        "pdf": "KG_Validation_HumanInTheLoop.pdf",
        "note": "Elsevier IPM. Section 4 is the approach, 5 the experiments.",
        "boundaries": [
            ("abstract", "line", r"A B S T R A C T"),
            ("introduction", "line", r"1\.\s+Introduction"),
            ("related_work", "line", r"2\.\s+Related work"),
            ("use_case", "line",
             r"3\.\s+Use case: Validating the computer science knowledge graph"),
            ("methods", "line",
             r"4\.\s+Integrating LLMs and HiL into the SCICERO validation"),
            ("experiments", "line", r"5\.\s+Experiment design and implementation"),
            ("results", "line", r"6\.\s+Results"),
        ],
        "wanted": ["abstract", "introduction", "methods", "experiments"],
    },
    "hypothesis_generation": {
        "pdf": "Research_Hypothesis_Generation.pdf",
        "note": "Elsevier KnoSys. '4. Evaluation' is the experiments section.",
        "boundaries": [
            ("abstract", "line", r"A B S T R A C T"),
            ("introduction", "line", r"1\.\s+Introduction"),
            ("related_work", "line", r"2\.\s+Related work"),
            ("methods", "line", r"3\.\s+Methodology"),
            ("experiments", "line", r"4\.\s+Evaluation"),
            ("limitations", "line", r"5\.\s+Limitations"),
        ],
        "wanted": ["abstract", "introduction", "methods", "experiments"],
    },
    "empire": {
        "pdf": "KG-EmpiRE.pdf",
        "note": (
            "IEEE ESEM. Roman numerals; run-in 'Abstract—'. "
            "'IV. RESEARCH APPROACH' is the method; 'V. RESULTS' is results, "
            "not an experiments section, so it is excluded."
        ),
        "boundaries": [
            ("abstract", "text", r"Abstract—"),
            ("introduction", "line", r"I\.\s+INTRODUCTION"),
            ("background", "line", r"II\.\s+BACKGROUND"),
            ("related_work", "line", r"III\.\s+RELATED WORK"),
            ("methods", "line", r"IV\.\s+RESEARCH APPROACH"),
            ("results", "line", r"V\.\s+RESULTS"),
        ],
        "wanted": ["abstract", "introduction", "methods"],
    },
}


def find_marker(text: str, pattern: str, kind: str, after: int) -> int:
    """Return the offset of ``pattern`` at or after ``after``.

    ``line`` markers must occupy a whole line; ``text`` markers may appear
    mid-line (run-in headings). Raises if not found — a silently skipped
    boundary is how the repo's segmenter produced garbage in the first place.
    """
    regex = (
        re.compile(rf"^[ \t]*{pattern}[ \t]*$", re.MULTILINE)
        if kind == "line"
        else re.compile(pattern)
    )
    match = regex.search(text, after)
    if match is None:
        raise LookupError(f"marker not found after offset {after}: {pattern!r}")
    return match.start()


def segment(text: str, spec: dict) -> tuple[dict[str, str], list[str]]:
    """Slice ``text`` into named spans using the paper's ordered boundaries."""
    offsets: list[tuple[str, int]] = []
    cursor = 0
    for name, kind, pattern in spec["boundaries"]:
        start = find_marker(text, pattern, kind, cursor)
        offsets.append((name, start))
        cursor = start + 1

    spans: dict[str, str] = {}
    report: list[str] = []
    for index, (name, start) in enumerate(offsets):
        end = offsets[index + 1][1] if index + 1 < len(offsets) else len(text)
        body = text[start:end].strip()
        spans[name] = body
        mark = "KEEP" if name in spec["wanted"] else "drop"
        report.append(f"{mark} {name:<14} {len(body.split()):>6}w")
    return spans, report


def main() -> int:
    extractor = PDFExtractor()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    for slug, spec in PAPERS.items():
        pdf = PDF_DIR / spec["pdf"]
        print("=" * 78)
        print(f"{slug}  <-  {spec['pdf']}")
        try:
            full = extractor.extract_from_file(pdf).full_text
            spans, report = segment(full, spec)
        except (LookupError, OSError) as exc:
            print(f"  FAILED: {exc}")
            failures += 1
            continue

        missing = [n for n in spec["wanted"] if not spans.get(n)]
        if missing:
            print(f"  FAILED: wanted spans came back empty: {missing}")
            failures += 1
            continue

        out_text = "\n\n".join(spans[n] for n in spec["wanted"])
        (OUT_DIR / f"paper_{slug}.txt").write_text(out_text, encoding="utf-8")
        for line in report:
            print("   ", line)
        print(f"    -> paper_{slug}.txt  ({len(out_text)} chars, "
              f"{len(out_text.split())} words)")

    print("=" * 78)
    print(f"{len(PAPERS) - failures}/{len(PAPERS)} papers written to {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
