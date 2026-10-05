"""Can the ``new`` arm be graded after the 0.3.0 / f68d1d7 re-pin? Measured: no.

And the *reason* is the point, because it changed. When ``build_new_arm`` was
written the honest statement was "the new pipeline has not been built". That is
no longer true: the shadow path runs, and one run over the committed corpus
emits 252 candidates of which 29 are gradeable surfaces on the two reconciled
papers. So the arm is not empty — it is **declined**.

It is declined because every one of those 29 surfaces traces to the replay
client in ``replay.py``, whose responses are derived from the legacy importer's
own committed output. Grading them as ``new`` would compare the legacy arm
against a copy of itself. The missing input is a **model recording**, which
neither of the two new pins provides and no amount of re-pinning could: the
KGIS fix is to *adjudication routing* and the KGCS fix is to *record identity*.
Neither is a producer.

This module states that as numbers rather than as a paragraph, so the null is
auditable:

* 29 gradeable surfaces exist for the graded slugs (66 across all eight papers)
  — the null is a declination, not an emptiness;
* 29 of 29 carry ``REPLAY_CONFIDENCE`` — every one traces to the replay
  constant, none to a model;
* 0 committed model recordings exist on disk;
* the arm therefore reports ``ArmUnavailable``, and every metric about it is
  ``None`` rather than ``0.0``.

The last bullet is the one that would do real damage if it were wrong. A graded
recall of ``0.0`` asserts the new pipeline found nothing. The measured truth is
that it found 29 things and the harness declines to score them.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from agentic_kg.migration.evaluation.arms import (
    NEW_ARM,
    ArmUnavailable,
    BuiltArm,
    build_new_arm,
)
from agentic_kg.migration.evaluation.arms import (
    UNAVAILABLE_REASON as ARMS_REASON,
)
from agentic_kg.migration.ingestion import GRADED_SLUGS, normalize_doi, to_arm_papers
from agentic_kg.migration.ingestion.arm_export import (
    UNAVAILABLE_REASON as EXPORT_REASON,
)
from agentic_kg.migration.ingestion.replay import REPLAY_CONFIDENCE
from kg_eval.ablation import AblationVerdict

pytest.importorskip("kg_eval")

#: Gradeable surfaces the shadow run exports for the two reconciled papers.
#: The count behind the honest null.
GRADEABLE_SURFACES = 29

#: ... and across all eight corpus papers.
EXPORTABLE_SURFACES = 66

REPO_ROOT = Path(__file__).resolve().parents[4].parent
CHAIN_ROOT = REPO_ROOT / "packages/core/tests/extraction/fixtures/ground_truth_chain"
IMPORTER_OUTPUT = REPO_ROOT / "docs/ground-truth/importer-output"


@pytest.fixture(scope="module")
def surface_index():
    """The reconciled-gold surface index the evaluation arms are built against.

    Declared here rather than reused from ``tests/migration/evaluation/conftest.py``:
    this module needs that package's fixtures *and* the shadow-ingestion
    ``shadow_run``, and only one of the two conftests is on this path.
    """
    from agentic_kg.migration.evaluation.adapter import build_surface_index
    from agentic_kg.migration.evaluation.corpus import load_reconciled_papers

    assert CHAIN_ROOT.is_dir(), f"ground-truth chain fixtures missing at {CHAIN_ROOT}"
    return build_surface_index(load_reconciled_papers(CHAIN_ROOT))


@pytest.fixture(scope="module")
def report():
    """The full evaluation, run exactly as CI runs it: no ``new_arm_papers``."""
    from agentic_kg.migration.evaluation.runner import run_evaluation

    assert IMPORTER_OUTPUT.is_dir(), f"importer output missing at {IMPORTER_OUTPUT}"
    return run_evaluation(chain_root=CHAIN_ROOT, importer_output_dir=IMPORTER_OUTPUT)


@pytest.fixture(scope="module")
def exported(shadow_run):
    """The shadow run's candidates as evaluation arm records."""
    from agentic_kg.migration.ingestion import load_corpus

    doi_to_slug = {normalize_doi(p.doi): p.slug for p in load_corpus()}
    return to_arm_papers(
        (*shadow_run.paper_candidates, *shadow_run.candidates), doi_to_slug=doi_to_slug
    )


@pytest.fixture(scope="module")
def graded(exported):
    return tuple(p for p in exported if p.slug in GRADED_SLUGS)


# --- the counts behind the null ------------------------------------------------


