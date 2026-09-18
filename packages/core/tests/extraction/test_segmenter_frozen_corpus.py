"""Drift guard for ``SectionSegmenter`` over the committed ground-truth corpus.

This is the test that makes a legacy-vs-new segmentation comparison
*reproducible*. Both arms of such a comparison have to be run over
byte-identical input and be shown to have produced byte-identical output where
nothing was meant to change; otherwise a recall delta is unattributable.

**Why it can run in CI at all.** ``scripts/measure_segmentation.py``'s original
corpus is ``ground-truth-papers/``, which is gitignored, so nothing about the
segmenter's behaviour on real papers was ever checked automatically. The eight
``paper_<slug>.txt`` fixtures in ``fixtures/ground_truth_chain/`` *are*
committed, and carry the same prose.

**What those fixtures actually are — and what that buys.** They are not raw
PDF text. ``scripts/segment_ground_truth.py`` produced them from hand-verified
section boundaries, keeping only the four sections the importer wants. That
makes them a sharper instrument than raw text for this particular question:

    every character in a ``paper_<slug>.txt`` is already gold-wanted, so any
    character the segmenter fails to route into a keep-list section is a
    segmenter recall failure with no competing explanation.

The cost is that failure modes living in the material gold *excludes* -- page
furniture, references, a 94-page survey's 237 KB of body -- are invisible here
and still need the PDF corpus. That is recorded in
``docs/ground-truth/corpus-readiness.md``, not papered over.

**What is pinned, and what is not.** The fixtures pin the segmenter's *output*:
the source text's sha256, the ordered section list, and the exact
``extractor_input`` string the extractors would receive. They record the
segmenter source sha and the keep-list sha as *provenance* but do not assert on
them -- a refactor that leaves the output byte-identical is not drift, and
pinning a commit would be simultaneously too strict and too weak, since
PyMuPDF and the PDF bytes drift independently of this repo.

Re-pin deliberately, with a reviewable diff, exactly as SEG-1 established for
``seg_baseline.json``::

    python scripts/measure_segmentation.py --freeze
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "measure_segmentation.py"
)
_spec = importlib.util.spec_from_file_location(
    "measure_segmentation", _SCRIPT_PATH,
)
measure_segmentation = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["measure_segmentation"] = measure_segmentation
_spec.loader.exec_module(measure_segmentation)  # type: ignore[union-attr]

ms = measure_segmentation

SLUGS = tuple(ms.PAPERS)


@pytest.fixture(scope="module")
def measured() -> dict:
    """Segment the whole committed corpus once; the segmenter is stateless."""
    entries, errors = ms.measure_committed_corpus()
    assert not errors, f"committed corpus unreadable: {errors}"
    return entries


# =============================================================================
# The corpus itself
# =============================================================================


def test_every_paper_has_a_committed_text_fixture():
    """A slug with no ``paper_<slug>.txt`` would be silently skipped, which is
    precisely the class of defect the original harness had."""
    missing = [s for s in SLUGS if not ms.committed_text_path(s).is_file()]
    assert not missing, f"no committed text for: {missing}"


def test_every_paper_has_a_frozen_fixture():
    missing = [s for s in SLUGS if not ms.frozen_path(s).is_file()]
    assert not missing, (
        f"no frozen fixture for: {missing}. Run "
        "`python scripts/measure_segmentation.py --freeze`."
    )


def test_frozen_dir_holds_no_orphans():
    """An orphan fixture is a paper that was dropped from the set without the
    drift guard noticing it stopped being checked."""
    on_disk = {p.stem for p in ms.FROZEN_DIR.glob("*.json")}
    assert on_disk == set(SLUGS), (
        f"frozen fixtures {sorted(on_disk)} != papers {sorted(SLUGS)}"
    )


# =============================================================================
# Drift
# =============================================================================


@pytest.mark.parametrize("slug", SLUGS)
def test_committed_corpus_matches_frozen_output(slug: str, measured: dict):
    """The whole point. Fails naming the paper and the section that moved."""
    frozen = ms.load_frozen(slug)
    assert frozen is not None, f"unreadable frozen fixture for {slug}"
    drift = ms.frozen_drift(frozen, measured[slug])
    assert not drift, "\n".join(
        ["segmenter output drifted:", *drift,
         "", "If intended: python scripts/measure_segmentation.py --freeze"],
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_frozen_fixture_is_internally_consistent(slug: str):
    """The stored shas must describe the stored text.

    Without this, a hand-edited fixture could pin a hash that matches nothing,
    and the drift check would compare current output against a fiction.
    """
    frozen = ms.load_frozen(slug)
    assert frozen is not None
    text = ms.committed_text_path(slug).read_text(encoding="utf-8")
    assert frozen["source_sha256"] == ms.sha256_text(text)
    assert frozen["source_chars"] == len(text)
    assert frozen["extractor_input_sha256"] == ms.sha256_text(
        frozen["extractor_input"],
    )
    assert frozen["extractor_input_chars"] == len(frozen["extractor_input"])
    assert frozen["keeplist_sha256"] == ms.sha256_text(
        json.dumps(frozen["keeplist"]),
    )


# =============================================================================
# Guarding the guard
# =============================================================================


def test_drift_is_detected_when_a_section_changes(measured: dict):
    """A drift checker that cannot fail is a green light, not a guard."""
    frozen = ms.load_frozen("cskg")
    mutated = json.loads(json.dumps(measured["cskg"]))
    mutated["sections"][0]["chars"] += 1
    drift = ms.frozen_drift(frozen, mutated)
    assert drift
    assert any("section[0] CHANGED" in line for line in drift)
    assert any("cskg: DRIFT" in line for line in drift)


def test_drift_is_detected_when_the_extractor_input_changes(measured: dict):
    frozen = ms.load_frozen("cskg")
    mutated = json.loads(json.dumps(measured["cskg"]))
    mutated["extractor_input"] += "x"
    mutated["extractor_input_chars"] += 1
    mutated["extractor_input_sha256"] = ms.sha256_text(
        mutated["extractor_input"],
    )
    drift = ms.frozen_drift(frozen, mutated)
    assert any("extractor_input_chars" in line for line in drift)
    assert any("extractor_input: text differs" in line for line in drift)


def test_drift_is_detected_when_the_source_text_changes(measured: dict):
    """Re-running ``segment_ground_truth.py`` against a new PyMuPDF would move
    the input out from under the frozen output. That must be loud."""
    frozen = ms.load_frozen("cskg")
    mutated = json.loads(json.dumps(measured["cskg"]))
    mutated["source_sha256"] = "0" * 64
    drift = ms.frozen_drift(frozen, mutated)
    assert any("source_sha256" in line for line in drift)


def test_a_segmenter_source_change_alone_is_not_drift(measured: dict):
    """Output is pinned, the commit is not. A pure refactor stays green, and
    the reviewer is still told the source moved."""
    frozen = json.loads(json.dumps(measured["cskg"]))
    frozen["segmenter_sha256"] = "f" * 64
    assert ms.frozen_drift(frozen, measured["cskg"]) == []


def test_a_keeplist_change_is_reported_in_the_drift_header(measured: dict):
    """SEG-7 will change the keep-list. When it does, the chars delta has two
    possible causes and the report must say which one moved."""
    frozen = json.loads(json.dumps(measured["cskg"]))
    frozen["keeplist"] = ["abstract"]
    frozen["extractor_input"] = "different"
    frozen["extractor_input_chars"] = len("different")
    frozen["extractor_input_sha256"] = ms.sha256_text("different")
    drift = ms.frozen_drift(frozen, measured["cskg"])
    assert any("keep-list changed" in line for line in drift)


# =============================================================================
# The mirror of ingestion's keep-list must not rot
# =============================================================================


def test_keeplist_mirror_matches_ingestion():
    """``WANTED_SECTIONS`` is duplicated in the script to keep it free of the
    ingestion import chain (feedparser / openai / langgraph). A duplicate that
    silently diverges would make every frozen fixture describe an extractor
    input production never produces."""
    from agentic_kg.ingestion import _EXTRACTOR_WANTED_SECTIONS

    assert tuple(ms.WANTED_SECTIONS) == tuple(_EXTRACTOR_WANTED_SECTIONS)


def test_extractor_input_mirror_matches_ingestion(measured: dict):
    """Same argument, for the join logic rather than the list."""
    from agentic_kg.extraction.section_segmenter import SectionSegmenter
    from agentic_kg.ingestion import _build_extractor_section_text

    segmenter = SectionSegmenter()
    for slug in SLUGS:
        doc = segmenter.segment(
            ms.committed_text_path(slug).read_text(encoding="utf-8"),
        )
        assert ms.build_extractor_input(doc) == _build_extractor_section_text(
            doc,
        ), f"{slug}: mirrored extractor-input builder diverged from ingestion"


# =============================================================================
# Readiness verdicts (the number the migration claim rests on)
# =============================================================================


def test_readiness_verdicts_are_pinned(measured: dict):
    """Which papers are valid for a recall comparison, pinned so that any
    change to the number is a deliberate, reviewed edit rather than a
    side-effect noticed after a migration claim has been made.

    Update this set in the same commit as the segmenter change that moves it,
    and quote the before/after in the PR.
    """
    valid = {
        slug
        for slug in SLUGS
        if ms.recall_validity(measured[slug])["valid"]
    }
    assert valid == {
        # SEG-3 (run-in and letter-spaced abstracts) moved five papers into
        # this set by giving them the abstract gold says they have.
        "cskg",
        "fact_completion",
        "hypothesis_generation",
        "kg_construction_survey",
        "kg_validation_hitl",
        "llm_ontology_gen",
        # SEG-4 (Nature vocabulary + positional abstract) moved the sixth.
        "cskg2",
        # Still out:
        #   empire -- "Threats to Validity" is a genuine SUBSECTION inside
        #             gold's methods span (IV. RESEARCH APPROACH). The
        #             segmenter promotes it to top level and types it
        #             limitations, which the keep-list drops: 3,208 chars of
        #             gold-wanted text lost. That is a heading-context problem
        #             (SEG-11) or a keep-list one (SEG-7), not an abstract one,
        #             and both are out of this change's scope.
    }, f"recall-comparison validity changed: now {sorted(valid)}"


# =============================================================================
# Gold-entity visibility (the metric that can score a deliberate reduction)
# =============================================================================


def test_gold_entity_visibility_is_pinned(measured: dict):
    """Character count cannot tell recovered content from a swallowed section,
    and SEG-4 deliberately *removes* characters while making ``cskg2`` more
    correct. This is the number that has to move the right way instead.

    ``reach`` is the ceiling: entity groups findable in the gold text itself.
    A group below the ceiling is a gold-curation artifact -- an alias spelled
    differently, or a quote from a section the keep-list drops -- and is not
    something a segmenter change can win back.
    """
    rows = ms.entity_visibility_corpus(measured)
    actual = {
        slug: (r["visible"], r["ceiling"], r["total"])
        for slug, r in rows.items()
    }
    assert actual == {
        "cskg": (13, 13, 19),
        # SEG-4 PR-1 (Nature vocabulary): 23 -> 25. The two recovered are
        # named in the test below; one of them is the citation chain's spine
        # concept.
        "cskg2": (25, 25, 30),
        "fact_completion": (15, 15, 21),
        "empire": (2, 2, 5),
    }, f"gold-entity visibility changed: {actual}"


def test_cskg2_recovered_the_two_entities_seg4_targeted(measured: dict):
    """Named, not counted. SEG-4 claimed two specifically --
    ``scientific knowledge graph`` (the citation chain's spine concept) and
    ``knowledge-centric paradigm``, both of which live in
    ``Background & Summary``. Before PR-1 that heading was unrecognized and
    the whole section was absorbed into ``Methods``, so losing them silently
    would corrupt every cross-paper accumulation number.
    """
    rows = ms.entity_visibility_corpus(measured)
    assert rows["cskg2"]["missed"] == [], (
        "SEG-4 PR-1 recovered both; if this regresses, Background & Summary "
        "is being absorbed by Methods again"
    )


def test_papers_without_a_gold_record_are_reported_as_absent(measured: dict):
    """Four of the eight have no gold record at all. A metric that silently
    returned zero for them would read as a catastrophic recall failure."""
    rows = ms.entity_visibility_corpus(measured)
    assert set(rows) == {"cskg", "cskg2", "fact_completion", "empire"}
