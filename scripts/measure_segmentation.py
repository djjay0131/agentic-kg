#!/usr/bin/env python3
"""Measure ``SectionSegmenter`` against the 8-paper ground-truth chain.

Two corpora, one verdict engine:

**PDF corpus** (default, ``--corpus pdf``). The papers live in
``ground-truth-papers/``, which is **gitignored** (`.gitignore:75`), so this
cannot run in CI. It is the local regression guard for the segmenter work: it
reports, per paper, how many characters reach the entity extractors and which
sections were found, and it **exits non-zero if any paper regresses** against
``scripts/seg_baseline.json``.

**Committed corpus** (``--corpus committed``). Runs the same segmenter over the
committed ``paper_<slug>.txt`` fixtures in
``packages/core/tests/extraction/fixtures/ground_truth_chain/``. Those are the
*hand-verified gold* extractor input produced by
``scripts/segment_ground_truth.py`` — not raw PDF text — so this corpus answers
a narrower but sharper question than the PDF one:

    given text whose section boundaries are known to be correct, does the
    segmenter route it to the extractors the way gold says it should?

Any shortfall is a segmenter recall failure by construction, because every
character in a ``paper_<slug>.txt`` is already gold-wanted. This corpus needs no
PDFs and no PyMuPDF, so it runs in CI, and it is pinned by committed per-paper
fixtures under ``fixtures/segmenter_frozen/`` (``--freeze`` regenerates them).
The fixtures pin the segmenter's **output**, not a commit sha: PyMuPDF and the
PDF bytes drift independently of this repo, so a commit pin would be both too
strict and too weak.

SEG-3, SEG-4, SEG-5 and SEG-7 will each legitimately move these numbers, so
re-baselining is a deliberate one-command act with a reviewable diff rather
than a hand-edited literal:

    python scripts/measure_segmentation.py                       # PDF corpus
    python scripts/measure_segmentation.py --update-baseline
    python scripts/measure_segmentation.py --corpus committed     # CI corpus
    python scripts/measure_segmentation.py --corpus committed --entities
    python scripts/measure_segmentation.py --freeze               # re-pin

See: ``llm/features/seg1-roman-numeral-headings.md`` (AC-7 .. AC-10) and
``docs/ground-truth/corpus-readiness.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "scripts" / "seg_baseline.json"
PDF_DIR = REPO / "ground-truth-papers"

_FIXTURES = REPO / "packages" / "core" / "tests" / "extraction" / "fixtures"
CORPUS_DIR = _FIXTURES / "ground_truth_chain"
FROZEN_DIR = _FIXTURES / "segmenter_frozen"
SEGMENTER_PATH = (
    REPO / "packages" / "core" / "src" / "agentic_kg" / "extraction"
    / "section_segmenter.py"
)
INGESTION_PATH = REPO / "packages" / "core" / "src" / "agentic_kg" / "ingestion.py"

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


# =============================================================================
# Committed corpus: the CI-runnable arm
# =============================================================================
#
# ``paper_<slug>.txt`` is the hand-verified gold extractor input (see
# ``scripts/segment_ground_truth.py``), NOT raw PDF text. Running the segmenter
# over it is therefore an idempotence check: every character in the file is
# already gold-wanted, so anything the segmenter fails to route into a
# keep-list section is a segmenter recall failure with no other explanation.


def sha256_text(text: str) -> str:
    """Hex sha256 of ``text`` encoded UTF-8. One definition, used for the
    source fixture, the extractor input and the keep-list."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    """Hex sha256 of a file's bytes. Used for provenance only."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keeplist_sha() -> str:
    """Hex sha256 of the keep-list, as a canonical JSON array.

    Frozen alongside the output because a keep-list change silently rewrites
    what ``extractor_input`` means -- SEG-7 is exactly that change -- and a
    reviewer staring at a chars delta deserves to be told which of the two
    moved.
    """
    return sha256_text(json.dumps(list(WANTED_SECTIONS)))


