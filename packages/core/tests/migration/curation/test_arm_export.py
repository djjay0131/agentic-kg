"""The `new` arm: consumable by the real runner, and honest about what it is.

Two obligations, and the second is the one that matters.

The first is shape: the export groups committed candidates by paper and drops
the types the gold files do not score.

The second is that the **evaluation runner can actually consume it**, checked by
importing the runner's own ``ArmPaper`` and ``build_new_arm`` and driving them,
not by asserting against a local restatement of their field names. A local
restatement would prove this package agrees with itself and would stay green
through any upstream rename.

Nothing here computes precision, recall, or coverage. ``evaluation/`` owns the
metrics and is not edited; this module hands it inputs.
"""

from __future__ import annotations

from pathlib import Path

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    STRUCTURED_IDENTITY_POLICY,
    curated_arm,
    run_curation,
)
from agentic_kg.migration.evaluation.adapter import build_surface_index
from agentic_kg.migration.evaluation.arms import ArmUnavailable, BuiltArm, build_new_arm
from agentic_kg.migration.evaluation.corpus import (
    ArmEntity,
    ArmPaper,
    load_reconciled_papers,
)
from agentic_kg.migration.ingestion.arm_export import as_arm_payload

from ._synthetic import CSKG_GOLD_TOPIC, graded_entity_candidate


def _runner_papers(arm_papers) -> tuple[ArmPaper, ...]:
    """The export's payload as the runner's OWN ``ArmPaper`` / ``ArmEntity``.

    Constructed through ``as_arm_payload`` and the runner's types rather than by
    handing ``ShadowArmPaper`` straight to ``build_new_arm``: the local
    dataclass is field-compatible today, and a test that relied on that would
    stay green through an upstream rename it was supposed to catch.
    """
    return tuple(
        ArmPaper(
            slug=payload["slug"],
            status=payload["status"],
            entities=tuple(ArmEntity(**entity) for entity in payload["entities"]),
            citations=tuple(payload["citations"]),
        )
        for payload in as_arm_payload(arm_papers)
    )


def _index(chain_root: Path):
    return build_surface_index(load_reconciled_papers(chain_root))


def _graded_slugs(chain_root: Path) -> frozenset[str]:
    return frozenset(p.slug for p in load_reconciled_papers(chain_root))


# --------------------------------------------------------------------------
# The honest null
# --------------------------------------------------------------------------


def test_deferred_graded_entities_produce_an_honest_null_not_a_zero(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
    doi_to_slug: dict[str, str],
) -> None:
    """The real corpus: graded entities were found, deferred, and not committed.

    An empty ``ArmOutput`` here would grade to a measured recall of 0.0, which
    asserts the new pipeline found nothing. The fact is that it found them and
    the policy declined to auto-apply them. Opposite conclusions from the same
    run.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    arm = curated_arm(result, shadow_candidates, doi_to_slug=doi_to_slug)

    assert arm.arm_papers is None
    assert arm.available is False
    assert arm.graded_committed == 0
    assert arm.graded_deferred > 0, (
        "the null is only honest if graded entities were actually deferred; "
        "with nothing deferred this would be a measured zero"
    )
    assert str(arm.graded_deferred) in (arm.reason or "")


def test_an_unavailable_arm_is_reported_as_unavailable_by_the_real_runner(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    doi_to_slug: dict[str, str],
    chain_root: Path,
) -> None:
    """``build_new_arm(None, ...)`` — the runner's own honest-null path."""
    result = run_curation(
        shadow_candidates, config=enabled_config, confidence_policy=CONTRACT_DEFAULT_POLICY
    )
    arm = curated_arm(result, shadow_candidates, doi_to_slug=doi_to_slug)
    built = build_new_arm(
        arm.arm_papers, _index(chain_root), graded_slugs=_graded_slugs(chain_root)
    )
    assert isinstance(built, ArmUnavailable)


