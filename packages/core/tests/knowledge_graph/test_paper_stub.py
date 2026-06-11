"""Tests for E-5 Paper entity changes (Unit 1).

Covers:
- The new is_stub / citation_count / reference_count fields.
- Relaxed validation: title.min_length = 2 (was 10); year is Optional.
- to_neo4j_properties includes the new fields.

Pure Pydantic — no Neo4j required.
"""

import pytest
from agentic_kg.knowledge_graph.models import Paper
from pydantic import ValidationError


class TestPaperE5FieldsExist:
    def test_is_stub_default_false(self):
        p = Paper(doi="10.1/test", title="A real title", year=2024)
        assert p.is_stub is False

    def test_citation_count_default_zero(self):
        p = Paper(doi="10.1/test", title="A real title", year=2024)
        assert p.citation_count == 0

    def test_reference_count_default_zero(self):
        p = Paper(doi="10.1/test", title="A real title", year=2024)
        assert p.reference_count == 0

    def test_negative_citation_count_raises(self):
        with pytest.raises(ValidationError):
            Paper(doi="10.1/x", title="title", year=2024, citation_count=-1)

    def test_negative_reference_count_raises(self):
        with pytest.raises(ValidationError):
            Paper(doi="10.1/x", title="title", year=2024, reference_count=-1)


class TestPaperRelaxedValidation:
    def test_title_min_length_dropped_to_two(self):
        """E-5: was 10, now 2 — admits stubs with short titles."""
        p = Paper(doi="10.1/x", title="AB", year=2024)
        assert p.title == "AB"

    def test_title_below_two_still_raises(self):
        with pytest.raises(ValidationError):
            Paper(doi="10.1/x", title="A", year=2024)

    def test_year_optional(self):
        """E-5: year is no longer required — admits stubs without year metadata."""
        p = Paper(doi="10.1/x", title="A stub paper")
        assert p.year is None

    def test_year_when_given_still_validates_range(self):
        with pytest.raises(ValidationError):
            Paper(doi="10.1/x", title="A title", year=1500)


class TestPaperStubShape:
    def test_minimal_stub_construction(self):
        """The realistic stub shape: DOI + title + is_stub. No year."""
        stub = Paper(
            doi="10.18653/v1/N19-1423",
            title="BERT: Pre-training of Deep Bidirectional Transformers",
            is_stub=True,
        )
        assert stub.is_stub is True
        assert stub.year is None
        assert stub.authors == []
        assert stub.abstract is None
        assert stub.citation_count == 0
        assert stub.reference_count == 0


class TestPaperToNeo4jPropertiesIncludesE5Fields:
    def test_full_paper_serializes(self):
        p = Paper(
            doi="10.1/x",
            title="A real paper",
            year=2024,
            authors=["A", "B"],
            citation_count=5,
            reference_count=12,
        )
        props = p.to_neo4j_properties()
        assert props["is_stub"] is False
        assert props["citation_count"] == 5
        assert props["reference_count"] == 12
        # Year is encoded as int.
        assert props["year"] == 2024

    def test_stub_serializes_with_null_year(self):
        stub = Paper(doi="10.1/stub", title="Stub", is_stub=True)
        props = stub.to_neo4j_properties()
        assert props["is_stub"] is True
        # Year should serialize as None (Neo4j stores as null).
        assert props["year"] is None
        assert props["citation_count"] == 0
        assert props["reference_count"] == 0
