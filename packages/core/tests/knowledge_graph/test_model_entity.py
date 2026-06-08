"""
Tests for Model entity (E-3, Unit 1).

Covers Pydantic validation, field shape, default values, and
``to_neo4j_properties()`` serialization. Pure model — no Neo4j required.
"""

import json
from datetime import datetime, timezone

import pytest
from agentic_kg.knowledge_graph.models import Model
from pydantic import ValidationError

# =============================================================================
# Creation & defaults
# =============================================================================


class TestModelCreation:
    def test_minimal_model(self):
        m = Model(name="BERT")
        assert m.name == "BERT"
        assert m.description is None
        assert m.aliases == []
        assert m.architecture is None
        assert m.model_type is None
        assert m.year_introduced is None
        assert m.introducing_paper_doi is None
        assert m.is_canonical is False
        assert m.embedding is None
        assert m.usage_count == 0

    def test_full_model(self):
        m = Model(
            name="BERT",
            description="Bidirectional Encoder Representations from Transformers",
            aliases=["bert-base", "bert-large"],
            architecture="transformer",
            model_type="language_model",
            year_introduced=2018,
            introducing_paper_doi="10.18653/v1/N19-1423",
            is_canonical=True,
            embedding=[0.1] * 1536,
            usage_count=42,
        )
        assert m.name == "BERT"
        assert "Bidirectional" in m.description
        assert m.aliases == ["bert-base", "bert-large"]
        assert m.architecture == "transformer"
        assert m.model_type == "language_model"
        assert m.year_introduced == 2018
        assert m.introducing_paper_doi == "10.18653/v1/N19-1423"
        assert m.is_canonical is True
        assert len(m.embedding) == 1536
        assert m.usage_count == 42

    def test_auto_generates_uuid(self):
        a = Model(name="GPT-4")
        b = Model(name="GPT-4")
        assert a.id != b.id
        assert len(a.id) == 36  # UUID format

    def test_auto_generates_timestamps(self):
        m = Model(name="ResNet")
        assert isinstance(m.created_at, datetime)
        assert isinstance(m.updated_at, datetime)
        assert m.created_at.tzinfo == timezone.utc


# =============================================================================
# Validation
# =============================================================================


class TestModelValidation:
    def test_name_too_short_raises(self):
        with pytest.raises(ValidationError):
            Model(name="A")

    def test_name_minimum_length_accepted(self):
        m = Model(name="T5")
        assert m.name == "T5"

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            Model(name="x" * 121)

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            Model(name="BERT", description="d" * 401)

    def test_aliases_too_many_raises(self):
        with pytest.raises(ValidationError):
            Model(name="BERT", aliases=[f"a{i}" for i in range(21)])

    def test_aliases_at_max_accepted(self):
        m = Model(name="BERT", aliases=[f"a{i}" for i in range(20)])
        assert len(m.aliases) == 20

    def test_negative_usage_count_raises(self):
        with pytest.raises(ValidationError):
            Model(name="BERT", usage_count=-1)

    def test_aliases_default_fresh_list(self):
        a = Model(name="BERT")
        a.aliases.append("polluted")
        b = Model(name="GPT-4")
        # Default factory yields a new list per instance.
        assert b.aliases == []


# =============================================================================
# Serialization to Neo4j
# =============================================================================


class TestModelToNeo4jProperties:
    def test_basic_serialization_shape(self):
        m = Model(
            name="BERT",
            description="A transformer-based language model",
            aliases=["bert-base", "bert-large"],
            architecture="transformer",
            year_introduced=2018,
            is_canonical=True,
            embedding=[0.5] * 1536,
            usage_count=5,
        )
        props = m.to_neo4j_properties()

        assert props["id"] == m.id
        assert props["name"] == "BERT"
        assert props["description"] == "A transformer-based language model"
        assert props["architecture"] == "transformer"
        assert props["year_introduced"] == 2018
        assert props["is_canonical"] is True
        assert props["usage_count"] == 5

    def test_aliases_json_encoded(self):
        m = Model(name="BERT", aliases=["bert-base", "bert-large"])
        props = m.to_neo4j_properties()
        # Aliases must be a JSON string for Neo4j (Neo4j cannot store list
        # of strings directly via the entity model — same pattern as E-2).
        assert isinstance(props["aliases"], str)
        assert json.loads(props["aliases"]) == ["bert-base", "bert-large"]

    def test_timestamps_iso_encoded(self):
        m = Model(name="BERT")
        props = m.to_neo4j_properties()
        # Round-trip parseable.
        datetime.fromisoformat(props["created_at"])
        datetime.fromisoformat(props["updated_at"])

    def test_embedding_excluded_from_properties(self):
        """Embedding is set via a separate Cypher SET — the entity model
        intentionally excludes it from the property dict so the repository
        code can decide when to write it."""
        m = Model(name="BERT", embedding=[0.1] * 1536)
        props = m.to_neo4j_properties()
        assert "embedding" not in props

    def test_optional_fields_when_unset_serialize(self):
        m = Model(name="BERT")
        props = m.to_neo4j_properties()
        # description / architecture / model_type / year / paper_doi all None.
        # Neo4j supports null properties; downstream code reads them back as None.
        assert props["description"] is None
        assert props["architecture"] is None
        assert props["model_type"] is None
        assert props["year_introduced"] is None
        assert props["introducing_paper_doi"] is None
