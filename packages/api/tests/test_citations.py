"""Tests for E-5 citation graph API endpoints.

Mocks the repository via the shared ``client`` + ``mock_repo`` fixtures.
"""

from __future__ import annotations

from agentic_kg.knowledge_graph.models import Paper
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_paper(**overrides) -> Paper:
    defaults = {
        "doi": "10.1/p1",
        "title": "Source paper title",
        "year": 2024,
        "is_stub": False,
        "citation_count": 0,
        "reference_count": 0,
    }
    defaults.update(overrides)
    return Paper(**defaults)


class TestGetPaperReferences:
    def test_returns_reference_rows(self, client, mock_repo):
        mock_repo.get_paper.return_value = _make_paper(doi="10.1/source")
        mock_repo.get_references.return_value = [
            {"doi": "10.1/a", "title": "Ref A", "year": 2020,
             "is_stub": False, "citation_count": 5},
            {"doi": "10.1/b", "title": "Ref B", "year": None,
             "is_stub": True, "citation_count": 1},
        ]

        response = client.get("/api/papers/10.1%2Fsource/references")
        assert response.status_code == 200
        data = response.json()
        assert data["paper_doi"] == "10.1/source"
        assert data["total"] == 2
        assert data["references"][0]["doi"] == "10.1/a"
        assert data["references"][1]["is_stub"] is True

    def test_404_when_paper_not_found(self, client, mock_repo):
        mock_repo.get_paper.side_effect = NotFoundError("nope")
        response = client.get("/api/papers/10.1%2Fmissing/references")
        assert response.status_code == 404


class TestGetPaperCitations:
    def test_returns_citation_rows(self, client, mock_repo):
        mock_repo.get_paper.return_value = _make_paper(doi="10.1/source")
        mock_repo.get_citing_papers.return_value = [
            {"doi": "10.1/c", "title": "Citer C", "year": 2025,
             "is_stub": False, "citation_count": 0},
        ]

        response = client.get("/api/papers/10.1%2Fsource/citations")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["citations"][0]["doi"] == "10.1/c"

    def test_404_when_paper_not_found(self, client, mock_repo):
        mock_repo.get_paper.side_effect = NotFoundError("nope")
        response = client.get("/api/papers/10.1%2Fmissing/citations")
        assert response.status_code == 404


class TestGetPaperCitationCounts:
    def test_returns_counts_and_is_stub(self, client, mock_repo):
        mock_repo.get_paper.return_value = _make_paper(
            doi="10.1/p1", citation_count=12, reference_count=30, is_stub=False,
        )
        response = client.get("/api/papers/10.1%2Fp1/citation-counts")
        assert response.status_code == 200
        data = response.json()
        assert data["citation_count"] == 12
        assert data["reference_count"] == 30
        assert data["is_stub"] is False

    def test_returns_counts_for_stub(self, client, mock_repo):
        mock_repo.get_paper.return_value = _make_paper(
            doi="10.1/stub", title="A stub", is_stub=True, citation_count=3,
        )
        response = client.get("/api/papers/10.1%2Fstub/citation-counts")
        assert response.status_code == 200
        data = response.json()
        assert data["is_stub"] is True
        assert data["citation_count"] == 3

    def test_404_when_paper_not_found(self, client, mock_repo):
        mock_repo.get_paper.side_effect = NotFoundError("nope")
        response = client.get("/api/papers/10.1%2Fmissing/citation-counts")
        assert response.status_code == 404


class TestRowToCitationEntryFallbacks:
    """The _row_to_citation_entry helper tolerates missing fields and
    non-int counts (Neo4j returns None for unset numeric properties)."""

    def test_handles_missing_title(self, client, mock_repo):
        mock_repo.get_paper.return_value = _make_paper(doi="10.1/p1")
        mock_repo.get_references.return_value = [
            {"doi": "10.1/x", "title": None, "year": None,
             "is_stub": False, "citation_count": None},
        ]
        response = client.get("/api/papers/10.1%2Fp1/references")
        assert response.status_code == 200
        data = response.json()
        assert data["references"][0]["title"] == "(untitled)"
        assert data["references"][0]["citation_count"] == 0