def load_segmenter() -> Any:
    """Return a ``SectionSegmenter`` instance loaded by file path.

    By path, not by package import: ``agentic_kg.extraction`` pulls
    ``feedparser`` / ``openai`` / ``langgraph`` in via ``data_acquisition``.
    The drift test uses this same loader, so the bytes the test checks are
    produced by the bytes ``--freeze`` wrote.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_seg_section_segmenter", SEGMENTER_PATH,
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.SectionSegmenter()


def committed_text_path(slug: str) -> Path:
    """Where the gold extractor-input fixture for ``slug`` lives."""
    return CORPUS_DIR / f"paper_{slug}.txt"


def frozen_path(slug: str) -> Path:
    """Where the frozen segmenter output for ``slug`` lives."""
    return FROZEN_DIR / f"{slug}.json"


def build_extractor_input(seg: Any) -> str:
    """The exact string ``ingestion._build_extractor_section_text`` would hand
    the extractors for this document.

    Mirrored rather than imported, for the same dependency reason as
    ``WANTED_SECTIONS``; ``test_measure_segmentation.py`` asserts the mirror
    still matches the original's source.
    """
    if seg is None or not getattr(seg, "sections", None):
        return ""
    parts: list[str] = []
    for section in seg.sections:
        value = getattr(getattr(section, "section_type", None), "value", "") or ""
        if value.lower() in WANTED_SECTIONS:
            content = (getattr(section, "content", "") or "").strip()
            if content:
                parts.append(content)
    return "\n\n".join(parts)


def freeze_entry(slug: str, text: str, seg: Any) -> dict[str, Any]:
    """Reduce one segmented committed paper to its frozen, diffable shape.

    ``extractor_input`` is stored in full and in the clear. A hash alone would
    tell a reviewer that something moved and nothing about what; this is the
    artifact both arms of a legacy-vs-new comparison have to agree on, so it is
    the artifact that gets reviewed.
    """
    extractor_input = build_extractor_input(seg)
    return {
        "slug": slug,
        "source_txt": committed_text_path(slug).name,
        "source_sha256": sha256_text(text),
        "source_chars": len(text),
        # Provenance, deliberately NOT asserted on: a refactor that leaves the
        # output byte-identical is not drift. Reported on failure so the
        # reviewer knows what moved underneath.
        "segmenter_sha256": sha256_path(SEGMENTER_PATH),
        "keeplist": list(WANTED_SECTIONS),
        "keeplist_sha256": keeplist_sha(),
        "sections": [
            {
                "type": s.section_type.value,
                "title": s.title,
                "chars": len(s.content),
            }
            for s in seg.sections
        ],
        "extractor_input_chars": len(extractor_input),
        "extractor_input_sha256": sha256_text(extractor_input),
        "extractor_input": extractor_input,
    }


def measure_committed_corpus(
    segmenter: Any | None = None,
    corpus_dir: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Segment every committed ``paper_<slug>.txt``.

    Returns ``(entries, errors)``.
    """
    segmenter = load_segmenter() if segmenter is None else segmenter
    corpus_dir = CORPUS_DIR if corpus_dir is None else corpus_dir
    entries: dict[str, Any] = {}
    errors: list[str] = []
    for slug in PAPERS:
        path = corpus_dir / f"paper_{slug}.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            errors.append(f"{slug}: cannot read {path.name}: {e}")
            continue
        entries[slug] = freeze_entry(slug, text, segmenter.segment(text))
    return entries, errors


# Fields whose change IS drift. ``segmenter_sha256`` is absent on purpose --
# the contract is to pin the output, not the commit.
_FROZEN_ASSERTED = (
    "source_sha256",
    "source_chars",
    "sections",
    "extractor_input_chars",
    "extractor_input_sha256",
    "extractor_input",
)


