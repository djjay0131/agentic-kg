"""The narrow adapter between the ground-truth chain fixtures and ``kg_eval``.

``kg_eval`` already is the evaluation framework — honest-null metrics
(``MetricValue.insufficient``), deterministic seeded bootstrap, a paired
ablation with a real INSUFFICIENT_EVIDENCE verdict, and a report renderer that
never prints ``0.000`` for an unmeasured value. None of that is reinvented here.
This module supplies the two things ``kg_eval`` cannot know: what our gold files
mean (:func:`build_goldset`) and how to turn an arm's output into
``Candidate`` objects that key against it (:func:`build_candidates`).

Three translation decisions carry the weight, and each is a place where a
plausible-looking shortcut would corrupt the numbers:

**Alias resolution happens candidate-side.** ``kg_eval`` matches on literal key
equality — deliberately, so that a true positive cannot be tuned. If gold
carried its aliases into the match key, either the key would stop being literal
or one gold entity would need several keys (and ``GoldSet`` rejects duplicate
keys outright). So the gold key is the canonical, full stop, and an alias→
canonical map rewrites *candidates* before they are keyed. The map was checked
against the corpus: across 140 aliases in the two reconciled files, no alias maps
to two canonicals and no alias is also another entry's canonical, so the
rewrite is unambiguous.

**``acceptable_extras`` are dropped before grading, not scored as anything.**
``SCHEMA.md`` says the right treatment is to subtract them from the precision
*denominator* and never to count them as recall misses. Dropping the candidate
does exactly that and nothing else. The filter is ordered so gold always wins: a
surface that resolves to a scored gold entity is kept even if some extras entry
also names it, because a recall obligation outranks a may-emit.

**Gold keys are paper-scoped.** See :meth:`ScoredEntity.scoped_key`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from kg_contracts.candidates import (
    Candidate,
    CandidateScores,
    EntityCandidate,
    RelationCandidate,
    SourceCoordinates,
)
from kg_contracts.evidence import (
    Evidence,
    EvidenceAvailability,
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
)
from kg_contracts.identity import EntityRef
from kg_eval.goldset import EvidenceSpan, GoldEntity, GoldRelation, GoldSet

from agentic_kg.migration.evaluation.corpus import (
    ANY_BUCKET,
    BUCKET_TO_ENTITY_TYPE,
    NAMED_RESOURCE_BUCKET,
    PAPER_DOIS,
    AcceptableExtra,
    ArmEntity,
    ArmPaper,
    CitationEdge,
    ReconciledPaper,
    normalize_doi,
    normalize_surface,
)

#: The one relation type this corpus grades. UPPER_SNAKE is enforced by
#: ``RelationCandidate``.
CITES = "CITES"

#: Entity type for a paper endpoint of a CITES edge.
PAPER_ENTITY_TYPE = "Paper"

#: Namespace for paper endpoints. DOI rather than slug: the slug is a label this
#: repo invented for eight files, the DOI is the identifier the importer, the
#: curation table and both reviews all independently speak. Endpoints are
#: case-folded (see :func:`normalize_doi`).
PAPER_NAMESPACE = "doi"

#: Locator prefix for an evidence span. A span's ``source_locator`` must match
#: the arm's ``Evidence.source_locator`` exactly for coverage to count, so the
#: two are built by the same function.
_SOURCE_TYPE = "paper_text"

#: Fixed timestamp for recorded evidence. Determinism is a requirement of this
#: runner (ADR-0009: the only randomness is the seeded bootstrap), and
#: ``Evidence.observed_at`` has no default — using ``datetime.now`` here would
#: make two runs of the same fixtures produce different ``content_hash``-adjacent
#: payloads and different reports.
RECORDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def source_locator(slug: str) -> str:
    """The stable locator for one paper's text."""
    return f"paper:{slug}"


def paper_ref(slug: str) -> str:
    """Render a paper endpoint as ``Paper:doi:<folded-doi>``.

    Raises for a slug with no DOI: silently minting an endpoint from a slug we
    cannot resolve would produce a relation key that can never match the other
    side, i.e. a guaranteed miss dressed up as a labelling fact.
    """
    doi = PAPER_DOIS.get(slug)
    if doi is None:
        raise KeyError(
            f"no DOI known for slug {slug!r}; add it to corpus.PAPER_DOIS "
            "(a citation endpoint cannot be graded without a resolvable identifier)"
        )
    return EntityRef(
        entity_type=PAPER_ENTITY_TYPE,
        namespace=PAPER_NAMESPACE,
        key=normalize_doi(doi),
    ).render()


