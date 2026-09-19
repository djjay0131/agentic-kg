"""Corpus facts, asserted against the files rather than taken on trust.

Every number in the evaluation report is derived from this corpus, and several
of the claims circulating about it were second-hand. These tests pin the ones
that matter, each with the consequence of being wrong written next to it, so a
fixture change that moves a count fails here instead of silently changing what
the report means.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.evaluation.corpus import (
    NAMED_RESOURCE_BUCKET,
    PAPER_DOIS,
    RECONCILED_SLUGS,
    check_paper_dois,
    check_reconciled_slugs,
    load_importer_output,
    load_reconciled_paper,
    load_reconciled_papers,
    normalize_doi,
    normalize_surface,
)


def test_exactly_two_papers_have_reconciled_gold(chain_root: Path, papers) -> None:
    """Two, not three. The earlier 'three' was a miscount of ``claude/``.

    ``claude/`` holds three files and ``human/`` four, but both are independent
    reviews. Scoring against a review would grade the importer against one
    opinion that reconciliation exists to adjudicate.
    """
    assert len(papers) == 2
    assert {p.slug for p in papers} == RECONCILED_SLUGS == {"cskg", "cskg2"}
    assert len(list((chain_root / "reconciled").glob("*.gold.yml"))) == 2
    assert len(list((chain_root / "claude").glob("*.gold.yml"))) == 3
    assert len(list((chain_root / "human").glob("*.gold.yml"))) == 4
    check_reconciled_slugs(papers)


def test_fact_completion_is_reviewed_but_not_reconciled(chain_root: Path) -> None:
    """Both reviews exist; no reconciliation. It is therefore not scoreable."""
    assert (chain_root / "human" / "paper_fact_completion.gold.yml").is_file()
    assert (chain_root / "claude" / "paper_fact_completion.gold.yml").is_file()
    assert not (chain_root / "reconciled" / "paper_fact_completion.gold.yml").is_file()
    assert "fact_completion" not in RECONCILED_SLUGS


@pytest.mark.parametrize("reviewer_dir", ["human", "claude"])
def test_loader_refuses_unreconciled_reviews(chain_root: Path, reviewer_dir: str) -> None:
    """The loader must reject a review file even if someone points it at one."""
    path = chain_root / reviewer_dir / "paper_cskg.gold.yml"
    with pytest.raises(ValueError, match="not 'reconciled'"):
        load_reconciled_paper(path)


def test_scoreable_entity_count_is_fiftythree(papers) -> None:
    """21 (cskg) + 32 (cskg2) = 53 scored entities across four categories."""
    by_slug = {p.slug: p for p in papers}
    assert len(by_slug["cskg"].entities) == 21
    assert len(by_slug["cskg2"].entities) == 32
    assert sum(len(p.entities) for p in papers) == 53

    counts = {
        (p.slug, b): len(p.entities_in(b))
        for p in papers
        for b in ("topics", "concepts", "models", "methods")
    }
    assert counts[("cskg", "topics")] == 2
    assert counts[("cskg", "concepts")] == 6
    assert counts[("cskg", "models")] == 3
    assert counts[("cskg", "methods")] == 10
    assert counts[("cskg2", "topics")] == 2
    assert counts[("cskg2", "concepts")] == 8
    assert counts[("cskg2", "models")] == 9
    assert counts[("cskg2", "methods")] == 13


def test_only_two_citation_edges_are_reconciled(papers) -> None:
    """The README documents a 10-edge chain; only 2 edges have reconciled gold.

    And only one of those two — ``cskg2 -> cskg`` — has a reconciled answer key
    at *both* ends. Any claim about citation recall "across the chain" rests on
    that one edge, not on ten.
    """
    edges = [e for p in papers for e in p.citations]
    assert len(edges) == 2
    assert {(e.citing_slug, e.cited_slug) for e in edges} == {
        ("cskg2", "cskg"),
        ("cskg2", "fact_completion"),
    }
    assert sum(1 for e in edges if e.cited_has_reconciled_gold) == 1


def test_acceptable_extras_use_both_yaml_shapes(papers) -> None:
    """61 extra names total; 41 of them only reachable via the ``names:`` shape.

    A loader that reads only ``{name, why}`` sees 20 and readmits 41 into the
    precision denominator as false positives — which is the exact harm the
    extras mechanism exists to prevent.
    """
    extras = [x for p in papers for x in p.extras]
    assert len(extras) == 61
    named_resources = [x for x in extras if x.source_bucket == NAMED_RESOURCE_BUCKET]
    assert len(named_resources) == 41
    assert len(extras) - len(named_resources) == 20
    # Every extra carries a reason; SCHEMA.md requires it.
    assert all(x.why for x in extras)


def test_named_resources_apply_to_every_category(papers) -> None:
    """Ad-hoc buckets have no entity type, so their extras are category-agnostic."""
    from agentic_kg.migration.evaluation.corpus import ANY_BUCKET

    named = [x for p in papers for x in p.extras if x.source_bucket == NAMED_RESOURCE_BUCKET]
    assert named and all(x.bucket == ANY_BUCKET for x in named)


def test_every_scored_concept_model_method_has_a_grounding_quote(papers) -> None:
    """49 of 53 gold entities carry a quote; the 4 topics do not, by schema."""
    quoted = [
        e
        for p in papers
        for e in p.entities
        if e.bucket in {"concepts", "models", "methods"}
    ]
    assert len(quoted) == 49
    assert all(e.quoted_text for e in quoted)
    topics = [e for p in papers for e in p.entities if e.bucket == "topics"]
    assert len(topics) == 4


def test_importer_output_has_four_failed_papers(importer_output_dir: Path) -> None:
    """4 of 8 recorded importer files never produced an extraction.

    Treating those four as zero-recall extractions would blame the extractor for
    a PDF that was never fetched. ``succeeded`` is the discriminator that keeps
    them out of the graded numbers and in the corpus-coverage line instead.
    """
    arm_papers = load_importer_output(importer_output_dir)
    assert len(arm_papers) == 8
    failed = [p.slug for p in arm_papers if not p.succeeded]
    assert sorted(failed) == [
        "fact_completion",
        "hypothesis_generation",
        "kg_construction_survey",
        "kg_validation_hitl",
    ]
    assert len(failed) == 4
    # Both reconciled papers DID extract, so the graded scope has zero failures.
    graded = [p for p in arm_papers if p.slug in RECONCILED_SLUGS]
    assert all(p.succeeded for p in graded)


def test_paper_doi_table_agrees_with_every_other_source(
    chain_root: Path, importer_output_dir: Path
) -> None:
    """A mistyped DOI turns a correct citation into an FP and an FN at once."""
    assert check_paper_dois(chain_root, importer_output_dir) == []
    assert len(PAPER_DOIS) == 8


def test_doi_case_folding_is_load_bearing(importer_output_dir: Path) -> None:
    """The importer emitted ``ACCESS`` where everything else says ``access``.

    Without folding, the one citation edge the legacy importer got right grades
    is silently DROPPED — it never becomes a candidate, so it is a false
    negative only (tp 0, fp 0, fn 2). A silent drop is the harder failure to
    notice: nothing looks anomalous, recall is just lower, and the natural
    reading is that the importer missed a citation it actually found.
    """
    assert normalize_doi("10.1109/ACCESS.2022.3220241") == PAPER_DOIS["fact_completion"]
    arm_papers = {p.slug: p for p in load_importer_output(importer_output_dir)}
    cited = {e.cited_slug for e in arm_papers["cskg2"].citations}
    assert cited == {"fact_completion"}


def test_surface_normalization_is_case_and_whitespace_only(chain_root: Path) -> None:
    """Enough to bridge Title Case to lowercase gold; no tunable similarity."""
    assert normalize_surface("Information Extraction Pipeline") == (
        "information extraction pipeline"
    )
    assert normalize_surface("  entity   linking ") == "entity linking"
    # Not a stemmer: singular and plural stay distinct, as SCHEMA.md requires
    # (aliases must list plural variants explicitly).
    assert normalize_surface("knowledge graph") != normalize_surface("knowledge graphs")


def test_seventeen_canonicals_collide_across_the_two_papers(papers) -> None:
    """Which is why a gold key must be paper-scoped.

    An unscoped key would make ``GoldSet`` reject the corpus outright, and if it
    did not, `cskg2` finding "reification" would silently discharge `cskg`'s
    obligation to find it.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for paper in papers:
        for entity in paper.entities:
            seen.setdefault((entity.bucket, entity.canonical), set()).add(paper.slug)
    collisions = {k for k, v in seen.items() if len(v) > 1}
    assert len(collisions) == 17
    keys = [e.scoped_key() for p in papers for e in p.entities]
    assert len(keys) == len(set(keys)) == 53


def test_reconciled_files_record_their_disagreements(papers) -> None:
    """37 adjudicated disagreements — the evidence that reconciliation happened."""
    by_slug = {p.slug: p for p in papers}
    assert by_slug["cskg"].disagreement_count == 20
    assert by_slug["cskg2"].disagreement_count == 17


def test_load_is_deterministic(chain_root: Path) -> None:
    assert load_reconciled_papers(chain_root) == load_reconciled_papers(chain_root)
