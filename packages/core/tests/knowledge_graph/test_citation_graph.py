"""Integration tests for E-5 Citation Graph repository methods.

Uses ``neo4j_repository`` fixture (testcontainers). All Papers use TEST_
DOIs so the shared cleanup sweep catches them.

Covers:
- TestLinkPaperCitesPaper: link / unlink / idempotent / counters (AC-3)
- TestCreateOrPromotePaperStub: stub creation idempotency (AC-4)
- TestStubPromotion: promotion preserves edges + citation_count (AC-5,
  AC-10 sentinel)
- TestGetReferencesAndCitations: graph traversal
- TestSelfCite: edge case
- TestNotFoundErrors: missing endpoints
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import Paper
from agentic_kg.knowledge_graph.repository import NotFoundError

pytestmark = pytest.mark.integration


def _test_doi(label: str = "") -> str:
    return f"10.TEST_{label}/{uuid.uuid4().hex[:8]}"


def _make_full_paper(repo, doi: str, title: str = "A real paper title") -> Paper:
    paper = Paper(doi=doi, title=title, year=2024, authors=[])
    repo.create_paper(paper)
    return paper


# =============================================================================
# AC-3: link_paper_cites_paper + counters + idempotency
# =============================================================================


class TestLinkPaperCitesPaper:
    def test_link_creates_edge_and_increments_counters(self, neo4j_repository):
        doi_a = _test_doi("A")
        doi_b = _test_doi("B")
        _make_full_paper(neo4j_repository, doi_a)
        _make_full_paper(neo4j_repository, doi_b)

        created = neo4j_repository.link_paper_cites_paper(
            source_doi=doi_a, target_doi=doi_b,
        )
        assert created is True

        a = neo4j_repository.get_paper(doi_a)
        b = neo4j_repository.get_paper(doi_b)
        assert a.reference_count == 1
        assert b.citation_count == 1

    def test_link_idempotent_no_double_increment(self, neo4j_repository):
        doi_a = _test_doi("A")
        doi_b = _test_doi("B")
        _make_full_paper(neo4j_repository, doi_a)
        _make_full_paper(neo4j_repository, doi_b)

        first = neo4j_repository.link_paper_cites_paper(doi_a, doi_b)
        second = neo4j_repository.link_paper_cites_paper(doi_a, doi_b)
        assert first is True
        assert second is False

        a = neo4j_repository.get_paper(doi_a)
        b = neo4j_repository.get_paper(doi_b)
        assert a.reference_count == 1
        assert b.citation_count == 1

    def test_unlink_removes_edge_and_decrements_counters(
        self, neo4j_repository
    ):
        doi_a = _test_doi("A")
        doi_b = _test_doi("B")
        _make_full_paper(neo4j_repository, doi_a)
        _make_full_paper(neo4j_repository, doi_b)
        neo4j_repository.link_paper_cites_paper(doi_a, doi_b)

        removed = neo4j_repository.unlink_paper_cites_paper(doi_a, doi_b)
        assert removed is True

        a = neo4j_repository.get_paper(doi_a)
        b = neo4j_repository.get_paper(doi_b)
        assert a.reference_count == 0
        assert b.citation_count == 0

    def test_unlink_when_no_edge_returns_false(self, neo4j_repository):
        """Adversarial: unlink should tolerate "no edge to remove"."""
        doi_a = _test_doi("A")
        doi_b = _test_doi("B")
        _make_full_paper(neo4j_repository, doi_a)
        _make_full_paper(neo4j_repository, doi_b)

        removed = neo4j_repository.unlink_paper_cites_paper(doi_a, doi_b)
        assert removed is False

    def test_link_missing_source_raises(self, neo4j_repository):
        doi_b = _test_doi("B")
        _make_full_paper(neo4j_repository, doi_b)
        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_cites_paper(
                source_doi="10.TEST_/nonexistent", target_doi=doi_b,
            )

    def test_link_missing_target_raises(self, neo4j_repository):
        doi_a = _test_doi("A")
        _make_full_paper(neo4j_repository, doi_a)
        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_cites_paper(
                source_doi=doi_a, target_doi="10.TEST_/nonexistent",
            )


# =============================================================================
# AC-4: create_or_promote_paper_stub idempotency
# =============================================================================


class TestCreateOrPromotePaperStub:
    def test_creates_stub_when_no_paper_exists(self, neo4j_repository):
        doi = _test_doi("Stub1")
        paper, created = neo4j_repository.create_or_promote_paper_stub(
            doi=doi, title="A stub paper title", year=2020,
        )
        assert created is True
        assert paper.is_stub is True
        assert paper.title == "A stub paper title"
        assert paper.year == 2020

    def test_returns_existing_stub_unchanged(self, neo4j_repository):
        doi = _test_doi("Stub2")
        first, _ = neo4j_repository.create_or_promote_paper_stub(
            doi=doi, title="First title",
        )
        second, created = neo4j_repository.create_or_promote_paper_stub(
            doi=doi, title="Second title (ignored)",
        )
        assert created is False
        # Existing title preserved (AC-10 sentinel — title is NOT overwritten).
        assert second.title == "First title"

    def test_returns_existing_full_paper_unchanged(self, neo4j_repository):
        doi = _test_doi("Full")
        _make_full_paper(neo4j_repository, doi, title="A full paper title")

        existing, created = neo4j_repository.create_or_promote_paper_stub(
            doi=doi, title="ignored",
        )
        assert created is False
        assert existing.is_stub is False
        assert existing.title == "A full paper title"

    def test_year_optional_for_stubs(self, neo4j_repository):
        doi = _test_doi("StubNoYear")
        paper, _ = neo4j_repository.create_or_promote_paper_stub(
            doi=doi, title="No year stub",
        )
        assert paper.year is None
        assert paper.is_stub is True


# =============================================================================
# AC-5: Stub promotion preserves edges + counters
# =============================================================================


class TestStubPromotion:
    def test_promote_preserves_inbound_cites_edges_and_count(
        self, neo4j_repository
    ):
        """A stub with 2 inbound CITES edges retains them and its
        citation_count=2 after promotion."""
        stub_doi = _test_doi("Promo")
        citer_a_doi = _test_doi("CiterA")
        citer_b_doi = _test_doi("CiterB")

        # Stub first.
        neo4j_repository.create_or_promote_paper_stub(
            doi=stub_doi, title="Original stub title",
        )
        _make_full_paper(neo4j_repository, citer_a_doi)
        _make_full_paper(neo4j_repository, citer_b_doi)
        neo4j_repository.link_paper_cites_paper(citer_a_doi, stub_doi)
        neo4j_repository.link_paper_cites_paper(citer_b_doi, stub_doi)

        # Sanity: 2 inbound CITES.
        before = neo4j_repository.get_paper(stub_doi)
        assert before.citation_count == 2
        assert before.is_stub is True

        # Promote.
        full = Paper(
            doi=stub_doi,
            title="A fully ingested paper title",
            year=2023,
            authors=["X", "Y"],
            abstract="Now we have an abstract",
        )
        promoted = neo4j_repository._promote_paper_stub(stub_doi, full)

        # Properties updated.
        assert promoted.is_stub is False
        assert promoted.title == "A fully ingested paper title"
        assert promoted.year == 2023
        assert promoted.authors == ["X", "Y"]

        # Citation count preserved.
        assert promoted.citation_count == 2

        # Both inbound edges still exist.
        citing = neo4j_repository.get_citing_papers(stub_doi, limit=10)
        citing_dois = {p["doi"] for p in citing}
        assert {citer_a_doi, citer_b_doi}.issubset(citing_dois)


# =============================================================================
# Graph traversal
# =============================================================================


class TestGetReferencesAndCitations:
    def test_get_references_returns_out_edges(self, neo4j_repository):
        doi_a = _test_doi("Out1")
        doi_b = _test_doi("Out2")
        doi_c = _test_doi("Out3")
        for d in (doi_a, doi_b, doi_c):
            _make_full_paper(neo4j_repository, d)
        neo4j_repository.link_paper_cites_paper(doi_a, doi_b)
        neo4j_repository.link_paper_cites_paper(doi_a, doi_c)

        refs = neo4j_repository.get_references(doi_a, limit=10)
        ref_dois = {p["doi"] for p in refs}
        assert ref_dois == {doi_b, doi_c}

    def test_get_citing_papers_returns_in_edges(self, neo4j_repository):
        doi_a = _test_doi("In1")
        doi_b = _test_doi("In2")
        doi_c = _test_doi("In3")
        for d in (doi_a, doi_b, doi_c):
            _make_full_paper(neo4j_repository, d)
        neo4j_repository.link_paper_cites_paper(doi_b, doi_a)
        neo4j_repository.link_paper_cites_paper(doi_c, doi_a)

        citing = neo4j_repository.get_citing_papers(doi_a, limit=10)
        citing_dois = {p["doi"] for p in citing}
        assert citing_dois == {doi_b, doi_c}

    def test_count_citations_returns_denormalized_count(self, neo4j_repository):
        doi_a = _test_doi("Cnt1")
        doi_b = _test_doi("Cnt2")
        _make_full_paper(neo4j_repository, doi_a)
        _make_full_paper(neo4j_repository, doi_b)
        neo4j_repository.link_paper_cites_paper(doi_b, doi_a)

        count = neo4j_repository.count_citations(doi_a)
        assert count == 1

    def test_count_citations_missing_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.count_citations("10.TEST_/nonexistent")


# =============================================================================
# Edge case: self-citation
# =============================================================================


class TestSelfCite:
    def test_self_cite_allowed_increments_both_counters(self, neo4j_repository):
        """Spec Edge Case: self-loop CITES edges are allowed. Both
        citation_count and reference_count increment by 1 (on the same node)."""
        doi = _test_doi("Self")
        _make_full_paper(neo4j_repository, doi)

        created = neo4j_repository.link_paper_cites_paper(doi, doi)
        assert created is True

        paper = neo4j_repository.get_paper(doi)
        assert paper.citation_count == 1
        assert paper.reference_count == 1