# --------------------------------------------------------------------------
# Gold set
# --------------------------------------------------------------------------


def build_goldset(
    papers: Sequence[ReconciledPaper],
    *,
    gold_set_id: str,
    buckets: Iterable[str] | None = None,
    include_relations: bool = True,
    description: str | None = None,
) -> GoldSet:
    """Build a ``kg_eval`` gold set from reconciled answer keys.

    ``buckets`` narrows the entity slice — passing a single bucket is how
    per-type precision/recall is produced, since ``kg_eval`` reports one
    aggregate entity PRF per gold set rather than a breakdown by type.

    ``attempted`` is the number of papers the gold set covers. That is the
    denominator ``kg_eval`` uses for abstention and failure rates, and it is
    deliberately the *graded* population (two papers), not the corpus (eight):
    a rate whose denominator differs from the one precision and recall were
    computed over is not comparable with them.
    """
    wanted = frozenset(buckets) if buckets is not None else frozenset(BUCKET_TO_ENTITY_TYPE)

    entities: list[GoldEntity] = []
    for paper in papers:
        for entity in paper.entities:
            if entity.bucket not in wanted:
                continue
            entities.append(
                GoldEntity(
                    entity_type=entity.entity_type,
                    semantic_key=entity.scoped_key(),
                    display_name=entity.canonical,
                    evidence=(
                        EvidenceSpan(
                            source_locator=source_locator(entity.slug),
                            quote=entity.quoted_text,
                        )
                        if entity.quoted_text
                        else None
                    ),
                )
            )

    relations: list[GoldRelation] = []
    if include_relations:
        for paper in papers:
            for edge in paper.citations:
                relations.append(
                    GoldRelation(
                        relation_type=CITES,
                        subject=paper_ref(edge.citing_slug),
                        object=paper_ref(edge.cited_slug),
                        # No evidence span: the fixture records the citation as a
                        # slug list with no supporting quote, so there is nothing
                        # to demand coverage of. Asserting a span we do not have
                        # would make every citation an automatic span failure.
                        evidence=None,
                    )
                )

    return GoldSet(
        gold_set_id=gold_set_id,
        description=description,
        attempted=len(papers),
        entities=tuple(entities),
        relations=tuple(relations),
    )


# --------------------------------------------------------------------------
# Alias and extras indices
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceIndex:
    """Candidate-side lookup tables derived from the reconciled gold.

    ``alias_to_canonical`` maps ``(slug, bucket, normalized surface)`` to the
    canonical a candidate should be keyed as. ``extras`` maps the same shape
    (with :data:`ANY_BUCKET` for ad-hoc buckets) to the extras entry that
    excuses it.
    """

    alias_to_canonical: Mapping[tuple[str, str, str], str]
    extras: Mapping[tuple[str, str, str], AcceptableExtra]

    def resolve(self, slug: str, bucket: str, surface: str) -> str | None:
        """The canonical this surface names in this paper+bucket, if any."""
        return self.alias_to_canonical.get((slug, bucket, normalize_surface(surface)))

    def excuse(self, slug: str, bucket: str, surface: str) -> AcceptableExtra | None:
        """The ``acceptable_extras`` entry covering this surface, if any.

        Checks the candidate's own bucket first, then :data:`ANY_BUCKET` for the
        ad-hoc buckets. ``named_resources`` lives in the second: nothing in the
        fixture says which of the four categories the importer will emit
        "Wikidata" under, so an extra recorded there excuses it wherever it lands.
        """
        key = normalize_surface(surface)
        return self.extras.get((slug, bucket, key)) or self.extras.get((slug, ANY_BUCKET, key))


