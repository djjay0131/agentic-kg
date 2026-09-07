#!/usr/bin/env python3
"""Measure ``SectionSegmenter`` against the 8-paper ground-truth chain.

The papers live in ``ground-truth-papers/``, which is **gitignored**
(`.gitignore:75`), so this cannot run in CI. It is the local regression guard
for the segmenter work: it reports, per paper, how many characters reach the
entity extractors and which sections were found, and it **exits non-zero if
any paper regresses** against ``scripts/seg_baseline.json``.

SEG-3, SEG-4, SEG-5 and SEG-7 will each legitimately move these numbers, so
re-baselining is a deliberate one-command act with a reviewable diff rather
than a hand-edited literal:

    .venv/Scripts/python.exe scripts/measure_segmentation.py
    .venv/Scripts/python.exe scripts/measure_segmentation.py --update-baseline

See: ``llm/features/seg1-roman-numeral-headings.md`` (AC-7 .. AC-10).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "scripts" / "seg_baseline.json"
PDF_DIR = REPO / "ground-truth-papers"

# Slug -> PDF filename. Mirrors scripts/segment_ground_truth.py, which is the
# hand-verified-boundary sibling of this script.
PAPERS: dict[str, str] = {
    "cskg": "Large_Scale_KG_CS.pdf",
    "cskg2": "CS_KG_2.0_2025.pdf",
    "kg_construction_survey": "Current_State_Challenges.pdf",
    "llm_ontology_gen": "LLMs_for_Scholarly_Ontology_Generation.pdf",
    "fact_completion": (
        "Completing_Scientific_Facts_in_Knowledge_Graphs_of_"
        "Research_Concepts.pdf"
    ),
    "kg_validation_hitl": "KG_Validation_HumanInTheLoop.pdf",
    "hypothesis_generation": "Research_Hypothesis_Generation.pdf",
    "empire": "KG-EmpiRE.pdf",
}

# The four types `_build_extractor_section_text` (ingestion.py:198) keeps.
# Duplicated rather than imported so this script does not depend on the
# ingestion module's import chain (which pulls feedparser, openai, langgraph).
WANTED_SECTIONS = ("abstract", "introduction", "methods", "experiments")


def _load_baseline(
    path: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read the baseline JSON. Returns ``(baseline, error)`` where exactly
    one is populated. Never raises.

    ``path`` is resolved at call time, not bound as a default — a default arg
    would freeze the module-level constant at import and silently ignore any
    later re-pointing.
    """
    path = BASELINE_PATH if path is None else path
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except OSError as e:
        return None, f"cannot open baseline {str(path)!r}: {e}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {str(path)!r}: {e}"

    # The baseline is hand-editable (that is the point of storing it as a
    # file), so validate its shape here rather than letting `compare` raise
    # an AttributeError at the operator.
    if not isinstance(loaded, dict):
        return None, (
            f"baseline {str(path)!r} must be an object keyed by paper slug, "
            f"got {type(loaded).__name__}"
        )
    bad = [k for k, v in loaded.items() if not isinstance(v, dict)]
    if bad:
        return None, (
            f"baseline {str(path)!r} has non-object entries for: "
            f"{sorted(bad)}"
        )
    return loaded, None


def measure_document(seg: Any) -> dict[str, Any]:
    """Reduce one ``SegmentedDocument`` to its comparable shape.

    ``chars`` is what reaches the extractors; ``sections`` is the ordered
    ``(type, title)`` sequence, which catches a resegmentation that happens
    to preserve the character total.
    """
    sections = [
        [s.section_type.value, s.title] for s in seg.sections
    ]
    chars = sum(
        len(s.content)
        for s in seg.sections
        if s.section_type.value in WANTED_SECTIONS
    )
    return {"chars": chars, "sections": sections}