def test_the_shadow_path_does_produce_gradeable_surfaces(exported, graded) -> None:
    """The null is a declination, not an emptiness.

    If this ever measured zero, the arm would be unavailable for a completely
    different reason and the report would have to say so.
    """
    assert sum(len(p.entities) for p in exported) == EXPORTABLE_SURFACES
    assert {p.slug for p in graded} == set(GRADED_SLUGS)
    assert sum(len(p.entities) for p in graded) == GRADEABLE_SURFACES
    assert Counter(e.bucket for p in graded for e in p.entities) == {
        "methods": 14,
        "models": 8,
        "concepts": 7,
    }


def test_every_gradeable_surface_traces_to_the_replay_constant(graded) -> None:
    """Why they are declined, measured rather than asserted.

    ``REPLAY_CONFIDENCE`` is a declared constant standing in for a model that
    never ran — the importer output records no per-item confidence. Every
    surface carrying it is a surface whose content came from the legacy arm.
    A single value other than this constant would mean something else produced
    a candidate, and the declination would need re-examining.
    """
    confidences = {e.confidence for p in graded for e in p.entities}
    assert confidences == {REPLAY_CONFIDENCE}
    assert len([e for p in graded for e in p.entities]) == GRADEABLE_SURFACES


def test_no_model_recording_is_committed() -> None:
    """The missing input, as a fact about the repository rather than a claim.

    ``replay_client_from_file`` is the slot a real recording drops into. Until
    a recording exists there is nothing to load, and this is what makes the
    declination unavoidable rather than a policy choice.
    """
    candidates = [
        path
        for path in REPO_ROOT.rglob("*recording*")
        if path.is_file()
        and ".venv" not in path.parts
        and ".git" not in path.parts
        and path.suffix in {".json", ".yml", ".yaml"}
    ]
    assert candidates == [], f"a recording exists - re-run the new arm: {candidates}"


# --- what the harness reports as a result --------------------------------------


def test_the_new_arm_is_unavailable_and_says_why_it_is_declined(surface_index) -> None:
    arm = build_new_arm(None, surface_index, graded_slugs=frozenset(GRADED_SLUGS))
    assert isinstance(arm, ArmUnavailable)
    assert arm.arm_id == NEW_ARM
    assert "replay client" in arm.reason
    assert "not from a" in arm.reason and "model" in arm.reason
    # The reason that used to be given is no longer the true one.
    assert "has not been built" not in arm.reason


def test_the_producing_and_consuming_sides_state_the_same_reason() -> None:
    """Two modules, one sentence — asserted, not hoped for.

    ``evaluation`` cannot import ``ingestion`` (it would drag the whole KGIS
    stack into a package that only needs ``kg_eval``), so the sentence is
    restated. A restatement that drifts is worse than no restatement: the
    producing side would explain the declination one way and the report another.
    """
    assert ARMS_REASON == EXPORT_REASON


def test_no_metric_about_the_new_arm_is_reported_as_a_number(report) -> None:
    """The rule that matters: ``None``, never ``0.0``.

    A recall of 0.0 on an arm that emitted 29 surfaces would be a false
    statement about the pipeline, not a conservative one.
    """
    new = report.arm(NEW_ARM)
    assert not new.available
    assert new.overall is None
    assert new.typed == ()

    involving_new = [
        a for a in report.ablations if NEW_ARM in (a.baseline_arm, a.enhanced_arm)
    ]
    assert involving_new, "an empty list would make this vacuous"
    for ablation in involving_new:
        assert ablation.verdict is AblationVerdict.INSUFFICIENT_EVIDENCE
        assert ablation.diff_ci is None
        for side in (ablation.baseline, ablation.enhanced):
            if side is not None:
                assert side.value is None, (
                    f"{ablation.metric} reported {side.value!r} for an arm that "
                    f"did not run; insufficient evidence must not become a number"
                )


# --- the blocker is provenance, not plumbing -----------------------------------


def test_the_arm_builds_the_moment_admissible_output_exists(graded, surface_index) -> None:
    """Handing the exported surfaces in produces a real arm — structure only.

    This asserts that wiring the new pipeline in is *passing data*, which is
    what makes "unavailable" a statement about provenance rather than about
    missing code. It deliberately asserts **no metric**: the candidates here
    are the legacy arm's own findings, and any score computed from them is the
    self-comparison this whole arrangement exists to refuse. The numbers are
    not computed, not asserted, and must not be published.
    """
    from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper

    arm_papers = tuple(
        ArmPaper(
            slug=p.slug,
            status=p.status,
            entities=tuple(ArmEntity(**vars(e)) for e in p.entities),
        )
        for p in graded
    )
    built = build_new_arm(
        arm_papers, surface_index, graded_slugs=frozenset(GRADED_SLUGS)
    )
    assert isinstance(built, BuiltArm)
    assert built.arm_id == NEW_ARM
    assert built.coverage.graded_attempted == len(GRADED_SLUGS)
    assert built.output.candidates, "the plumbing produced no candidates at all"
