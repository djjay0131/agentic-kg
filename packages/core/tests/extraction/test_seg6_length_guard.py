"""SEG-6: an upper length guard between the segmenter and the LLM call.

Spec: ``llm/features/seg6-extractor-input-length-guard.md``.

``_build_extractor_section_text`` joined the keep-list sections and handed the
result to five or six extractor calls with nothing bounding it.
``MIN_USABLE_CHARS = 250`` guarded the lower end; the upper end was open, and
``kg_construction_survey`` came through at **244,742 characters against a
7,152-character hand-verified gold — 34.2x**, because an unrecognized heading
let ``introduction`` run for 94 pages.

The framing that matters is not cost. Two arms of a legacy-vs-new comparison
can be fed inputs differing by 30x with no signal that anything is wrong: one
arm is scored on a haystack, the other on a needle, and the difference is
attributed to the extractor. An input with no upper bound is not a reproducible
experimental condition.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from agentic_kg.ingestion import (
    MAX_EXTRACTOR_CHARS,
    _build_extractor_section_text,
    _truncate_extractor_input,
)

REPO = Path(__file__).resolve().parents[4]
SEG_BASELINE = REPO / "scripts" / "seg_baseline.json"
FROZEN_DIR = (
    Path(__file__).resolve().parents[1]
    / "extraction" / "fixtures" / "segmenter_frozen"
)


class _Section:
    def __init__(self, type_value: str, content: str):
        self.section_type = type("T", (), {"value": type_value})()
        self.title = type_value
        self.content = content


class _Doc:
    def __init__(self, *sections):
        self.sections = list(sections)


# =============================================================================
# AC-1, AC-2 — the bound itself
# =============================================================================


class TestBound:
    def test_input_at_or_below_the_cap_is_untouched(self, caplog):
        text = "word " * 100
        with caplog.at_level(logging.WARNING, logger="agentic_kg.ingestion"):
            assert _truncate_extractor_input(text, "doi:x") == text
        assert not caplog.records

    def test_input_exactly_at_the_cap_is_untouched(self):
        text = "a" * MAX_EXTRACTOR_CHARS
        assert _truncate_extractor_input(text, "doi:x") == text

    def test_oversized_input_comes_back_within_the_cap(self):
        text = ("paragraph body text. " * 40 + "\n\n") * 500
        assert len(text) > MAX_EXTRACTOR_CHARS
        assert len(_truncate_extractor_input(text, "doi:x")) <= MAX_EXTRACTOR_CHARS

    def test_the_survey_sized_input_is_bounded(self):
        """The case this exists for, at its measured size."""
        text = ("survey section body. " * 40 + "\n\n") * 300
        assert len(text) > 244_742
        assert len(_truncate_extractor_input(text, "survey")) <= MAX_EXTRACTOR_CHARS


# =============================================================================
# AC-3 — determinism
# =============================================================================


class TestDeterminism:
    """Same input, same output, always. A guard that truncates to a
    time-, hash- or locale-dependent point would replace one unreproducible
    condition with another."""

    def test_truncation_is_repeatable(self):
        text = ("body. " * 50 + "\n\n") * 1000
        assert _truncate_extractor_input(text, "a") == _truncate_extractor_input(
            text, "a",
        )

    def test_truncating_an_already_truncated_string_is_a_no_op(self):
        text = ("body. " * 50 + "\n\n") * 1000
        once = _truncate_extractor_input(text, "a")
        assert _truncate_extractor_input(once, "a") == once

    def test_the_label_does_not_affect_the_output(self):
        text = ("body. " * 50 + "\n\n") * 1000
        assert _truncate_extractor_input(text, "a") == _truncate_extractor_input(
            text, "completely different label",
        )


# =============================================================================
# AC-4, AC-5, AC-6 — where the cut lands
# =============================================================================


class TestCutBoundary:
    def test_cuts_at_a_paragraph_boundary_when_one_exists(self):
        """Sections are joined with ``\\n\\n``, so this usually ends the input
        on a whole section rather than mid-paragraph."""
        para = "x" * 1000
        text = "\n\n".join([para] * 200)
        out = _truncate_extractor_input(text, "a")
        assert len(out) <= MAX_EXTRACTOR_CHARS
        assert not out.endswith("x" * 1000 + "x")
        # every retained paragraph is whole
        assert all(len(p) == 1000 for p in out.split("\n\n") if p)

    def test_cuts_at_a_word_boundary_when_there_is_no_paragraph(self):
        text = ("token " * (MAX_EXTRACTOR_CHARS // 3)).strip()
        assert "\n\n" not in text
        out = _truncate_extractor_input(text, "a")
        assert len(out) <= MAX_EXTRACTOR_CHARS
        assert out.endswith("token")  # no split token

    def test_hard_slices_when_there_is_no_boundary_at_all(self):
        text = "a" * (MAX_EXTRACTOR_CHARS * 2)
        out = _truncate_extractor_input(text, "a")
        assert len(out) == MAX_EXTRACTOR_CHARS

    def test_a_late_first_boundary_still_respects_the_cap(self):
        """A boundary existing only AFTER the cap must not be used."""
        text = "a" * (MAX_EXTRACTOR_CHARS + 10) + "\n\n" + "b" * 100
        out = _truncate_extractor_input(text, "a")
        assert len(out) <= MAX_EXTRACTOR_CHARS


# =============================================================================
# AC-7 — truncation is loud
# =============================================================================


class TestWarning:
    def test_truncation_logs_one_warning_with_the_numbers(self, caplog):
        """SEG-1's lesson: the failure mode that stayed hidden for months was
        the silent one."""
        text = ("body. " * 50 + "\n\n") * 1000
        with caplog.at_level(logging.WARNING, logger="agentic_kg.ingestion"):
            _truncate_extractor_input(text, "doi:10.1234/survey")
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1
        message = warnings[0]
        assert "doi:10.1234/survey" in message
        assert f"{len(text):,}" in message
        assert f"{MAX_EXTRACTOR_CHARS:,}" in message


# =============================================================================
# The cap's value has to stay defensible
# =============================================================================


class TestCapValue:
    def test_the_cap_clears_the_largest_healthy_paper_with_margin(self):
        """AC-9. If a future segmenter change legitimately grows a paper past
        the cap, this fails and the constant gets revisited deliberately rather
        than silently truncating real content."""
        baseline = json.loads(SEG_BASELINE.read_text(encoding="utf-8"))
        healthy = {
            slug: entry["chars"]
            for slug, entry in baseline.items()
            # The survey is the mis-segmented outlier this guard exists for;
            # including it in "healthy" would make the assertion circular.
            if slug != "kg_construction_survey"
        }
        largest = max(healthy.values())
        assert largest == 49_511, f"baseline moved: largest healthy is {largest}"
        assert MAX_EXTRACTOR_CHARS >= 2 * largest

    def test_the_survey_is_over_the_cap_in_the_pdf_baseline(self):
        """AC-10, asserted against the committed baseline rather than a
        gitignored PDF, so it runs in CI."""
        baseline = json.loads(SEG_BASELINE.read_text(encoding="utf-8"))
        assert baseline["kg_construction_survey"]["chars"] == 244_742
        assert baseline["kg_construction_survey"]["chars"] > MAX_EXTRACTOR_CHARS


# =============================================================================
# AC-8 — the guard must not fire on healthy input
# =============================================================================


class TestNoHealthyPaperIsAffected:
    def test_no_committed_corpus_paper_reaches_the_cap(self):
        """If this ever fails, the frozen fixtures and the guard disagree and
        one of them is wrong."""
        for path in sorted(FROZEN_DIR.glob("*.json")):
            entry = json.loads(path.read_text(encoding="utf-8"))
            assert entry["extractor_input_chars"] < MAX_EXTRACTOR_CHARS, path.name


# =============================================================================
# Wiring: the guard is actually reached from the builder
# =============================================================================


class TestBuilderAppliesTheGuard:
    def test_builder_truncates_an_oversized_document(self):
        big = ("body text here. " * 40 + "\n\n") * 1000
        doc = _Doc(_Section("abstract", big), _Section("introduction", big))
        assert len(_build_extractor_section_text(doc)) <= MAX_EXTRACTOR_CHARS

    def test_builder_leaves_a_normal_document_byte_identical(self):
        doc = _Doc(
            _Section("abstract", "An abstract."),
            _Section("related_work", "dropped"),
            _Section("introduction", "An introduction."),
        )
        assert _build_extractor_section_text(doc) == (
            "An abstract.\n\nAn introduction."
        )

    def test_builder_accepts_an_optional_label_for_the_warning(self, caplog):
        big = ("body text here. " * 40 + "\n\n") * 1000
        doc = _Doc(_Section("abstract", big))
        with caplog.at_level(logging.WARNING, logger="agentic_kg.ingestion"):
            _build_extractor_section_text(doc, label="doi:10.5555/x")
        assert any("doi:10.5555/x" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("doc", [None, _Doc()])
    def test_builder_still_short_circuits_on_an_empty_document(self, doc):
        assert _build_extractor_section_text(doc) == ""
