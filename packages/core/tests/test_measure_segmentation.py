"""Tests for ``scripts/measure_segmentation.py`` (SEG-1 AC-10).

The script's *measurement* needs PyMuPDF and the gitignored
``ground-truth-papers/``, so it cannot run in CI. Its *comparison* logic —
the part that decides whether a change is a regression — is pure and is
tested here with injected fakes.

See: ``llm/features/seg1-roman-numeral-headings.md``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Load scripts/measure_segmentation.py as a module without polluting sys.path
# with the whole scripts/ directory (mirrors tests/test_smoke_assert.py).
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "measure_segmentation.py"
)
_spec = importlib.util.spec_from_file_location(
    "measure_segmentation", _SCRIPT_PATH,
)
measure_segmentation = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["measure_segmentation"] = measure_segmentation
_spec.loader.exec_module(measure_segmentation)  # type: ignore[union-attr]

ms = measure_segmentation


# =============================================================================
# Helpers
# =============================================================================


def _section(type_value: str, title: str, content: str):
    """A Section-alike: only .section_type.value, .title, .content are read."""
    return SimpleNamespace(
        section_type=SimpleNamespace(value=type_value),
        title=title,
        content=content,
    )


def _doc(*sections):
    return SimpleNamespace(sections=list(sections))


def _entry(chars: int, sections=None):
    """A baseline/measurement entry."""
    return {
        "chars": chars,
        "sections": sections if sections is not None else [["introduction", "I. INTRODUCTION"]],
    }


def _full_baseline(**overrides):
    """Every paper in PAPERS at a nominal value, so `compare` produces no
    MISSING rows unless a test asks for one."""
    base = {slug: _entry(1000) for slug in ms.PAPERS}
    base.update(overrides)
    return base


# =============================================================================
# measure_document — reducing a SegmentedDocument to comparable shape
# =============================================================================


class TestMeasureDocument:
    def test_counts_only_the_four_wanted_types(self):
        doc = _doc(
            _section("abstract", "Abstract", "a" * 10),
            _section("introduction", "I. INTRODUCTION", "b" * 20),
            _section("methods", "III. Methods", "c" * 30),
            _section("experiments", "IV. EVALUATION", "d" * 40),
            # Discarded by the keep-list, so excluded from `chars`.
            _section("related_work", "II. RELATED WORK", "e" * 5000),
            _section("references", "REFERENCES", "f" * 5000),
        )

        assert ms.measure_document(doc)["chars"] == 100

    def test_records_the_ordered_type_title_sequence(self):
        """The sequence catches a resegmentation that preserves the total."""
        doc = _doc(
            _section("introduction", "I. INTRODUCTION", "x"),
            _section("experiments", "IV. EVALUATION", "y"),
        )

        assert ms.measure_document(doc)["sections"] == [
            ["introduction", "I. INTRODUCTION"],
            ["experiments", "IV. EVALUATION"],
        ]

    def test_document_with_no_sections_is_zero(self):
        """The fact_completion pre-fix case."""
        result = ms.measure_document(_doc())

        assert result == {"chars": 0, "sections": []}


# =============================================================================
# compare — the verdict logic
# =============================================================================


class TestCompare:
    def test_identical_chars_and_shape_is_same(self):
        entry = _entry(8917, [["introduction", "Introduction"]])
        rows = ms.compare(_full_baseline(cskg=entry), _full_baseline(cskg=entry))

        cskg = next(r for r in rows if r["slug"] == "cskg")
        assert cskg["verdict"] == "SAME"
        assert cskg["delta"] == 0

    def test_more_chars_same_shape_is_improved(self):
        before = _entry(100, [["introduction", "I. INTRODUCTION"]])
        after = _entry(200, [["introduction", "I. INTRODUCTION"]])
        rows = ms.compare(
            _full_baseline(empire=before), _full_baseline(empire=after)
        )

        empire = next(r for r in rows if r["slug"] == "empire")
        assert empire["verdict"] == "IMPROVED"
        assert empire["delta"] == 100

    def test_more_chars_different_shape_is_reshaped(self):
        """What SEG-1 itself produces: new headings found, so the sequence
        changes as well as the total. Reported, but not a failure."""
        before = _entry(0, [])
        after = _entry(25586, [["introduction", "I. INTRODUCTION"]])
        rows = ms.compare(
            _full_baseline(fact_completion=before),
            _full_baseline(fact_completion=after),
        )

        row = next(r for r in rows if r["slug"] == "fact_completion")
        assert row["verdict"] == "RESHAPED"
        assert row["delta"] == 25586
        assert ms.failures(rows) == []

    def test_fewer_chars_is_regressed(self):
        """The hypothesis_generation case if hierarchical stripping were
        ever reintroduced: 49,511 -> 43,926."""
        rows = ms.compare(
            _full_baseline(hypothesis_generation=_entry(49511)),
            _full_baseline(hypothesis_generation=_entry(43926)),
        )

        row = next(r for r in rows if r["slug"] == "hypothesis_generation")
        assert row["verdict"] == "REGRESSED"
        assert row["delta"] == -5585
        assert [r["slug"] for r in ms.failures(rows)] == [
            "hypothesis_generation"
        ]

    def test_same_chars_different_shape_is_regressed(self):
        """A silent resegmentation. Identical totals are not a clean bill of
        health, so the shape check has to be able to fail on its own."""
        before = _entry(1000, [["methods", "III. Methods"]])
        after = _entry(1000, [["results", "B. RESULTS AND DISCUSSION"]])
        rows = ms.compare(
            _full_baseline(cskg2=before), _full_baseline(cskg2=after)
        )

        row = next(r for r in rows if r["slug"] == "cskg2")
        assert row["verdict"] == "REGRESSED"

    def test_paper_absent_from_baseline_is_new(self):
        baseline = _full_baseline()
        del baseline["empire"]
        rows = ms.compare(baseline, _full_baseline())

        row = next(r for r in rows if r["slug"] == "empire")
        assert row["verdict"] == "NEW"
        assert row["before"] is None
        # Adding a paper to the set must not fail the run.
        assert ms.failures(rows) == []

    def test_paper_that_failed_to_measure_is_missing_and_fails(self):
        """A paper silently dropped from the run is exactly how this class of
        defect stayed hidden, so MISSING must fail."""
        measured = _full_baseline()
        del measured["kg_validation_hitl"]
        rows = ms.compare(_full_baseline(), measured)

        row = next(r for r in rows if r["slug"] == "kg_validation_hitl")
        assert row["verdict"] == "MISSING"
        assert [r["slug"] for r in ms.failures(rows)] == ["kg_validation_hitl"]

    def test_row_order_follows_papers_not_dict_order(self):
        """Stable output ordering keeps the report diffable across runs."""
        rows = ms.compare(_full_baseline(), _full_baseline())

        assert [r["slug"] for r in rows] == list(ms.PAPERS)

    def test_every_paper_gets_exactly_one_row(self):
        rows = ms.compare(_full_baseline(), _full_baseline())

        assert len(rows) == len(ms.PAPERS)

    def test_baseline_missing_chars_key_is_treated_as_zero(self):
        """Defensive: a hand-edited baseline entry without `chars`."""
        rows = ms.compare(
            _full_baseline(cskg={"sections": []}),
            _full_baseline(cskg=_entry(500, [])),
        )

        row = next(r for r in rows if r["slug"] == "cskg")
        assert row["before"] == 0
        assert row["verdict"] == "IMPROVED"


# =============================================================================
# measure_corpus — injection boundary
# =============================================================================


class TestMeasureCorpus:
    def test_measures_every_paper_in_the_set(self):
        doc = _doc(_section("introduction", "I. INTRODUCTION", "x" * 42))

        measured, errors = ms.measure_corpus(
            segment=lambda text: doc,
            extract_text=lambda pdf: "text",
            pdf_dir=Path("nonexistent"),
        )

        assert set(measured) == set(ms.PAPERS)
        assert errors == []
        assert measured["cskg"]["chars"] == 42

    def test_passes_the_expected_pdf_path_per_slug(self):
        seen: list[Path] = []

        ms.measure_corpus(
            segment=lambda text: _doc(),
            extract_text=lambda pdf: seen.append(pdf) or "",
            pdf_dir=Path("papers"),
        )

        assert Path("papers") / ms.PAPERS["empire"] in seen

    def test_a_failed_paper_is_reported_not_swallowed(self):
        """One unreadable PDF must not abort the other seven, and must not
        pass silently either."""

        def _extract(pdf: Path) -> str:
            if ms.PAPERS["cskg2"] in str(pdf):
                raise OSError("corrupt PDF")
            return "text"

        measured, errors = ms.measure_corpus(
            segment=lambda text: _doc(),
            extract_text=_extract,
            pdf_dir=Path("papers"),
        )

        assert "cskg2" not in measured
        assert len(measured) == len(ms.PAPERS) - 1
        assert errors == ["cskg2: corrupt PDF"]

    def test_missing_paper_becomes_a_failing_row(self):
        """End-to-end of the two behaviours above: an unmeasurable paper
        reaches `failures` rather than being counted as unchanged."""

        def _extract(pdf: Path) -> str:
            if ms.PAPERS["cskg2"] in str(pdf):
                raise OSError("corrupt PDF")
            return "text"

        # The seven readable papers must match the baseline exactly, so that
        # cskg2's MISSING row is the only failure the assertion can see.
        healthy = _doc(_section("introduction", "I. INTRODUCTION", "x" * 1000))
        measured, _ = ms.measure_corpus(
            segment=lambda text: healthy,
            extract_text=_extract,
            pdf_dir=Path("papers"),
        )

        rows = ms.compare(_full_baseline(), measured)
        assert [r["slug"] for r in ms.failures(rows)] == ["cskg2"]


# =============================================================================
# Baseline I/O
# =============================================================================


class TestBaselineIO:
    def test_round_trips_a_measurement(self, tmp_path):
        path = tmp_path / "seg_baseline.json"
        measured = _full_baseline(empire=_entry(17953))

        ms._write_baseline(measured, path)
        loaded, error = ms._load_baseline(path)

        assert error is None
        assert loaded == measured

    def test_written_baseline_is_sorted_and_newline_terminated(self, tmp_path):
        """Keeps the re-baseline diff readable in review, which is the whole
        argument for storing it as a file instead of a literal."""
        path = tmp_path / "seg_baseline.json"

        ms._write_baseline({"zebra": _entry(1), "alpha": _entry(2)}, path)
        raw = path.read_text(encoding="utf-8")

        assert raw.endswith("\n")
        assert raw.index('"alpha"') < raw.index('"zebra"')
        assert json.loads(raw)["alpha"]["chars"] == 2

    def test_missing_baseline_returns_an_actionable_error(self, tmp_path):
        loaded, error = ms._load_baseline(tmp_path / "absent.json")

        assert loaded is None
        assert "cannot open baseline" in error

    def test_baseline_that_is_not_an_object_returns_an_error(self, tmp_path):
        """Gate 2: the baseline is hand-editable, so a wrong shape must
        produce an actionable message, not an AttributeError in `compare`."""
        path = tmp_path / "seg_baseline.json"
        path.write_text('["cskg", "empire"]', encoding="utf-8")

        loaded, error = ms._load_baseline(path)

        assert loaded is None
        assert "keyed by paper slug" in error
        assert "got list" in error

    def test_baseline_with_a_non_object_entry_names_the_offenders(
        self, tmp_path,
    ):
        path = tmp_path / "seg_baseline.json"
        path.write_text('{"cskg": 8917, "empire": {"chars": 1}}', encoding="utf-8")

        loaded, error = ms._load_baseline(path)

        assert loaded is None
        assert "non-object entries" in error
        assert "cskg" in error

    def test_unparseable_baseline_returns_an_error(self, tmp_path):
        path = tmp_path / "seg_baseline.json"
        path.write_text("{not json", encoding="utf-8")

        loaded, error = ms._load_baseline(path)

        assert loaded is None
        assert "invalid JSON" in error

    def test_committed_baseline_covers_the_whole_set(self):
        """Guards against a baseline that silently stops tracking a paper."""
        baseline, error = ms._load_baseline()

        assert error is None
        assert set(baseline) == set(ms.PAPERS)
        for slug, entry in baseline.items():
            assert "chars" in entry, slug
            assert "sections" in entry, slug

    def test_committed_baseline_records_the_seg1_outcome(self):
        """Pins the measured result so a future edit cannot quietly undo it.
        These are the AC-7 / AC-8 numbers."""
        baseline, _ = ms._load_baseline()

        assert baseline["fact_completion"]["chars"] == 25586
        assert baseline["empire"]["chars"] == 17953
        assert ["introduction", "I. INTRODUCTION"] in baseline["empire"][
            "sections"
        ]


# =============================================================================
# format_report
# =============================================================================


class TestFormatReport:
    def test_lists_every_paper_and_the_verdict_tally(self):
        report = ms.format_report(ms.compare(_full_baseline(), _full_baseline()))

        for slug in ms.PAPERS:
            assert slug in report
        assert "8 same" in report

    def test_signs_the_delta(self):
        rows = ms.compare(
            _full_baseline(empire=_entry(12405)),
            _full_baseline(empire=_entry(17953)),
        )

        assert "+5,548" in ms.format_report(rows)

    def test_renders_absent_values_without_crashing(self):
        """NEW and MISSING rows carry None for before/after/delta."""
        baseline = _full_baseline()
        del baseline["empire"]
        measured = _full_baseline()
        del measured["cskg"]

        report = ms.format_report(ms.compare(baseline, measured))

        assert "NEW" in report
        assert "MISSING" in report

    def test_output_is_ascii_only(self):
        """The report prints to a Windows console under cp1252; a stray
        em dash renders as mojibake."""
        rows = ms.compare(_full_baseline(), _full_baseline())
        baseline = _full_baseline()
        del baseline["empire"]

        for report in (
            ms.format_report(rows),
            ms.format_report(ms.compare(baseline, _full_baseline())),
        ):
            report.encode("ascii")  # raises UnicodeEncodeError on failure


# =============================================================================
# main — exit codes
# =============================================================================


class TestMain:
    def test_returns_2_when_the_pdf_directory_is_absent(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The common operator error: cloned the repo, has no PDFs."""
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path / "ground-truth-papers")

        assert ms.main([]) == 2
        assert "gitignored" in capsys.readouterr().err

    def test_returns_2_when_the_baseline_is_absent(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", tmp_path / "absent.json")
        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: _doc(), lambda pdf: ""),
        )

        assert ms.main([]) == 2
        assert "--update-baseline" in capsys.readouterr().err

    def test_returns_0_and_says_so_when_nothing_regressed(
        self, tmp_path, monkeypatch, capsys,
    ):
        path = tmp_path / "seg_baseline.json"
        ms._write_baseline(_full_baseline(), path)
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", path)
        doc = _doc(_section("introduction", "I. INTRODUCTION", "x" * 1000))
        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: doc, lambda pdf: ""),
        )

        assert ms.main([]) == 0
        assert "no paper regressed" in capsys.readouterr().out

    def test_returns_1_and_prints_the_rebaseline_command_on_regression(
        self, tmp_path, monkeypatch, capsys,
    ):
        path = tmp_path / "seg_baseline.json"
        ms._write_baseline(_full_baseline(cskg=_entry(9000)), path)
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", path)
        doc = _doc(_section("introduction", "I. INTRODUCTION", "x" * 10))
        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: doc, lambda pdf: ""),
        )

        assert ms.main([]) == 1
        out = capsys.readouterr().out
        assert "REGRESSED" in out
        assert "--update-baseline" in out
        assert "justify the diff" in out

    def test_update_baseline_rewrites_the_file(
        self, tmp_path, monkeypatch, capsys,
    ):
        path = tmp_path / "seg_baseline.json"
        ms._write_baseline(_full_baseline(cskg=_entry(1)), path)
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", path)
        doc = _doc(_section("introduction", "I. INTRODUCTION", "x" * 777))
        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: doc, lambda pdf: ""),
        )

        assert ms.main(["--update-baseline"]) == 0
        assert "Baseline rewritten" in capsys.readouterr().out
        rewritten, _ = ms._load_baseline(path)
        assert rewritten["cskg"]["chars"] == 777

    def test_update_baseline_still_fails_when_a_paper_could_not_be_measured(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Re-baselining an incomplete run would bake a missing paper into
        the guard, permanently hiding it."""
        path = tmp_path / "seg_baseline.json"
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", path)

        def _extract(pdf: Path) -> str:
            raise OSError("no such file")

        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: _doc(), _extract),
        )

        assert ms.main(["--update-baseline"]) == 1
        assert "ERROR" in capsys.readouterr().err

    def test_measurement_error_fails_the_run(
        self, tmp_path, monkeypatch, capsys,
    ):
        path = tmp_path / "seg_baseline.json"
        ms._write_baseline(_full_baseline(), path)
        monkeypatch.setattr(ms, "PDF_DIR", tmp_path)
        monkeypatch.setattr(ms, "BASELINE_PATH", path)

        def _extract(pdf: Path) -> str:
            if ms.PAPERS["empire"] in str(pdf):
                raise ValueError("unreadable")
            return ""

        doc = _doc(_section("introduction", "I. INTRODUCTION", "x" * 1000))
        monkeypatch.setattr(
            ms, "_real_segment_and_extract",
            lambda: (lambda text: doc, _extract),
        )

        assert ms.main([]) == 1
        captured = capsys.readouterr()
        assert "empire: unreadable" in captured.err
        assert "MISSING" in captured.out


class TestScriptWiring:
    def test_wanted_sections_match_the_ingestion_keep_list(self):
        """The script duplicates the keep-list to avoid importing the whole
        ingestion dependency chain. If ingestion's list changes, this must
        fail rather than measure the wrong thing."""
        from agentic_kg.ingestion import _EXTRACTOR_WANTED_SECTIONS

        assert ms.WANTED_SECTIONS == _EXTRACTOR_WANTED_SECTIONS

    def test_paper_set_matches_the_hand_segmenter(self):
        """`segment_ground_truth.py` is the hand-verified-boundary sibling.
        Measuring a different set of papers than gold was built from would
        make the two incomparable."""
        gt = importlib.util.spec_from_file_location(
            "segment_ground_truth",
            _SCRIPT_PATH.parent / "segment_ground_truth.py",
        )
        module = importlib.util.module_from_spec(gt)  # type: ignore[arg-type]
        gt.loader.exec_module(module)  # type: ignore[union-attr]

        assert set(ms.PAPERS) == set(module.PAPERS)
        for slug, spec in module.PAPERS.items():
            assert ms.PAPERS[slug] == spec["pdf"], slug


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
