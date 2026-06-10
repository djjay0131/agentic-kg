"""Tests for Method entity (E-4, Unit 1).

Pure Pydantic validation — no Neo4j required. Mirrors
``test_research_concept_models.py`` (the E-2 shape) rather than the
``test_model_entity.py`` shape, because Method has no ``is_canonical``
field and the smaller surface tracks E-2.
"""

import json
from datetime import datetime, timezone

import pytest
from agentic_kg.knowledge_graph.models import Method
from pydantic import ValidationError


class TestMethodCreation:
    def test_minimal_method(self):
        m = Method(name="fine-tuning")
        assert m.name == "fine-tuning"
        assert m.description is None
        assert m.aliases == []
        assert m.method_type is None
        assert m.embedding is None
        assert m.usage_count == 0

    def test_full_method(self):
        m = Method(
            name="contrastive learning",
            description="Self-supervised pretraining via positive/negative pair contrast",
            aliases=["contrastive loss", "InfoNCE"],
            method_type="training",
            embedding=[0.1] * 1536,
            usage_count=12,
        )
        assert m.name == "contrastive learning"
        assert m.aliases == ["contrastive loss", "InfoNCE"]
        assert m.method_type == "training"
        assert len(m.embedding) == 1536
        assert m.usage_count == 12

    def test_auto_generates_uuid(self):
        a = Method(name="fine-tuning")
        b = Method(name="fine-tuning")
        assert a.id != b.id
        assert len(a.id) == 36  # UUID format

    def test_auto_generates_timestamps(self):
        m = Method(name="distillation")
        assert isinstance(m.created_at, datetime)
        assert isinstance(m.updated_at, datetime)
        assert m.created_at.tzinfo == timezone.utc


class TestMethodValidation:
    def test_name_too_short_raises(self):
        with pytest.raises(ValidationError):
            Method(name="A")

    def test_name_minimum_length_accepted(self):
        m = Method(name="FT")
        assert m.name == "FT"

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            Method(name="x" * 121)

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            Method(name="fine-tuning", description="d" * 401)

    def test_aliases_too_many_raises(self):
        """Spec Edge Case: alias cap = 20. Tech Lead Q3 review: hitting
        this is documented as a known limitation; the cap surfaces as a
        Pydantic ValidationError. This test pins the cap value so a future
        change to ``max_length`` is intentional."""
        with pytest.raises(ValidationError):
            Method(name="fine-tuning", aliases=[f"a{i}" for i in range(21)])

    def test_aliases_at_max_accepted(self):
        m = Method(name="fine-tuning", aliases=[f"a{i}" for i in range(20)])
        assert len(m.aliases) == 20

    def test_negative_usage_count_raises(self):
        with pytest.raises(ValidationError):
            Method(name="fine-tuning", usage_count=-1)

    def test_aliases_default_fresh_list(self):
        a = Method(name="A1")
        a.aliases.append("polluted")
        b = Method(name="A2")
        # Default factory yields a new list per instance.
        assert b.aliases == []

    def test_no_is_canonical_field(self):
        """E-4 deliberately drops the is_canonical field that E-3 has.
        Pin this so a future copy-paste from Model doesn't reintroduce it."""
        m = Method(name="fine-tuning")
        assert not hasattr(m, "is_canonical")
        assert "is_canonical" not in Method.model_fields


class TestMethodToNeo4jProperties:
    def test_basic_serialization_shape(self):
        m = Method(
            name="contrastive learning",
            description="self-supervised contrast",
            aliases=["contrastive loss"],
            method_type="training",
            embedding=[0.5] * 1536,
            usage_count=5,
        )
        props = m.to_neo4j_properties()

        assert props["id"] == m.id
        assert props["name"] == "contrastive learning"
        assert props["description"] == "self-supervised contrast"
        assert props["method_type"] == "training"
        assert props["usage_count"] == 5

    def test_aliases_json_encoded(self):
        m = Method(name="fine-tuning", aliases=["FT", "parameter-efficient fine-tuning"])
        props = m.to_neo4j_properties()
        assert isinstance(props["aliases"], str)
        assert json.loads(props["aliases"]) == [
            "FT",
            "parameter-efficient fine-tuning",
        ]

    def test_timestamps_iso_encoded(self):
        m = Method(name="fine-tuning")
        props = m.to_neo4j_properties()
        datetime.fromisoformat(props["created_at"])
        datetime.fromisoformat(props["updated_at"])

    def test_embedding_excluded_from_properties(self):
        m = Method(name="fine-tuning", embedding=[0.1] * 1536)
        props = m.to_neo4j_properties()
        assert "embedding" not in props

    def test_optional_fields_when_unset_serialize(self):
        m = Method(name="fine-tuning")
        props = m.to_neo4j_properties()
        assert props["description"] is None
        assert props["method_type"] is None