def measure_corpus(
    segment: Callable[[str], Any],
    extract_text: Callable[[Path], str],
    pdf_dir: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Measure every paper. Returns ``(measurements, errors)``.

    ``segment`` and ``extract_text`` are injected so the comparison logic can
    be unit-tested without PyMuPDF or the gitignored PDFs.
    """
    pdf_dir = PDF_DIR if pdf_dir is None else pdf_dir
    measured: dict[str, Any] = {}
    errors: list[str] = []
    for slug, filename in PAPERS.items():
        pdf = pdf_dir / filename
        try:
            measured[slug] = measure_document(segment(extract_text(pdf)))
        except (OSError, RuntimeError, ValueError) as e:
            errors.append(f"{slug}: {e}")
    return measured, errors


def compare(
    baseline: dict[str, Any],
    measured: dict[str, Any],
) -> list[dict[str, Any]]:
    """Diff measured against baseline, one row per paper in ``PAPERS`` order.

    Verdicts: ``IMPROVED`` (more chars), ``REGRESSED`` (fewer chars, or the
    same chars with a different section sequence), ``RESHAPED`` (more chars
    but a changed sequence — worth a human look, not a failure), ``SAME``,
    ``NEW`` (absent from baseline), ``MISSING`` (not measured).
    """
    rows: list[dict[str, Any]] = []
    for slug in PAPERS:
        base = baseline.get(slug)
        now = measured.get(slug)
        if now is None:
            rows.append({"slug": slug, "verdict": "MISSING",
                         "before": None, "after": None, "delta": None})
            continue
        if base is None:
            rows.append({"slug": slug, "verdict": "NEW",
                         "before": None, "after": now["chars"], "delta": None})
            continue

        before, after = base.get("chars", 0), now["chars"]
        same_shape = base.get("sections") == now["sections"]
        if after < before:
            verdict = "REGRESSED"
        elif after > before:
            verdict = "IMPROVED" if same_shape else "RESHAPED"
        else:
            verdict = "SAME" if same_shape else "REGRESSED"
        rows.append({"slug": slug, "verdict": verdict, "before": before,
                     "after": after, "delta": after - before})
    return rows


def failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that should fail the run. ``RESHAPED`` is reported but allowed;
    ``NEW`` is allowed so a paper can be added to the set."""
    return [r for r in rows if r["verdict"] in ("REGRESSED", "MISSING")]


def format_report(rows: list[dict[str, Any]]) -> str:
    """Render the per-paper table. Never truncates: a silently dropped paper
    is how this class of defect stayed hidden."""
    lines = [
        "",
        "=== SectionSegmenter: extractor-input chars per paper ===",
        f"  {'paper':<24}{'before':>10}{'after':>10}{'delta':>10}  verdict",
    ]
    for r in rows:
        # ASCII only: this prints to a Windows console under cp1252.
        before = "-" if r["before"] is None else f"{r['before']:,}"
        after = "-" if r["after"] is None else f"{r['after']:,}"
        delta = "-" if r["delta"] is None else f"{r['delta']:+,}"
        lines.append(
            f"  {r['slug']:<24}{before:>10}{after:>10}{delta:>10}"
            f"  {r['verdict']}"
        )

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
    lines.append("")
    lines.append(f"  {summary}")
    return "\n".join(lines)


def _write_baseline(
    measured: dict[str, Any],
    path: Path | None = None,
) -> None:
    """Persist a new baseline. Sorted keys + trailing newline so the diff is
    readable in review. ``path`` resolved at call time (see
    ``_load_baseline``)."""
    path = BASELINE_PATH if path is None else path
    with open(path, "w", encoding="utf-8") as f:
        json.dump(measured, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def _real_segment_and_extract() -> tuple[
    Callable[[str], Any], Callable[[Path], str]
]:  # pragma: no cover - requires PyMuPDF + the gitignored PDFs
    """Build the real callables.

    Imported by file path, not package path: ``agentic_kg.extraction`` pulls
    ``feedparser`` / ``openai`` / ``langgraph`` in via ``data_acquisition``,
    which a minimal PDF-only venv does not have.
    """
    import importlib.util

    src = REPO / "packages" / "core" / "src" / "agentic_kg" / "extraction"

    def _load(name: str, filename: str) -> Any:
        spec = importlib.util.spec_from_file_location(name, src / filename)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    pdf_extractor = _load("_seg_pdf_extractor", "pdf_extractor.py")
    section_segmenter = _load("_seg_section_segmenter", "section_segmenter.py")

    extractor = pdf_extractor.PDFExtractor()
    segmenter = section_segmenter.SectionSegmenter()
    return (
        segmenter.segment,
        lambda pdf: extractor.extract_from_file(pdf).full_text,
    )


def main(argv: list[str] | None = None) -> int:
    """Measure, compare, report. Returns the intended process exit code."""
    parser = argparse.ArgumentParser(
        description="Measure SectionSegmenter over the ground-truth chain.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="overwrite scripts/seg_baseline.json with the current numbers",
    )
    args = parser.parse_args(argv)

    if not PDF_DIR.is_dir():
        print(
            f"FAIL: {PDF_DIR} not found. The ground-truth PDFs are "
            "gitignored; see docs/ground-truth/README.md for download "
            "pointers.",
            file=sys.stderr,
        )
        return 2

    segment, extract_text = _real_segment_and_extract()
    measured, errors = measure_corpus(segment, extract_text)
    for err in errors:
        print(f"  ERROR {err}", file=sys.stderr)

    if args.update_baseline:
        _write_baseline(measured)
        print(f"Baseline rewritten: {BASELINE_PATH}")
        print(format_report(compare(measured, measured)))
        return 1 if errors else 0

    baseline, load_error = _load_baseline()
    if load_error is not None:
        print(f"FAIL: {load_error}", file=sys.stderr)
        print("  (first run? use --update-baseline)", file=sys.stderr)
        return 2
    assert baseline is not None  # narrows for type-checkers

    rows = compare(baseline, measured)
    print(format_report(rows))

    failed = failures(rows)
    if failed or errors:
        print("\nFAILED:")
        for r in failed:
            print(
                f"  {r['slug']}: {r['verdict']}"
                + (f" ({r['delta']:+,} chars)" if r["delta"] is not None else "")
            )
        print(
            "\nIf this change is intended, re-baseline with:\n"
            "  python scripts/measure_segmentation.py --update-baseline\n"
            "and justify the diff to scripts/seg_baseline.json in the PR."
        )
        return 1

    print("\nOK: no paper regressed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    sys.exit(main())