def _section_drift(before: list[dict], after: list[dict]) -> list[str]:
    """Per-section diff, so a failure names the section rather than dumping
    two lists at the reader."""
    lines: list[str] = []
    for i in range(max(len(before), len(after))):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) else None
        if b == a:
            continue
        if b is None:
            lines.append(f"      section[{i}] ADDED   {a}")
        elif a is None:
            lines.append(f"      section[{i}] REMOVED {b}")
        else:
            lines.append(f"      section[{i}] CHANGED {b} -> {a}")
    return lines


def frozen_drift(frozen: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-readable drift report for one paper. Empty list == no drift."""
    slug = current.get("slug", "?")
    lines: list[str] = []
    for field_name in _FROZEN_ASSERTED:
        before, after = frozen.get(field_name), current.get(field_name)
        if before == after:
            continue
        if field_name == "sections":
            lines.append(
                f"    sections: {len(before or [])} -> {len(after or [])}"
            )
            lines.extend(_section_drift(before or [], after or []))
        elif field_name == "extractor_input":
            # The text itself is pinned by its sha, already reported above;
            # printing 30 KB of prose into a CI log helps nobody.
            lines.append("    extractor_input: text differs (see sha/chars)")
        else:
            lines.append(f"    {field_name}: {before!r} -> {after!r}")
    if not lines:
        return []
    header = [f"  {slug}: DRIFT"]
    if frozen.get("segmenter_sha256") != current.get("segmenter_sha256"):
        header.append(
            "    (section_segmenter.py changed; if this drift is the "
            "intended effect, re-freeze)"
        )
    if frozen.get("keeplist") != current.get("keeplist"):
        header.append(
            f"    (keep-list changed: {frozen.get('keeplist')} -> "
            f"{current.get('keeplist')})"
        )
    return header + lines


def load_frozen(
    slug: str,
    frozen_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Read one frozen fixture, or ``None`` if it is absent/unreadable."""
    frozen_dir = FROZEN_DIR if frozen_dir is None else frozen_dir
    path = frozen_dir / f"{slug}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_frozen(
    entries: dict[str, Any],
    frozen_dir: Path | None = None,
) -> list[Path]:
    """Persist frozen fixtures, one JSON per paper. Returns the paths written."""
    frozen_dir = FROZEN_DIR if frozen_dir is None else frozen_dir
    frozen_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for slug, entry in entries.items():
        path = frozen_dir / f"{slug}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)
            f.write("\n")
        written.append(path)
    return written


def format_committed_report(entries: dict[str, Any]) -> str:
    """Per-paper gold-recall table for the committed corpus.

    ``recall`` is extractor-input chars over gold chars. It is an upper bound
    on quality, not a grade: a section swallowed by a mislabelled neighbour
    still counts its characters. Read it with the ``secs`` column.
    """
    lines = [
        "",
        "=== Committed corpus (gold text -> extractor input) ===",
        f"  {'paper':<24}{'gold':>9}{'kept':>9}{'recall':>9}"
        f"{'secs':>6}  abstract",
    ]
    for slug in PAPERS:
        e = entries.get(slug)
        if e is None:
            lines.append(f"  {slug:<24}{'-':>9}{'-':>9}{'-':>9}{'-':>6}  -")
            continue
        gold = e["source_chars"] or 1
        kept = e["extractor_input_chars"]
        has_abstract = any(s["type"] == "abstract" for s in e["sections"])
        lines.append(
            f"  {slug:<24}{gold:>9,}{kept:>9,}{kept / gold:>9.1%}"
            f"{len(e['sections']):>6}  {'yes' if has_abstract else 'NO'}"
        )
    n_abs = sum(
        1
        for e in entries.values()
        if any(s["type"] == "abstract" for s in e["sections"])
    )
    lines.append("")
    lines.append(f"  abstracts found: {n_abs}/{len(entries)}")
    return "\n".join(lines)


