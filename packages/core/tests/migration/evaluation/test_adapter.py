"""The adapter's three load-bearing translations, plus their failure modes.

Mutation tests live here: §9.0's fifth obligation is that a criterion must be
able to fail for the reason it names, so each claim about the adapter is paired
with a deliberately-wrong input that must move the number.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.evaluation.adapter import (
    build_candidates,
    build_goldset,
    build_surface_index,
    filter_candidates,
    paper_ref,
)
from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper, CitationEdge
from kg_eval.goldset import GoldSet
from kg_eval.matching import match_entities, match_relations


def _arm(*entities: ArmEntity, slug: str = "cskg", citations=()) -> tuple[ArmPaper, ...]:
    return (ArmPaper(slug=slug, status="extracted", entities=entities, citations=citations),)


def _graded(papers, index, **kw):
    outcomes = filter_candidates(papers, index, **kw)
    candidates, evidence = build_candidates("t", papers, outcomes)
    return outcomes, candidates, evidence


# --------------------------------------------------------------------------
# Gold set construction
# --------------------------------------------------------------------------


def test_goldset_has_53_entities_and_2_relations(papers) -> None:
    gold = build_goldset(papers, gold_set_id="t")
    assert len(gold.entities) == 53
    assert len(gold.relations) == 2
    assert gold.attempted == 2
    # 49 of 53 carry an evidence span (the 4 topics have no quoted_text).
    assert sum(1 for e in gold.entities if e.evidence is not None) == 49


def test_goldset_construction_would_reject_unscoped_keys(papers) -> None:
    """The duplicate-key guard is real, and paper scoping is what satisfies it.

    Rebuilding the gold set with the collision restored must raise — otherwise
    the paper-scoping in :meth:`ScoredEntity.scoped_key` is decoration and 17
    recall obligations are silently merged.
    """
    gold = build_goldset(papers, gold_set_id="t")
    unscoped = tuple(
        e.model_copy(update={"semantic_key": e.semantic_key.split("/", 1)[1]})
        for e in gold.entities
    )
    with pytest.raises(ValueError, match="duplicate entity gold key"):
        GoldSet(gold_set_id="t", entities=unscoped)


def test_goldset_content_hash_is_stable(papers) -> None:
    a = build_goldset(papers, gold_set_id="a", description="one")
    b = build_goldset(papers, gold_set_id="b", description="two")
    assert a.content_hash == b.content_hash  # id/description excluded by design


def test_bucket_slice_narrows_the_gold_set(papers) -> None:
    topics = build_goldset(papers, gold_set_id="t", buckets=("topics",), include_relations=False)
    assert len(topics.entities) == 4
    assert topics.relations == ()
    assert {e.entity_type for e in topics.entities} == {"Topic"}


def test_citation_endpoints_are_folded_dois(papers) -> None:
    gold = build_goldset(papers, gold_set_id="t")
    subjects = {r.subject for r in gold.relations}
    assert subjects == {"Paper:doi:10.1038/s41597-025-05200-8"}
    assert paper_ref("fact_completion") == "Paper:doi:10.1109/access.2022.3220241"


def test_unknown_slug_endpoint_raises_rather_than_guessing() -> None:
    with pytest.raises(KeyError, match="no DOI known"):
        paper_ref("a_paper_that_does_not_exist")


# --------------------------------------------------------------------------
# Alias resolution
# --------------------------------------------------------------------------


def test_alias_is_resolved_candidate_side_to_the_canonical_key(papers, surface_index) -> None:
    """An alias must score as the canonical, with the gold key left literal."""
    gold = build_goldset(papers, gold_set_id="t", buckets=("methods",), include_relations=False)
    # "entity resolution" is an acceptable alias of the gold canonical
    # "entity merging" in cskg.
    _, candidates, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="methods", name="entity resolution")),
        surface_index,
    )
    match = match_entities(candidates, gold)
    assert match.tp == 1 and match.fp == 0
    assert candidates[0].semantic_key == "cskg/entity merging"
    # The gold key stays literal — the rewrite happened on the candidate side.
    assert any(g.semantic_key == "cskg/entity merging" for g in gold.entities)

    # Mutation: an unrelated surface must NOT resolve.
    _, wrong, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="methods", name="entity resolution service")),
        surface_index,
    )
    assert match_entities(wrong, gold).tp == 0


def test_alias_resolution_is_case_and_whitespace_insensitive(papers, surface_index) -> None:
    """The legacy importer emits Title Case; gold canonicals are lowercase."""
    gold = build_goldset(papers, gold_set_id="t", buckets=("methods",), include_relations=False)
    _, candidates, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="methods", name="  Entity   Merging  ")),
        surface_index,
    )
    assert match_entities(candidates, gold).tp == 1


def test_alias_resolution_is_paper_scoped(papers, surface_index) -> None:
    """An alias valid in cskg must not discharge cskg2's obligation.

    17 canonicals are gold in both papers; without scoping, one arm finding a
    term in one paper would satisfy both.
    """
    gold = build_goldset(papers, gold_set_id="t", buckets=("concepts",), include_relations=False)
    _, both, _ = _graded(
        (
            ArmPaper("cskg", "extracted", (ArmEntity("cskg", "concepts", "reification"),)),
            ArmPaper("cskg2", "extracted", (ArmEntity("cskg2", "concepts", "reification"),)),
        ),
        surface_index,
    )
    assert match_entities(both, gold).tp == 2

    _, one, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="concepts", name="reification")), surface_index
    )
    assert match_entities(one, gold).tp == 1


def test_alias_resolution_is_bucket_scoped(papers, surface_index) -> None:
    """A method surface emitted as a concept is a false positive, not a hit."""
    gold = build_goldset(papers, gold_set_id="t", buckets=("concepts",), include_relations=False)
    _, candidates, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="concepts", name="entity linking")), surface_index
    )
    m = match_entities(candidates, gold)
    assert m.tp == 0 and m.fp == 1


def test_surface_index_rejects_an_ambiguous_alias(papers) -> None:
    """A future alias collision must fail the load, not silently move credit."""
    from agentic_kg.migration.evaluation.corpus import ScoredEntity

    poisoned = papers[0].__class__(
        slug="x",
        doi="10.0/x",
        title="x",
        entities=(
            ScoredEntity("x", "concepts", "alpha", aliases=("shared",)),
            ScoredEntity("x", "concepts", "beta", aliases=("shared",)),
        ),
        citations=(),
        extras=(),
        disagreement_count=0,
    )
    with pytest.raises(ValueError, match="ambiguous surface"):
        build_surface_index((poisoned,))


def test_surface_index_rejects_an_alias_that_is_another_canonical(papers) -> None:
    from agentic_kg.migration.evaluation.corpus import ScoredEntity

    poisoned = papers[0].__class__(
        slug="x",
        doi="10.0/x",
        title="x",
        entities=(
            ScoredEntity("x", "concepts", "alpha", aliases=("beta",)),
            ScoredEntity("x", "concepts", "beta"),
        ),
        citations=(),
        extras=(),
        disagreement_count=0,
    )
    with pytest.raises(ValueError, match="also the canonical"):
        build_surface_index((poisoned,))


def test_the_real_corpus_has_no_alias_ambiguity(papers, surface_index) -> None:
    """Building the index at all is the assertion — it raises on ambiguity.

    Beyond that, every gold surface must resolve to its own canonical: 53
    canonicals plus 140 aliases, of which a couple are case-variants of a
    canonical and collapse into it, leaving 191 distinct lookup keys.
    """
    assert len(surface_index.alias_to_canonical) == 191
    for paper in papers:
        for entity in paper.entities:
            for surface in entity.surfaces():
                assert (
                    surface_index.resolve(entity.slug, entity.bucket, surface)
                    == entity.canonical
                ), (entity.slug, surface)


# --------------------------------------------------------------------------
# acceptable_extras
# --------------------------------------------------------------------------


def test_extras_leave_the_precision_denominator(papers, surface_index) -> None:
    """An excusable emission must be dropped, not counted as a false positive."""
    gold = build_goldset(papers, gold_set_id="t", buckets=("topics",), include_relations=False)
    extra = next(
        x for p in papers for x in p.extras if x.bucket == "topics" and x.slug == "cskg"
    )
    arm = _arm(ArmEntity(slug="cskg", bucket="topics", name=extra.name))

    outcomes, kept, _ = _graded(arm, surface_index)
    assert [o.kept for o in outcomes] == [False]
    assert match_entities(kept, gold).fp == 0

    # Mutation: with the filter off, the very same emission becomes an FP.
    outcomes_off, unfiltered, _ = _graded(arm, surface_index, exclude_extras=False)
    assert [o.kept for o in outcomes_off] == [True]
    assert match_entities(unfiltered, gold).fp == 1


def test_named_resource_extras_are_excused_in_any_category(papers, surface_index) -> None:
    """"CS-KG" is inventoried under an ad-hoc bucket with no entity type.

    It must be excused wherever the importer puts it, because nothing in the
    fixture says which category that will be — and guessing would make the
    filter depend on the decision it is waiting on.
    """
    for bucket in ("concepts", "models", "methods", "topics"):
        outcomes = filter_candidates(
            _arm(ArmEntity(slug="cskg", bucket=bucket, name="CS-KG")), surface_index
        )
        assert [o.kept for o in outcomes] == [False], bucket
        assert outcomes[0].excused_by is not None
        assert outcomes[0].excused_by.source_bucket == "named_resources"

    # Mutation: turn the named-resource exclusion off and it is graded again.
    outcomes = filter_candidates(
        _arm(ArmEntity(slug="cskg", bucket="concepts", name="CS-KG")),
        surface_index,
        exclude_named_resources=False,
    )
    assert [o.kept for o in outcomes] == [True]


def test_the_plural_names_shape_is_what_excuses_most_of_them(papers, surface_index) -> None:
    """41 of the 61 extras are only reachable through ``names: [...]``.

    Spot-checked on three real entries drawn from that shape; a loader reading
    only ``{name, why}`` keeps every one of them in the denominator.
    """
    plural = [
        x
        for p in papers
        for x in p.extras
        if x.source_bucket == "named_resources" and x.slug == "cskg"
    ]
    assert len(plural) == 14
    for extra in plural[:3]:
        outcomes = filter_candidates(
            _arm(ArmEntity(slug="cskg", bucket="concepts", name=extra.name)), surface_index
        )
        assert [o.kept for o in outcomes] == [False], extra.name


def test_no_extra_currently_shadows_a_gold_surface(papers) -> None:
    """Today the two sets are disjoint — recorded so the next test's scope is clear.

    ``SCHEMA.md`` explicitly permits an extra to be a generic surface form of a
    scored entry, so the sets *may* overlap; in the two reconciled files they do
    not. The ordering rule below is therefore a guard for gold files not yet
    written, and cannot be exercised on the real corpus.
    """
    from agentic_kg.migration.evaluation.corpus import ANY_BUCKET, normalize_surface

    gold_surfaces = {
        (p.slug, e.bucket, normalize_surface(s))
        for p in papers
        for e in p.entities
        for s in e.surfaces()
    }
    shadowed = [
        (x.slug, x.name)
        for p in papers
        for x in p.extras
        for (slug, bucket, surface) in gold_surfaces
        if slug == x.slug
        and surface == normalize_surface(x.name)
        and x.bucket in (bucket, ANY_BUCKET)
    ]
    assert shadowed == []


def test_gold_always_beats_an_extras_excuse(papers) -> None:
    """A surface that is both gold and excusable must stay graded.

    Excusing it would delete a true positive: the recall obligation outranks the
    may-emit. Constructed rather than drawn from the corpus, because no overlap
    exists there yet (see the test above) — and the rule has to hold before the
    first file that does overlap is written, not after.
    """
    from agentic_kg.migration.evaluation.corpus import AcceptableExtra, ScoredEntity

    paper = papers[0].__class__(
        slug="x",
        doi="10.0/x",
        title="x",
        entities=(ScoredEntity("x", "methods", "entity filtering"),),
        citations=(),
        extras=(
            AcceptableExtra("x", "methods", "entity filtering", "generic form", "methods"),
        ),
        disagreement_count=0,
    )
    index = build_surface_index((paper,))
    gold = build_goldset(
        (paper,), gold_set_id="t", buckets=("methods",), include_relations=False
    )
    outcomes, candidates, _ = _graded(
        _arm(ArmEntity(slug="x", bucket="methods", name="Entity Filtering")), index
    )
    assert [o.kept for o in outcomes] == [True]
    assert outcomes[0].resolved_canonical == "entity filtering"
    assert match_entities(candidates, gold).tp == 1


def test_an_unknown_surface_is_a_false_positive(papers, surface_index) -> None:
    """Not in gold, not excused: it counts against precision, as it should."""
    gold = build_goldset(papers, gold_set_id="t", buckets=("concepts",), include_relations=False)
    _, candidates, _ = _graded(
        _arm(ArmEntity(slug="cskg", bucket="concepts", name="a thing nobody labelled")),
        surface_index,
    )
    m = match_entities(candidates, gold)
    assert m.tp == 0 and m.fp == 1


# --------------------------------------------------------------------------
# Relations and evidence
# --------------------------------------------------------------------------


def test_citation_candidates_key_against_gold_relations(papers, surface_index) -> None:
    gold = build_goldset(papers, gold_set_id="t")
    arm = (
        ArmPaper(
            slug="cskg2",
            status="extracted",
            citations=(CitationEdge("cskg2", "cskg"), CitationEdge("cskg2", "fact_completion")),
        ),
    )
    _, candidates, _ = _graded(arm, surface_index)
    m = match_relations(candidates, gold)
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)

    # Mutation: an edge that is not in gold is a false positive.
    wrong = (ArmPaper("cskg2", "extracted", citations=(CitationEdge("cskg2", "empire"),)),)
    _, wrong_candidates, _ = _graded(wrong, surface_index)
    m2 = match_relations(wrong_candidates, gold)
    assert (m2.tp, m2.fp, m2.fn) == (0, 1, 2)


def test_evidence_is_only_built_where_a_quote_exists(papers, surface_index) -> None:
    """The legacy arm keeps no quotes for scored entities, so its store is empty."""
    _, _, no_quote = _graded(
        _arm(ArmEntity(slug="cskg", bucket="methods", name="entity linking")), surface_index
    )
    assert no_quote == {}

    _, candidates, store = _graded(
        _arm(
            ArmEntity(
                slug="cskg",
                bucket="methods",
                name="entity linking",
                quoted_text="we perform entity linking over the extracted triples",
            )
        ),
        surface_index,
    )
    assert len(store) == 1
    assert candidates[0].evidence_refs
    assert next(iter(store.values())).source_locator == "paper:cskg"


def test_candidate_construction_is_deterministic(papers, surface_index) -> None:
    """No wall-clock, no random ids that vary between runs of the same fixtures."""
    arm = _arm(
        ArmEntity(slug="cskg", bucket="methods", name="entity linking", quoted_text="q1234567890")
    )
    _, first, store_a = _graded(arm, surface_index)
    _, second, store_b = _graded(arm, surface_index)
    assert [c.semantic_key for c in first] == [c.semantic_key for c in second]
    assert list(store_a) == list(store_b)
    assert {e.observed_at for e in store_a.values()} == {e.observed_at for e in store_b.values()}
