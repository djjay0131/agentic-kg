"""
Tests for ResearchConcept model (E-2, Unit 1).

Covers Pydantic validation, aliases handling, serialization, and equality.
"""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentic_kg.knowledge_graph.models import ResearchConcept


# =============================================================================
# Creation & defaults
# =============================================================================


class TestResearchConceptCreation:
    def test_minimal_concept(self):
        concept = ResearchConcept(name="attention mechanism")
        assert concept.name == "attention mechanism"
        assert concept.description is None
        assert concept.aliases == []
        assert concept.embedding is None
        assert concept.mention_count == 0
        assert concept.paper_count == 0

    def test_full_concept(self):
        concept = ResearchConcept(
            name="attention mechanism",
            description="Core component of transformer architectures",
            aliases=["self-attention", "scaled dot-product attention"],
            embedding=[0.1] * 1536,
            mention_count=12,
            paper_count=7,
        )
        assert concept.name == "attention mechanism"
        assert concept.description == "Core component of transformer architectures"
        assert concept.aliases == [
            "self-attention",
            "scaled dot-product attention",
        ]
        assert len(concept.embedding) == 1536
        assert concept.mention_count == 12
        assert concept.paper_count == 7

    def test_auto_generates_uuid(self):
        c1 = ResearchConcept(name="concept one")
        c2 = ResearchConcept(name="concept two")
        assert c1.id != c2.id
        assert len(c1.id) == 36  # UUID format

    def test_auto_generates_timestamps(self):
        concept = ResearchConcept(name="any concept")
        assert isinstance(concept.created_at, datetime)
        assert isinstance(concept.updated_at, datetime)
        assert concept.created_at.tzinfo == timezone.utc


# =============================================================================
# Validation
# =============================================================================


class TestResearchConceptValidation:
    def test_name_too_short_raises(self):
        with pytest.raises(ValidationError, match="string_too_short"):
            ResearchConcept(name="A")

    def test_name_minimum_length_accepted(self):
        concept = ResearchConcept(name="AI")
        assert concept.name == "AI"

    def test_negative_mention_count_raises(self):
        with pytest.raises(ValidationError):
            ResearchConcept(name="bad", mention_count=-1)

    def test_negative_paper_count_raises(self):
        with pytest.raises(ValidationError):
            ResearchConcept(name="bad", paper_count=-1)

    def test_aliases_default_empty_list(self):
        concept = ResearchConcept(name="x-y")
        assert concept.aliases == []
        # Default is a fresh list, not a shared singleton.
        concept.aliases.append("foo")
        assert ResearchConcept(name="other").aliases == []


# =============================================================================
# Serialization
# =============================================================================


class TestResearchConceptSerialization:
    def test_basic_serialization_shape(self):
        concept = ResearchConcept(
            name="knowledge distillation",
            description="Training a smaller model to mimic a larger one",
            aliases=["KD", "teacher-student training"],
        )
        props = concept.to_neo4j_properties()
        assert props["name"] == "knowledge distillation"
        assert props["description"] == "Training a smaller model to mimic a larger one"
        assert isinstance(props["aliases"], str)  # JSON-serialized
        assert json.loads(props["aliases"]) == ["KD", "teacher-student training"]
        assert isinstance(props["created_at"], str)
        assert isinstance(props["updated_at"], str)

    def test_embedding_excluded(self):
        concept = ResearchConcept(
            name="retrieval augmented generation",
            embedding=[0.1] * 1536,
        )
        props = concept.to_neo4j_properties()
        assert "embedding" not in props

    def test_empty_aliases_serialize_to_empty_json_list(self):
        concept = ResearchConcept(name="in-context learning")
        props = concept.to_neo4j_properties()
        assert props["aliases"] == "[]"

    def test_all_expected_keys_present(self):
        concept = ResearchConcept(
            name="graph neural networks",
            description="Neural networks over graph-structured inputs",
            aliases=["GNN"],
            mention_count=3,
            paper_count=2,
        )
        props = concept.to_neo4j_properties()
        expected_keys = {
            "id",
            "name",
            "description",
            "aliases",
            "mention_count",
            "paper_count",
            "created_at",
            "updated_at",
        }
        assert expected_keys == set(props.keys())

    def test_datetime_iso_format(self):
        concept = ResearchConcept(name="dense retrieval")
        props = concept.to_neo4j_properties()
        datetime.fromisoformat(props["created_at"])
        datetime.fromisoformat(props["updated_at"])


# =============================================================================
# Identity
# =============================================================================


class TestResearchConceptIdentity:
    def test_same_name_distinct_ids(self):
        c1 = ResearchConcept(name="attention mechanism")
        c2 = ResearchConcept(name="attention mechanism")
        assert c1.id != c2.id

    def test_custom_id_preserved(self):
        concept = ResearchConcept(id="custom-id-123", name="self-supervised")
        assert concept.id == "custom-id-123"