# =============================================================================
# Recall-comparison readiness
# =============================================================================
#
# "Is this paper valid for a legacy-vs-new recall comparison?" needs an answer
# that is computed, not asserted. A recall number is only interpretable when
# the extractor input the number was computed over is the input gold describes.
# Three per-paper conditions, all measurable on the committed corpus:

# 1. An abstract is present. Gold gives all eight papers one, and it is the
#    densest gold-entity source in the paper. Missing it guarantees a recall
#    shortfall that is a segmentation failure wearing an extractor's clothes.
# 2. At least GOLD_RECALL_FLOOR of the gold text reaches the extractors. Every
#    character in a paper_<slug>.txt is gold-wanted, so a shortfall is content
#    the segmenter dropped, with no other explanation available. The floor is
#    below 100% because _extract_sections consumes the heading line itself --
#    a few hundred characters per paper that are structurally unrecoverable.
# 3. No single section holds SWALLOW_SHARE or more of the kept text while
#    more than one gold section is expected. That is SEG-4's under-segmentation
#    signature: one mislabelled span absorbing the rest of the paper keeps its
#    characters, so conditions 1 and 2 cannot see it.
GOLD_RECALL_FLOOR = 0.95
SWALLOW_SHARE = 0.90

# WHAT THOSE THREE CONDITIONS CANNOT SEE, stated here because the verdict they
# produce is the number this work gets cited for.
#
# All four keep-list types are concatenated into ONE blob before the extractors
# see it, so a span labelled `introduction` that is really the methods section
# keeps every one of its characters and lands in the same prompt. Char recall
# is therefore structurally blind to confusion BETWEEN keep-list types:
# intra-keep-list mislabelling is free.
#
# Measured, and not hypothetical: `cskg`'s 14,988-char gold methods span
# [6728,21716) is absorbed into a 20,271-char `introduction`, and the paper
# still scores 99.9% recall. Five of the seven papers that pass the three
# conditions are missing a wanted section TYPE.
#
# So the verdict is reported in two columns, and neither stands in for the
# other:
#
#   CONCAT   valid for a legacy-vs-new comparison over the concatenated
#            extractor blob -- which is what today's pipeline actually feeds
#            the extractors. This is the claim this work supports.
#   TYPED    also produces every wanted section type gold records for the
#            paper, under the right label. This is the claim SEG-7 needs,
#            because inverting the keep-list is exactly the change that stops
#            type errors being free.
#
# A paper can be CONCAT-valid and TYPED-invalid. Most of them are.

# Mirrored from scripts/segment_ground_truth.py's per-paper `wanted` lists.
# Read from gold rather than assumed to be all four: kg_construction_survey is
# a 94-page survey with no methods and no experiments sections, and gold says
# so -- holding it to four types would manufacture a failure out of an honest
# answer. test_gold_wanted_types_mirror_matches_the_generator keeps the two in
# step.
_GOLD_WANTED_TYPES: dict[str, tuple[str, ...]] = {
    "cskg": ("abstract", "introduction", "methods", "experiments"),
    "cskg2": ("abstract", "introduction", "methods", "experiments"),
    "kg_construction_survey": ("abstract", "introduction"),
    "llm_ontology_gen": ("abstract", "introduction", "experiments"),
    "fact_completion": ("abstract", "introduction", "methods", "experiments"),
    "kg_validation_hitl": ("abstract", "introduction", "methods", "experiments"),
    "hypothesis_generation": (
        "abstract", "introduction", "methods", "experiments",
    ),
    "empire": ("abstract", "introduction", "methods"),
}


def expected_wanted_types(slug: str) -> tuple[str, ...]:
    """The keep-list types gold actually records for ``slug``."""
    return _GOLD_WANTED_TYPES.get(slug, tuple(WANTED_SECTIONS))