def build_surface_index(papers: Sequence[ReconciledPaper]) -> SurfaceIndex:
    """Build the alias and extras indices, rejecting ambiguity rather than guessing.

    Two ambiguities are fatal and both are checked, because either one would
    make the alias rewrite change which gold entity a candidate is credited to:

    * one surface naming two different canonicals in the same paper+bucket;
    * an alias of one entry that is also the canonical of another.

    Neither occurs in the corpus today (verified over all 140 aliases in the two
    reconciled files). Raising rather than picking a winner is what keeps that
    true as gold files are added — a future alias collision fails the load
    instead of quietly reassigning credit.
    """
    aliases: dict[tuple[str, str, str], str] = {}
    canonicals: set[tuple[str, str, str]] = set()

    for paper in papers:
        for entity in paper.entities:
            canonicals.add((entity.slug, entity.bucket, normalize_surface(entity.canonical)))

    for paper in papers:
        for entity in paper.entities:
            for surface in entity.surfaces():
                key = (entity.slug, entity.bucket, normalize_surface(surface))
                existing = aliases.get(key)
                if existing is not None and existing != entity.canonical:
                    raise ValueError(
                        f"ambiguous surface {surface!r} in {entity.slug}/{entity.bucket}: "
                        f"names both {existing!r} and {entity.canonical!r}. Resolving it "
                        "either way would silently move credit between two gold entities."
                    )
                is_own_canonical = normalize_surface(surface) == normalize_surface(entity.canonical)
                if not is_own_canonical and key in canonicals:
                    raise ValueError(
                        f"alias {surface!r} of {entity.canonical!r} is also the canonical of "
                        f"another gold entry in {entity.slug}/{entity.bucket}; the alias rewrite "
                        "would merge two distinct recall obligations."
                    )
                aliases[key] = entity.canonical

    extras: dict[tuple[str, str, str], AcceptableExtra] = {}
    for paper in papers:
        for extra in paper.extras:
            extras.setdefault(
                (extra.slug, extra.bucket, normalize_surface(extra.name)), extra
            )

    return SurfaceIndex(alias_to_canonical=aliases, extras=extras)


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterOutcome:
    """What the extras filter did to one arm entity, and why.

    Kept as data rather than being applied silently: "how many of this arm's
    emissions left the precision denominator, and on whose authority" is a
    number a reader of the report must be able to audit. 61 surface forms are
    excusable in this corpus and 41 of them are blocked on an open decision;
    an unexplained drop of that size would be indistinguishable from a bug.
    """

    entity: ArmEntity
    kept: bool
    resolved_canonical: str | None
    excused_by: AcceptableExtra | None


def filter_candidates(
    papers: Sequence[ArmPaper],
    index: SurfaceIndex,
    *,
    exclude_extras: bool = True,
    exclude_named_resources: bool = True,
) -> tuple[FilterOutcome, ...]:
    """Decide, per emitted entity, whether it is graded or excused.

    Order matters and is the whole correctness argument: an entity that resolves
    to a scored gold canonical is **always** kept, even when an extras entry also
    names that surface. ``SCHEMA.md`` allows an extra to be "a generic surface
    form of a scored entry", so the two sets genuinely overlap, and excusing a
    surface that satisfies a recall obligation would delete a true positive.

    ``exclude_named_resources`` is separate from ``exclude_extras`` because the
    two are answerable questions of different kinds. The ordinary extras have an
    agreed disposition; ``named_resources`` does not (its disposition is the open
    decision), so the two flags let the runner show what the number would be
    under each option instead of hard-coding one.
    """
    outcomes: list[FilterOutcome] = []
    for paper in papers:
        for entity in paper.entities:
            canonical = index.resolve(entity.slug, entity.bucket, entity.name)
            if canonical is not None:
                outcomes.append(FilterOutcome(entity, True, canonical, None))
                continue
            excuse = index.excuse(entity.slug, entity.bucket, entity.name)
            if excuse is not None:
                is_named_resource = excuse.source_bucket == NAMED_RESOURCE_BUCKET
                drop = exclude_named_resources if is_named_resource else exclude_extras
                if drop:
                    outcomes.append(FilterOutcome(entity, False, None, excuse))
                    continue
                outcomes.append(FilterOutcome(entity, True, None, excuse))
                continue
            outcomes.append(FilterOutcome(entity, True, None, None))
    return tuple(outcomes)


def _scores() -> CandidateScores:
    """Scores for a recorded candidate.

    The legacy importer retains no per-entity confidence — its own ``_caveats``
    say concepts/models/methods keep "name+aliases only". ``CandidateScores``
    requires ``extraction_confidence`` and ``source_reliability``, so a value must
    be supplied; these are placeholders and are never read by any metric this
    runner computes. Calibration, which is the one metric that *would* read them,
    is reported as an honest null for exactly this reason rather than being
    computed off a constant (see :mod:`.metrics`).
    """
    return CandidateScores(extraction_confidence=0.5, source_reliability=0.5)


