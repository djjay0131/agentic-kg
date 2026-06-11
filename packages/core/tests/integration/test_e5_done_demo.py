"""E-5 AC-9 — testcontainers integration test (the verify gate).

Exercises the full citation-graph round trip:

1. Create 3 Papers (A, B, C) in the KG.
2. Run ``populate_citations`` against A with a mocked Semantic Scholar
   client whose references endpoint returns [B (in KG), D (not in KG,
   has DOI), E (no DOI)].
3. Assert: edges (A→B), (A→D), no edge to E. Stub for D created.
   ``A.reference_count == 2``, ``B.citation_count == 1``,
   ``D.citation_count == 1``.
4. Promote D via ``_promote_paper_stub`` with full metadata.
5. Assert: same node id, ``D.is_stub == False``, the (A→D) edge survives,
   ``D.citation_count == 1`` is preserved.

Plus AC-10 stub-promotion sentinel: re-calling
``create_or_promote_paper_stub`` against an existing stub does NOT
mutate it.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.knowledge_graph.citation_graph import populate_citations
from agentic_kg.knowledge_graph.models import Paper

pytestmark = pytest.mark.integration


def _test_doi(label: str) -> str:
    return f"10.TEST_{label}/{uuid.uuid4().hex[:8]}"


def _make_paper(repo, doi: str, title: str = "A real paper title") -> Paper:
    paper = Paper(doi=doi, title=title, year=2024, authors=[])
    repo.create_paper(paper)
    return paper


@pytest.fixture
def loaded_repo(neo4j_repository):
    """Repo with test isolation: drop Paper nodes after the test so the
    bundled seed-style fixtures from other E-* suites don't leak."""
    yield neo4j_repository
    with neo4j_repository.session() as session:
        session.run(
            "MATCH (p:Paper) WHERE p.doi STARTS WITH '10.TEST_' "
            "DETACH DELETE p"
        )


@pytest.fixture
def mock_s2(test_dois):
    """Mocked Semantic Scholar client with deterministic references payload."""
    doi_a, doi_b, doi_d_known, doi_e_known = test_dois

    client = MagicMock()
    client.get_paper_by_doi = AsyncMock(return_value={"paperId": f"S2_{doi_a}"})
    client.get_paper_references = AsyncMock(
        return_value={
            "data": [
                # B — already in KG (has DOI).
                {"citedPaper": {
                    "externalIds": {"DOI": doi_b},
                    "title": "Paper B title",
                    "year": 2020,
                }},
                # D — not in KG; has DOI → stub created.
                {"citedPaper": {
                    "externalIds": {"DOI": doi_d_known},
                    "title": "Paper D title (stub)",
                    "year": 2019,
                }},
                # E — not in KG; no DOI → skipped.
                {"citedPaper": {
                    "externalIds": {},
                    "title": "Paper E title (skipped)",
                    "year": 2018,
                }},
            ],
        },
    )
    return client


@pytest.fixture
def test_dois():
    """Fixed DOIs used across the round trip."""
    return (
        _test_doi("A"),
        _test_doi("B"),
        _test_doi("D"),
        _test_doi("E"),
    )


class TestRoundTripDoneDemo:
    @pytest.mark.asyncio
    async def test_full_round_trip(self, loaded_repo, mock_s2, test_dois):
        """AC-9: populate_citations + stub promotion preserves edges."""
        doi_a, doi_b, doi_d, doi_e = test_dois

        # Step 1: A and B exist in KG.
        _make_paper(loaded_repo, doi_a, title="Paper A title")
        _make_paper(loaded_repo, doi_b, title="Paper B title")

        # Step 2: populate citations for A.
        result = await populate_citations(
            repo=loaded_repo,
            s2_client=mock_s2,
            paper_doi=doi_a,
            paper_s2_id=f"S2_{doi_a}",
        )

        # Counts:
        # - B existed already, so create_or_promote returns created=False
        #   → stubs_created stays at 0 for B.
        # - D was new → stubs_created ticks to 1.
        # - E had no DOI → skipped_no_doi ticks to 1.
        # - Two CITES edges created: (A→B) and (A→D).
        assert result.stubs_created == 1
        assert result.edges_created == 2
        assert result.skipped_no_doi == 1
        assert result.errors == []

        # Step 3: verify the graph state.
        a = loaded_repo.get_paper(doi_a)
        b = loaded_repo.get_paper(doi_b)
        d_stub = loaded_repo.get_paper(doi_d)

        assert a.reference_count == 2
        assert b.citation_count == 1
        assert d_stub.is_stub is True
        assert d_stub.citation_count == 1
        assert d_stub.title == "Paper D title (stub)"
        assert d_stub.year == 2019

        # No node for E exists.
        from agentic_kg.knowledge_graph.repository import NotFoundError
        with pytest.raises(NotFoundError):
            loaded_repo.get_paper(doi_e)

        # Edges as expected.
        a_refs = loaded_repo.get_references(doi_a, limit=10)
        ref_dois = {r["doi"] for r in a_refs}
        assert ref_dois == {doi_b, doi_d}

        # Step 4: promote D to a full Paper via the importer-equivalent path.
        full_d = Paper(
            doi=doi_d,
            title="Paper D title (full)",
            year=2019,
            authors=["Author One", "Author Two"],
            abstract="Now we have an abstract.",
        )
        promoted = loaded_repo._promote_paper_stub(doi_d, full_d)

        # Step 5: promotion preserves edges + citation_count + same node id.
        assert promoted.is_stub is False
        assert promoted.title == "Paper D title (full)"
        assert promoted.authors == ["Author One", "Author Two"]
        assert promoted.citation_count == 1  # PRESERVED through promotion

        # (A→D) edge survives — get_citing_papers(D) still returns A.
        citing_d = loaded_repo.get_citing_papers(doi_d, limit=10)
        citing_dois = {r["doi"] for r in citing_d}
        assert doi_a in citing_dois


class TestStubPromotionSentinel:
    """AC-10: ``create_or_promote_paper_stub`` is a STUB CREATOR, not a
    title-updater. Calling it on an existing stub returns the stub
    unchanged. The promotion (overwriting properties) happens via
    ``_promote_paper_stub`` called from the importer-equivalent path.
    """

    def test_repeated_stub_create_does_not_overwrite_title(self, loaded_repo):
        doi = _test_doi("Sentinel")

        first, created_first = loaded_repo.create_or_promote_paper_stub(
            doi=doi, title="Original title",
        )
        assert created_first is True

        second, created_second = loaded_repo.create_or_promote_paper_stub(
            doi=doi, title="Attempted overwrite",
        )
        assert created_second is False
        assert second.doi == first.doi
        assert second.title == "Original title"  # NOT overwritten
        assert second.is_stub is True