def recall_validity(entry: dict[str, Any]) -> dict[str, Any]:
    """Per-paper verdict for one frozen/measured entry.

    Returns BOTH verdicts -- see the comment above ``_GOLD_WANTED_TYPES``.
    ``valid`` is the concatenated-blob verdict; ``section_typed`` is the
    stricter one, and ``missing_types`` names what is absent.
    """
    sections = entry["sections"]
    kept = [s for s in sections if s["type"] in WANTED_SECTIONS]
    gold_chars = entry["source_chars"] or 1
    kept_chars = entry["extractor_input_chars"]
    recall = kept_chars / gold_chars
    largest = max((s["chars"] for s in kept), default=0)
    share = largest / kept_chars if kept_chars else 1.0

    reasons: list[str] = []
    if not any(s["type"] == "abstract" for s in sections):
        reasons.append("no abstract section")
    if recall < GOLD_RECALL_FLOOR:
        reasons.append(
            f"gold recall {recall:.1%} < {GOLD_RECALL_FLOOR:.0%} "
            f"({gold_chars - kept_chars:,} chars dropped)"
        )
    if len(kept) > 1 and share >= SWALLOW_SHARE:
        reasons.append(f"one section holds {share:.1%} of the kept text")
    elif len(kept) == 1 and recall < 1.0:
        reasons.append("a single section holds all kept text (under-segmented)")

    # The second verdict. Mirrors ingestion._missing_wanted_sections, which
    # treats an empty-content section as not found; the frozen `sections` list
    # carries char counts, so the two agree.
    produced = {s["type"] for s in sections if s["chars"] > 0}
    missing = [t for t in expected_wanted_types(entry["slug"]) if t not in produced]

    return {
        "slug": entry["slug"],
        "valid": not reasons,
        "section_typed": not reasons and not missing,
        "missing_types": missing,
        "recall": recall,
        "kept_chars": kept_chars,
        "gold_chars": gold_chars,
        "reasons": reasons,
    }


def format_readiness_report(entries: dict[str, Any]) -> str:
    """Render the per-paper verdict table, BOTH columns.

    Printed side by side on purpose. A single "VALID" column reads as "this
    paper is correctly segmented", which it does not mean and cannot mean:
    char recall cannot see confusion between two types that are both on the
    keep-list, and five of the seven CONCAT-valid papers are missing a wanted
    type.
    """
    lines = [
        "",
        "=== Recall-comparison readiness ===",
        "  CONCAT = valid over the concatenated extractor blob "
        "(what today's pipeline feeds the extractors).",
        "  TYPED  = also produces every wanted section type gold records, "
        "under the right label.",
        "  CONCAT does NOT imply TYPED. SEG-7 (inverting the keep-list) "
        "needs TYPED.",
        "",
        f"  {'paper':<24}{'CONCAT':>8}{'TYPED':>8}  notes",
    ]
    concat = typed = 0
    for slug in PAPERS:
        entry = entries.get(slug)
        if entry is None:
            lines.append(f"  {slug:<24}{'MISSING':>8}{'-':>8}  not measured")
            continue
        row = recall_validity(entry)
        concat += bool(row["valid"])
        typed += bool(row["section_typed"])
        notes = list(row["reasons"])
        if row["missing_types"]:
            notes.append(f"missing type(s): {', '.join(row['missing_types'])}")
        lines.append(
            f"  {slug:<24}"
            f"{'VALID' if row['valid'] else 'INVALID':>8}"
            f"{'VALID' if row['section_typed'] else 'INVALID':>8}"
            f"  {'; '.join(notes) if notes else '-'}"
        )
    total = len(PAPERS)
    lines.append("")
    lines.append(
        f"  {concat}/{total} valid for a concatenated-blob recall comparison; "
        f"{typed}/{total} also correctly section-typed"
    )
    return "\n".join(lines)