def build_candidates(
    arm_id: str,
    papers: Sequence[ArmPaper],
    outcomes: Sequence[FilterOutcome],
    *,
    graph_id: str = "agentic-kg",
    ontology_version: str = "ground-truth-chain-v1",
) -> tuple[tuple[Candidate, ...], dict[str, Evidence]]:
    """Turn kept arm entities and citation edges into ``Candidate`` objects.

    The returned evidence store holds one ``Evidence`` per candidate that carries
    a supporting quote. The recorded legacy arm carries none for scored entities,
    so its store is empty — which is a *finding*, not a gap in this adapter, and
    it is the reason evidence resolvability comes back insufficient (no refs to
    resolve) while span coverage comes back a measured 0.0 (gold demanded 49
    spans and the arm supplied none).
    """
    candidates: list[Candidate] = []
    evidence: dict[str, Evidence] = {}
    provenance = Provenance(source="ground-truth-chain", actor=arm_id)

    for i, outcome in enumerate(outcomes):
        if not outcome.kept:
            continue
        entity = outcome.entity
        # An unresolved surface keys on itself, scoped to the paper, so it lands
        # outside every gold key and grades as the false positive it is.
        canonical = outcome.resolved_canonical or entity.name
        refs: tuple[EvidenceRef, ...] = ()
        if entity.quoted_text:
            evidence_id = f"ev_{arm_id}_{i}"
            evidence[evidence_id] = Evidence(
                evidence_id=evidence_id,
                source_type=_SOURCE_TYPE,
                source_locator=source_locator(entity.slug),
                observed_at=RECORDED_AT,
                availability=EvidenceAvailability.PRESENT,
                content=entity.quoted_text,
                provenance=provenance,
            )
            refs = (
                EvidenceRef(
                    evidence_id=evidence_id, relationship=EvidenceRelationship.SUPPORTS
                ),
            )
        candidates.append(
            EntityCandidate(
                graph_id=graph_id,
                producer=arm_id,
                producer_run_id=f"{arm_id}-recorded",
                ontology_version=ontology_version,
                source_coordinates=SourceCoordinates(
                    source_type=_SOURCE_TYPE, locator=source_locator(entity.slug)
                ),
                semantic_key=f"{entity.slug}/{canonical}",
                entity_type=BUCKET_TO_ENTITY_TYPE[entity.bucket],
                aliases=(
                    EntityRef(
                        entity_type=BUCKET_TO_ENTITY_TYPE[entity.bucket],
                        namespace="ground-truth-chain",
                        key=f"{entity.slug}/{canonical}",
                    ),
                ),
                display_name=entity.name,
                evidence_refs=refs,
                scores=_scores(),
            )
        )

    for paper in papers:
        for edge in paper.citations:
            candidates.append(_citation_candidate(arm_id, edge, graph_id, ontology_version))

    return tuple(candidates), evidence


def _citation_candidate(
    arm_id: str, edge: CitationEdge, graph_id: str, ontology_version: str
) -> RelationCandidate:
    return RelationCandidate(
        graph_id=graph_id,
        producer=arm_id,
        producer_run_id=f"{arm_id}-recorded",
        ontology_version=ontology_version,
        source_coordinates=SourceCoordinates(
            source_type=_SOURCE_TYPE, locator=source_locator(edge.citing_slug)
        ),
        semantic_key=f"{edge.citing_slug}->{edge.cited_slug}",
        relation_type=CITES,
        subject=EntityRef(
            entity_type=PAPER_ENTITY_TYPE,
            namespace=PAPER_NAMESPACE,
            key=normalize_doi(PAPER_DOIS[edge.citing_slug]),
        ),
        object=EntityRef(
            entity_type=PAPER_ENTITY_TYPE,
            namespace=PAPER_NAMESPACE,
            key=normalize_doi(PAPER_DOIS[edge.cited_slug]),
        ),
        scores=_scores(),
    )


__all__ = [
    "CITES",
    "PAPER_ENTITY_TYPE",
    "PAPER_NAMESPACE",
    "RECORDED_AT",
    "FilterOutcome",
    "SurfaceIndex",
    "build_candidates",
    "build_goldset",
    "build_surface_index",
    "filter_candidates",
    "paper_ref",
    "source_locator",
]