def test_nothing_deferred_and_nothing_committed_is_a_measured_zero(
    enabled_config: MigrationConfig, doi_to_slug: dict[str, str]
) -> None:
    """The discriminator: honest-null must not have become "never report a zero".

    A run whose graded-type candidates were neither deferred nor rejected —
    here, a run with no graded-type candidates at all — produced nothing
    *because there was nothing*. That is a real zero and must be graded, not
    hidden behind a reason string.
    """
    result = run_curation([], config=enabled_config)
    arm = curated_arm(result, [], doi_to_slug=doi_to_slug)
    assert (arm.graded_deferred, arm.graded_rejected, arm.graded_uncommitted) == (0, 0, 0)
    assert arm.reason is None
    assert arm.arm_papers is not None
    assert {p.slug for p in arm.arm_papers} == set(doi_to_slug.values())
    assert all(p.entities == () for p in arm.arm_papers)


# --------------------------------------------------------------------------
# The committed arm, consumed by the real runner
# --------------------------------------------------------------------------


def test_a_committed_graded_entity_becomes_a_runner_arm_paper(
    enabled_config: MigrationConfig,
    memory_store: object,
    doi_to_slug: dict[str, str],
) -> None:
    """A curated, committed Topic reaches the runner's own ``ArmPaper``.

    Constructed through ``kg_eval.arms.ArmPaper``, the type
    ``build_new_arm`` consumes — so a field rename upstream fails here rather
    than being absorbed by a local dataclass that happens to match.
    """
    candidate = graded_entity_candidate()
    result = run_curation(
        [candidate],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert result.committed, "the synthetic AUTO candidate did not commit"

    arm = curated_arm(result, [candidate], doi_to_slug=doi_to_slug)
    assert arm.arm_papers is not None
    assert arm.graded_committed == 1

    papers = _runner_papers(arm.arm_papers)
    cskg = next(p for p in papers if p.slug == "cskg")
    assert [e.name for e in cskg.entities] == [CSKG_GOLD_TOPIC]
    assert [e.bucket for e in cskg.entities] == ["topics"]


def test_the_real_runner_builds_a_graded_arm_from_the_curated_output(
    enabled_config: MigrationConfig,
    memory_store: object,
    doi_to_slug: dict[str, str],
    chain_root: Path,
) -> None:
    """End of the seam: ``build_new_arm`` returns a runnable arm carrying it.

    This is the claim the whole subpackage exists to support — that the curated
    output is the `new` arm rather than something that would need an adapter in
    ``evaluation/``. ``evaluation/`` is untouched; only its published entry
    point is called.
    """
    candidate = graded_entity_candidate()
    result = run_curation(
        [candidate],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    arm = curated_arm(result, [candidate], doi_to_slug=doi_to_slug)
    papers = _runner_papers(arm.arm_papers or ())

    built = build_new_arm(papers, _index(chain_root), graded_slugs=_graded_slugs(chain_root))
    assert isinstance(built, BuiltArm)
    assert built.arm_id == "new"
    surfaces = {
        alias.key
        for candidate_out in built.output.candidates
        for alias in getattr(candidate_out, "aliases", ())
    }
    # Paper-scoped: the runner keys a candidate as "<slug>/<canonical>" because a
    # recall obligation belongs to one paper. Asserting the bare surface would
    # pass against an arm that had lost the paper attribution entirely.
    assert surfaces == {f"cskg/{CSKG_GOLD_TOPIC}"}


def test_an_uncommitted_plan_contributes_nothing_to_the_arm(
    enabled_config: MigrationConfig,
    memory_store: object,
    doi_to_slug: dict[str, str],
) -> None:
    """Planned is not committed: a STALE replay must not populate the arm.

    The arm joins on ``committed_candidate_ids``. Joining on the plan instead
    would report a batch the graph refused as canonical fact, and the number
    would look identical.
    """
    candidate = graded_entity_candidate()
    kwargs = dict(
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    first = run_curation([candidate], **kwargs)
    assert first.committed

    replay = run_curation([candidate], **kwargs)
    assert replay.committed is False
    assert replay.planned_candidate_ids == first.planned_candidate_ids

    arm = curated_arm(replay, [candidate], doi_to_slug=doi_to_slug)
    assert arm.graded_committed == 0
    assert arm.graded_uncommitted == 1
    assert arm.arm_papers is None
    assert "STALE" in (arm.reason or "")