# =============================================================================
# Gold-entity visibility (SEG-4 Decision 3)
# =============================================================================
#
# Character count cannot tell "recovered content" from "swallowed the next
# section", and SEG-4 deliberately REMOVES characters (-3,797 on cskg2) while
# making the paper more correct. A char-only guard would score that change a
# regression, so the corpus needs a metric that moves the right way.
#
# A gold entity group is *visible* when its canonical name or any acceptable
# alias occurs in the extractor input under case-insensitive word-boundary
# matching -- the same rule docs/ground-truth/segmenter-findings.md used, so
# the numbers stay comparable with what is already written down.

# Gold records, most authoritative first. `reconciled/` is the answer key;
# `human/` and `claude/` are the independent reviews it was built from and are
# used only as a fallback so a paper with a single review still reports.
GOLD_DIRS = ("reconciled", "human", "claude")

# Gold sections holding surface forms. `expected_topics` is excluded: it is a
# closed taxonomy the extractor binds a Literal to, never matched against text.
# `problems` is excluded: its entries are restatements, not surface forms.
GOLD_ENTITY_KEYS = ("expected_concepts", "expected_models", "expected_methods")


def gold_path(slug: str, corpus_dir: Path | None = None) -> Path | None:
    """The most authoritative gold record for ``slug``, or None if it has none."""
    corpus_dir = CORPUS_DIR if corpus_dir is None else corpus_dir
    for sub in GOLD_DIRS:
        path = corpus_dir / sub / f"paper_{slug}.gold.yml"
        if path.is_file():
            return path
    return None


