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


def test_gold_wanted_types_mirror_matches_the_generator():
    """``_GOLD_WANTED_TYPES`` mirrors each paper's ``wanted`` list in
    ``scripts/segment_ground_truth.py``. If the two drift, the TYPED verdict
    scores papers against section types gold never claimed they have --
    `kg_construction_survey` is a 94-page survey with no methods and no
    experiments, and `llm_ontology_gen`'s approach lives in its experiments
    section, so neither should be marked as missing `methods`.
    """
    source = (
        Path(__file__).resolve().parents[4]
        / "scripts" / "segment_ground_truth.py"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("PAPERS: dict[str, dict] = {") : source.index("def find_marker(")
    ]
    namespace: dict = {}
    exec(block, namespace)  # noqa: S102 - our own committed source
    generator = {
        slug: tuple(spec["wanted"]) for slug, spec in namespace["PAPERS"].items()
    }
    mirrored = {slug: ms.expected_wanted_types(slug) for slug in SLUGS}
    assert mirrored == generator


def test_readiness_verdicts_are_pinned(measured: dict):
    """Which papers are valid, on BOTH verdicts, pinned so that any change is
    a deliberate reviewed edit rather than a side-effect noticed after a
    migration claim has been made.

    CONCAT and TYPED are pinned separately on purpose. The PR #69 review found
    that a single "VALID" column reads as "correctly segmented", which it is
    not: char recall cannot see confusion between two types that are BOTH on
    the keep-list, so a methods span mislabelled `introduction` keeps all its
    characters and scores 99.9%.
    """
    concat = {s for s in SLUGS if ms.recall_validity(measured[s])["valid"]}
    typed = {
        s for s in SLUGS if ms.recall_validity(measured[s])["section_typed"]
    }

    assert concat == {
        # SEG-3 (run-in and letter-spaced abstracts) moved five papers in.
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
        #             gold-wanted text lost. SEG-11 (heading context) or SEG-7
        #             (keep-list), both out of this change's scope.
    }, f"CONCAT validity changed: now {sorted(concat)}"

    assert typed == {
        "cskg2",
        "hypothesis_generation",
        # Gold records only abstract + introduction for the survey and only
        # abstract + introduction + experiments for llm_ontology_gen, and the
        # segmenter produces exactly those -- so both are correctly typed.
        "kg_construction_survey",
        "llm_ontology_gen",
        # NOT typed, all three for the same underlying reason (SEG-5: a heading
        # named after the contribution, or an over-strict anchor):
        #   cskg               missing methods -- "The Computer Science
        #                      Knowledge Graph" is unmatched, so its
        #                      14,988-char gold span is absorbed into a
        #                      20,271-char `introduction`. Char recall still
        #                      reads 99.9%, which is exactly the blind spot.
        #   fact_completion    missing methods -- "III. SciCheck"
        #   kg_validation_hitl missing experiments -- "5. Experiment design
        #                      and implementation"
    }, f"TYPED validity changed: now {sorted(typed)}"


def test_concat_validity_does_not_imply_correct_typing(measured: dict):
    """The property the two-column verdict exists to make unmissable. If this
    ever passes trivially (because TYPED caught up with CONCAT), delete it and
    say so in the PR -- do not weaken it."""
    rows = [ms.recall_validity(measured[s]) for s in SLUGS]
    concat_only = [r for r in rows if r["valid"] and not r["section_typed"]]
    assert concat_only, "expected at least one CONCAT-valid, TYPED-invalid paper"
    assert {r["slug"] for r in concat_only} == {
        "cskg", "fact_completion", "kg_validation_hitl",
    }
    for row in concat_only:
        assert row["missing_types"], row["slug"]


def test_missing_types_agrees_with_ingestions_own_warning(measured: dict):
    """The PR shipped two instruments that disagreed: production's
    ``_missing_wanted_sections`` would warn on papers the readiness report
    called VALID. They are now the same fact, so they must not diverge.

    The one legitimate difference is the denominator: production checks all
    four keep-list types, the readiness report checks what gold records for
    that paper. So production's list is a superset.
    """
    from agentic_kg.extraction.section_segmenter import SectionSegmenter
    from agentic_kg.ingestion import _missing_wanted_sections

    segmenter = SectionSegmenter()
    for slug in SLUGS:
        doc = segmenter.segment(
            ms.committed_text_path(slug).read_text(encoding="utf-8"),
        )
        production = set(_missing_wanted_sections(doc))
        readiness = set(ms.recall_validity(measured[slug])["missing_types"])
        assert readiness <= production, (
            f"{slug}: readiness reports a missing type production does not"
        )
