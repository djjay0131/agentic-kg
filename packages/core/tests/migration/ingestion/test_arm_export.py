"""The hand-off to PR #73's evaluation runner — no second evaluator here.

Two kinds of assertion, and the second is the one that matters.

The first checks the export's own shape: buckets, grouping by DOI, the four
scored types and no others.

The second checks that the runner can actually consume it, by importing the
runner's own `ArmPaper` / `ArmEntity` and constructing them from the payload.
That import only succeeds once PR #73 lands on this branch's base, so until then
the test skips *with the reason spelled out* rather than asserting against a
local restatement of the runner's field names — which would prove that this
package agrees with itself and would stay green through any upstream rename
(§9.0 obligation 3).
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.ingestion.arm_export import (
    ENTITY_TYPE_TO_BUCKET,
    UNAVAILABLE_REASON,
    ShadowArmEntity,
    ShadowArmPaper,
    as_arm_payload,
    to_arm_papers,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.pipeline import ShadowRunResult


def _doi_to_slug(corpus: tuple[CorpusPaper, ...]) -> dict[str, str]:
    return {paper.doi.casefold(): paper.slug for paper in corpus}


def _arm(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> tuple[ShadowArmPaper, ...]:
    return to_arm_papers(
        [*shadow_run.paper_candidates, *shadow_run.candidates],
        doi_to_slug=_doi_to_slug(corpus),
    )


def test_every_corpus_paper_gets_an_arm_record(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Eight records, including the ones that produced no entities.

    A paper that extracted nothing must still appear, with an empty entity
    tuple. Dropping it would silently shrink the denominator, and a recall
    computed over a shrinking denominator rises for free.
    """
    papers = _arm(shadow_run, corpus)
    assert {p.slug for p in papers} == {p.slug for p in corpus}


def test_the_export_is_not_empty(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    papers = _arm(shadow_run, corpus)
    assert sum(len(p.entities) for p in papers) > 0


def test_only_the_four_scored_buckets_appear(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """`Paper` and `Problem` are dropped, not filed under a guessed bucket.

    The gold files score four categories. A candidate placed in a fifth — or
    squeezed into one of the four — becomes a false positive of whichever
    category received it, and the precision it depresses is a number about a
    different type entirely.
    """
    papers = _arm(shadow_run, corpus)
    buckets = {e.bucket for p in papers for e in p.entities}
    assert buckets
    assert buckets <= set(ENTITY_TYPE_TO_BUCKET.values())
    assert "papers" not in buckets and "problems" not in buckets


def test_entities_are_grouped_under_the_paper_they_came_from(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Grouping is by the candidate's own coordinate, not by position.

    The defect: assigning entities to papers by iteration order. With eight
    papers processed in sequence it would produce a plausible-looking
    distribution and be wrong for every entity after the first paper.
    """
    papers = _arm(shadow_run, corpus)
    populated = [p for p in papers if p.entities]
    assert len(populated) > 1, "only one paper has entities; grouping is untestable"
    for paper in populated:
        assert all(e.slug == paper.slug for e in paper.entities)


def test_entities_carry_their_grounding_and_confidence(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    entities = [e for p in _arm(shadow_run, corpus) for e in p.entities]
    assert entities
    for entity in entities:
        assert entity.name
        assert entity.aliases
        assert entity.confidence is not None
        assert 0.0 <= entity.confidence <= 1.0
        assert entity.quoted_text, (
            f"{entity.name!r} has no source passage; the candidate's "
            f"`source_passage` representation is how the runner sees grounding"
        )


def test_the_payload_is_json_shaped(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    payload = as_arm_payload(_arm(shadow_run, corpus))
    assert payload
    assert {"slug", "status", "entities", "citations"} == set(payload[0])


def test_the_runner_can_construct_its_own_types_from_this_payload(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Checked against the runner's definition, not against a local copy.

    Skipped, loudly, until PR #73 merges into this branch's base. A test that
    instead compared `dataclasses.fields(ShadowArmPaper)` to a transcribed list
    of names would run today and mean nothing: it would assert that this
    package matches its own restatement of the runner's contract.
    """
    corpus_module = pytest.importorskip(
        "agentic_kg.migration.evaluation.corpus",
        reason=(
            "PR #73 (the evaluation runner) is not on this branch's base yet, so "
            "the real ArmPaper/ArmEntity cannot be imported. This test starts "
            "exercising them the moment it merges."
        ),
    )
    arm_paper = corpus_module.ArmPaper
    arm_entity = corpus_module.ArmEntity

    payloads = as_arm_payload(_arm(shadow_run, corpus))
    assert payloads
    built = [
        arm_paper(
            slug=payload["slug"],
            status=payload["status"],
            entities=tuple(arm_entity(**entity) for entity in payload["entities"]),
            citations=tuple(payload["citations"]),
        )
        for payload in payloads
    ]
    assert len(built) == len(payloads)
    assert sum(len(p.entities) for p in built) > 0


def test_the_new_arm_reason_names_why_it_is_unavailable() -> None:
    """The `new` arm stays `ArmUnavailable`, and this is the sentence saying so.

    Not a style check. The runner draws a hard line between an unavailable arm
    and an empty one — an empty arm grades to a measured recall of 0.0, which
    asserts the new pipeline found nothing. Here the fact is different again:
    the pipeline runs, but only against responses derived from the legacy
    arm's own output, so grading it would compare the legacy arm with a copy of
    itself and report a flattering 1.0.
    """
    assert "replay" in UNAVAILABLE_REASON
    assert "legacy" in UNAVAILABLE_REASON
    assert "unavailable, not empty" in UNAVAILABLE_REASON


def test_arm_types_are_field_compatible_with_each_other() -> None:
    """The two local dataclasses agree on the entity type they nest."""
    entity = ShadowArmEntity(slug="s", bucket="concepts", name="n")
    paper = ShadowArmPaper(slug="s", status="extracted", entities=(entity,))
    assert paper.entities[0] is entity