def gold_entity_groups(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Read ``(canonical, surface_forms)`` pairs out of one gold record.

    ``surface_forms`` includes the canonical name, so a group with no aliases
    is still matchable.
    """
    import yaml  # lazy: only the --entities path needs PyYAML

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups: list[tuple[str, tuple[str, ...]]] = []
    for key in GOLD_ENTITY_KEYS:
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            canonical = (item.get("canonical") or "").strip()
            if not canonical:
                continue
            aliases = [
                str(a).strip()
                for a in (item.get("acceptable_aliases") or [])
                if str(a).strip()
            ]
            groups.append((canonical, tuple([canonical, *aliases])))
    return groups


def _surface_visible(surface: str, text: str) -> bool:
    """Case-insensitive word-boundary containment.

    ``\\b`` is wrong at a non-word edge -- "DyGIE++" ends in '+', where ``\\b``
    would demand a following word character -- so the boundary is asserted only
    on the sides where the surface form actually starts/ends with a word
    character.
    """
    if not surface:
        return False
    left = r"\b" if surface[0].isalnum() or surface[0] == "_" else ""
    right = r"\b" if surface[-1].isalnum() or surface[-1] == "_" else ""
    return re.search(left + re.escape(surface) + right, text, re.IGNORECASE) is not None


def entity_visibility(
    slug: str,
    extractor_input: str,
    gold_text: str,
    corpus_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Gold-entity visibility for one paper, or None when it has no gold record.

    Three numbers, because two of them would mislead:

    ``total``    gold entity groups in the record.
    ``ceiling``  groups findable in the gold text *itself*. A group below the
                 ceiling is a gold-curation artifact (a quote from a section
                 the keep-list drops, or an alias spelled differently), not
                 something the segmenter can win back.
    ``visible``  groups findable in the extractor input. This is the number a
                 segmenter change moves.
    """
    path = gold_path(slug, corpus_dir)
    if path is None:
        return None
    groups = gold_entity_groups(path)
    visible = [
        canonical
        for canonical, forms in groups
        if any(_surface_visible(f, extractor_input) for f in forms)
    ]
    reachable = [
        canonical
        for canonical, forms in groups
        if any(_surface_visible(f, gold_text) for f in forms)
    ]
    return {
        "slug": slug,
        "source": path.parent.name,
        "total": len(groups),
        "ceiling": len(reachable),
        "visible": len(visible),
        "missed": sorted(set(reachable) - set(visible)),
    }


def entity_visibility_corpus(
    entries: dict[str, Any],
    corpus_dir: Path | None = None,
) -> dict[str, Any]:
    """Visibility for every paper in ``entries`` that has a gold record."""
    corpus_dir = CORPUS_DIR if corpus_dir is None else corpus_dir
    out: dict[str, Any] = {}
    for slug, entry in entries.items():
        gold_text = (corpus_dir / f"paper_{slug}.txt").read_text(encoding="utf-8")
        row = entity_visibility(
            slug, entry["extractor_input"], gold_text, corpus_dir,
        )
        if row is not None:
            out[slug] = row
    return out


def format_entities_report(rows: dict[str, Any]) -> str:
    """Render the gold-entity visibility table."""
    lines = [
        "",
        "=== Gold-entity visibility in the extractor input ===",
        f"  {'paper':<24}{'gold':>7}{'reach':>7}{'seen':>7}{'of reach':>10}"
        "  record",
    ]
    if not rows:
        lines.append("  (no gold records found)")
        return "\n".join(lines)
    for slug in PAPERS:
        r = rows.get(slug)
        if r is None:
            continue
        pct = r["visible"] / r["ceiling"] if r["ceiling"] else 0.0
        lines.append(
            f"  {slug:<24}{r['total']:>7}{r['ceiling']:>7}{r['visible']:>7}"
            f"{pct:>10.1%}  {r['source']}"
        )
    for slug in PAPERS:
        r = rows.get(slug)
        if r and r["missed"]:
            lines.append(f"    {slug} unreachable: {', '.join(r['missed'])}")
    return "\n".join(lines)


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


def run_committed(freeze: bool = False, entities: bool = False) -> int:
    """Committed-corpus arm: measure, freeze or drift-check. Returns exit code."""
    entries, errors = measure_committed_corpus()
    for err in errors:
        print(f"  ERROR {err}", file=sys.stderr)

    print(format_committed_report(entries))
    print(format_readiness_report(entries))
    if entities:
        print(format_entities_report(entity_visibility_corpus(entries)))

    if freeze:
        written = write_frozen(entries)
        print(f"\nFrozen: {len(written)} fixtures under {FROZEN_DIR}")
        return 1 if errors else 0

    drifted: list[str] = []
    for slug, entry in entries.items():
        frozen = load_frozen(slug)
        if frozen is None:
            drifted.append(f"  {slug}: NO FROZEN FIXTURE ({frozen_path(slug)})")
            continue
        drifted.extend(frozen_drift(frozen, entry))

    if drifted or errors:
        print("\nFAILED: segmenter output drifted from the frozen fixtures.")
        print("\n".join(drifted))
        print(
            "\nIf this drift is the intended effect of a segmenter change, "
            "re-freeze with:\n"
            "  python scripts/measure_segmentation.py --freeze\n"
            "and justify the fixture diff in the PR."
        )
        return 1

    print("\nOK: committed corpus matches the frozen fixtures.")
    return 0


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
    parser.add_argument(
        "--corpus",
        choices=("pdf", "committed"),
        default="pdf",
        help=(
            "pdf: gitignored ground-truth-papers/ (local only). "
            "committed: the paper_<slug>.txt gold fixtures (runs in CI)."
        ),
    )
    parser.add_argument(
        "--freeze",
        action="store_true",
        help=(
            "re-pin the committed-corpus fixtures under "
            "fixtures/segmenter_frozen/ (implies --corpus committed)"
        ),
    )
    parser.add_argument(
        "--entities",
        action="store_true",
        help=(
            "also report gold-entity visibility in the extractor input, for "
            "the papers that have a gold record (implies --corpus committed)"
        ),
    )
    args = parser.parse_args(argv)

    if args.freeze or args.entities or args.corpus == "committed":
        return run_committed(freeze=args.freeze, entities=args.entities)

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
