"""Handing shadow candidates to the evaluation runner — without a second evaluator.

PR #73 owns the metrics. It already defines the arm structure this path must
speak: `build_new_arm(arm_papers, index, graded_slugs=..., ...)` accepts a
`Sequence[ArmPaper] | None` and returns `ArmUnavailable` when passed `None` —
its own docstring says "wiring the new pipeline in is passing `arm_papers`, not
editing this module". So this module produces `arm_papers`, and nothing here
computes precision, recall, coverage or any other number.

:class:`ShadowArmPaper` / :class:`ShadowArmEntity` are field-for-field the
runner's `ArmPaper` / `ArmEntity`. They are defined here rather than imported
because PR #73 is not merged into this PR's base — importing a module that does
not exist yet would make this package unimportable. :func:`to_arm_papers`
returns plain dataclasses and :func:`as_arm_payload` returns dicts, and
`test_arm_export.py` **imports the real `ArmPaper` when it is present** and
constructs one from each payload, so the correspondence is checked against the
runner's own definition rather than against a local restatement of it (§9.0
obligation 3). When #73 merges, that test starts exercising the real types on
every run; until then it reports precisely why it could not.

**What must not happen next.** The candidates this exports come from the replay
client described in `replay.py`, whose responses are derived from the legacy
importer's output. Grading them as the `new` arm would compare the legacy arm
with a copy of itself and report a flattering, meaningless 1.0. The `new` arm
stays `ArmUnavailable` until a real provider recording exists;
:data:`UNAVAILABLE_REASON` is the sentence that says so, written to be dropped
into `ArmUnavailable.reason` verbatim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from agentic_kg.migration.ingestion._contracts import Candidate, EntityCandidate
from agentic_kg.migration.ingestion.documents import doi_from_locator
from agentic_kg.migration.ingestion.identity import normalize_doi

#: entity_type -> the runner's bucket name. The inverse of the runner's own
#: `BUCKET_TO_ENTITY_TYPE`; `Paper` and `Problem` are absent because the gold
#: files score exactly four categories and a fifth bucket would be graded
#: against nothing.
ENTITY_TYPE_TO_BUCKET: dict[str, str] = {
    "Topic": "topics",
    "ResearchConcept": "concepts",
    "Model": "models",
    "Method": "methods",
}

UNAVAILABLE_REASON = (
    "the KGIS shadow path runs, but only against a replay client whose responses "
    "are derived from the legacy importer's own committed output — not from a "
    "model. Grading it as the `new` arm would compare the legacy arm against a "
    "copy of itself and report a meaningless 1.0. A real provider recording "
    "(kgis RecordingCompletionClient, see migration/ingestion/replay.py) is the "
    "one remaining step; until it exists this arm is unavailable, not empty."
)


@dataclass(frozen=True)
class ShadowArmEntity:
    """Field-for-field the evaluation runner's `ArmEntity`."""

    slug: str
    bucket: str
    name: str
    aliases: tuple[str, ...] = ()
    quoted_text: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ShadowArmPaper:
    """Field-for-field the evaluation runner's `ArmPaper`."""

    slug: str
    status: str
    entities: tuple[ShadowArmEntity, ...] = ()
    citations: tuple[Any, ...] = ()


def _quoted_text(candidate: EntityCandidate) -> str | None:
    """The passage the model read, off the `source_passage` representation.

    `LLMExtractor._finalize` attaches it to every extracted candidate, so this
    is the candidate's own record of its grounding rather than a re-derivation.
    A structured candidate has no such representation and correctly gets `None`.
    """
    passage = candidate.representations.get("source_passage")
    return None if passage is None else passage.text


def to_arm_papers(
    candidates: Sequence[Candidate], *, doi_to_slug: dict[str, str]
) -> tuple[ShadowArmPaper, ...]:
    """Group submitted entity candidates into per-paper arm records.

    Only the four scored entity types are emitted. `Paper` and `Problem`
    candidates are dropped rather than assigned an invented bucket: the gold
    files score four categories, and a candidate in a fifth would become a false
    positive of whichever category it was filed under.

    Papers are keyed by DOI through the candidate's own `source_coordinates`,
    not by a slug guessed from the document id, because the DOI is the
    identifier the importer, the gold files and the curation table all
    independently speak.
    """
    grouped: dict[str, list[ShadowArmEntity]] = {slug: [] for slug in doi_to_slug.values()}
    for candidate in candidates:
        if not isinstance(candidate, EntityCandidate):
            continue
        bucket = ENTITY_TYPE_TO_BUCKET.get(candidate.entity_type)
        if bucket is None:
            continue
        doi = normalize_doi(doi_from_locator(candidate.source_coordinates.locator))
        slug = doi_to_slug.get(doi)
        if slug is None:
            continue
        grouped[slug].append(
            ShadowArmEntity(
                slug=slug,
                bucket=bucket,
                name=candidate.display_name or candidate.aliases[0].key,
                aliases=tuple(alias.key for alias in candidate.aliases),
                quoted_text=_quoted_text(candidate),
                confidence=candidate.scores.extraction_confidence,
            )
        )
    return tuple(
        ShadowArmPaper(slug=slug, status="extracted", entities=tuple(entities))
        for slug, entities in sorted(grouped.items())
    )


def as_arm_payload(papers: Sequence[ShadowArmPaper]) -> list[dict[str, Any]]:
    """The arm records as plain dicts, ready for `ArmPaper(**payload)`."""
    return [asdict(paper) for paper in papers]


__all__ = [
    "ENTITY_TYPE_TO_BUCKET",
    "UNAVAILABLE_REASON",
    "ShadowArmEntity",
    "ShadowArmPaper",
    "as_arm_payload",
    "to_arm_papers",
]
