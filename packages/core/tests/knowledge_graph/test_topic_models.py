"""
Tests for Topic and TopicLevel models.

Tests Pydantic validation, serialization, and edge cases for
the Topic entity and TopicLevel enum introduced in E-1.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentic_kg.knowledge_graph.models import Topic, TopicLevel


# =============================================================================
# TopicLevel Enum Tests
# =============================================================================


class TestTopicLevel:
    """Tests for TopicLevel enum."""

    def test_all_values_exist(self):
        assert TopicLevel.DOMAIN == "domain"
        assert TopicLevel.AREA == "area"
        assert TopicLevel.SUBTOPIC == "subtopic"

    def test_string_value(self):
        assert TopicLevel.DOMAIN.value == "domain"
        assert isinstance(TopicLevel.DOMAIN, str)

    def test_from_string(self):
        assert TopicLevel("domain") == TopicLevel.DOMAIN
        assert TopicLevel("area") == TopicLevel.AREA
        assert TopicLevel("subtopic") == TopicLevel.SUBTOPIC

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TopicLevel("invalid")


# =============================================================================
# Topic Model Tests
# =============================================================================


class TestTopicCreation:
    """Tests for Topic model creation and defaults."""

    def test_minimal_valid_topic(self):
        topic = Topic(name="Computer Science", level=TopicLevel.DOMAIN)
        assert topic.name == "Computer Science"
        assert topic.level == TopicLevel.DOMAIN
        assert topic.parent_id is None
        assert topic.source == "manual"
        assert topic.openalex_id is None
        assert topic.embedding is None
        assert topic.problem_count == 0
        assert topic.paper_count == 0
        assert topic.description is None

    def test_full_topic(self):
        topic = Topic(
            name="Natural Language Processing",
            description="Study of computational linguistics",
            level=TopicLevel.AREA,
            parent_id="parent-uuid-123",
            source="openalex",
            openalex_id="C12345",
            embedding=[0.1] * 1536,
            problem_count=5,
            paper_count=10,
        )
        assert topic.name == "Natural Language Processing"
        assert topic.description == "Study of computational linguistics"
        assert topic.level == TopicLevel.AREA
        assert topic.parent_id == "parent-uuid-123"
        assert topic.source == "openalex"
        assert topic.openalex_id == "C12345"
        assert len(topic.embedding) == 1536
        assert topic.problem_count == 5
        assert topic.paper_count == 10

    def test_auto_generates_uuid(self):
        t1 = Topic(name="Topic A", level=TopicLevel.AREA)
        t2 = Topic(name="Topic B", level=TopicLevel.AREA)
        assert t1.id != t2.id
        assert len(t1.id) == 36  # UUID format

    def test_auto_generates_timestamps(self):
        topic = Topic(name="Test Topic", level=TopicLevel.AREA)
        assert isinstance(topic.created_at, datetime)
        assert isinstance(topic.updated_at, datetime)
        assert topic.created_at.tzinfo == timezone.utc


class TestTopicValidation:
    """Tests for Topic model validation rules."""

    def test_name_too_short_raises(self):
        with pytest.raises(ValidationError, match="string_too_short"):
            Topic(name="A", level=TopicLevel.AREA)

    def test_name_minimum_length_accepted(self):
        topic = Topic(name="AI", level=TopicLevel.AREA)
        assert topic.name == "AI"

    def test_negative_problem_count_raises(self):
        with pytest.raises(ValidationError):
            Topic(name="Test", level=TopicLevel.AREA, problem_count=-1)

    def test_negative_paper_count_raises(self):
        with pytest.raises(ValidationError):
            Topic(name="Test", level=TopicLevel.AREA, paper_count=-1)

    def test_domain_with_parent_raises(self):
        with pytest.raises(ValidationError, match="Domain-level topics must not have a parent_id"):
            Topic(name="CS", level=TopicLevel.DOMAIN, parent_id="some-parent")

    def test_domain_without_parent_valid(self):
        topic = Topic(name="Computer Science", level=TopicLevel.DOMAIN)
        assert topic.parent_id is None

    def test_area_with_parent_valid(self):
        topic = Topic(name="NLP", level=TopicLevel.AREA, parent_id="parent-123")
        assert topic.parent_id == "parent-123"

    def test_area_without_parent_valid(self):
        topic = Topic(name="NLP", level=TopicLevel.AREA)
        assert topic.parent_id is None

    def test_subtopic_with_parent_valid(self):
        topic = Topic(name="Machine Translation", level=TopicLevel.SUBTOPIC, parent_id="area-123")
        assert topic.parent_id == "area-123"


class TestTopicSerialization:
    """Tests for Topic.to_neo4j_properties()."""

    def test_basic_serialization(self):
        topic = Topic(name="NLP", level=TopicLevel.AREA, source="manual")
        props = topic.to_neo4j_properties()
        assert props["name"] == "NLP"
        assert props["level"] == "area"
        assert props["source"] == "manual"
        assert isinstance(props["created_at"], str)
        assert isinstance(props["updated_at"], str)

    def test_embedding_excluded(self):
        topic = Topic(
            name="NLP", level=TopicLevel.AREA, embedding=[0.1] * 1536
        )
        props = topic.to_neo4j_properties()
        assert "embedding" not in props

    def test_enum_serialized_as_string(self):
        topic = Topic(name="CS", level=TopicLevel.DOMAIN)
        props = topic.to_neo4j_properties()
        assert props["level"] == "domain"
        assert isinstance(props["level"], str)

    def test_all_fields_present(self):
        topic = Topic(
            name="NLP",
            description="Natural Language Processing",
            level=TopicLevel.AREA,
            parent_id="parent-123",
            source="openalex",
            openalex_id="C12345",
        )
        props = topic.to_neo4j_properties()
        expected_keys = {
            "id", "name", "description", "level", "parent_id", "source",
            "openalex_id", "problem_count", "paper_count", "created_at", "updated_at",
        }
        assert expected_keys == set(props.keys())

    def test_none_fields_preserved(self):
        topic = Topic(name="NLP", level=TopicLevel.AREA)
        props = topic.to_neo4j_properties()
        assert props["description"] is None
        assert props["parent_id"] is None
        assert props["openalex_id"] is None

    def test_datetime_iso_format(self):
        topic = Topic(name="NLP", level=TopicLevel.AREA)
        props = topic.to_neo4j_properties()
        datetime.fromisoformat(props["created_at"])
        datetime.fromisoformat(props["updated_at"])


class TestTopicEquality:
    """Tests for Topic identity and comparison."""

    def test_same_name_different_ids(self):
        t1 = Topic(name="NLP", level=TopicLevel.AREA)
        t2 = Topic(name="NLP", level=TopicLevel.AREA)
        assert t1.id != t2.id

    def test_duplicate_names_different_levels(self):
        t1 = Topic(name="Information Retrieval", level=TopicLevel.AREA)
        t2 = Topic(name="Information Retrieval", level=TopicLevel.SUBTOPIC, parent_id="p-1")
        assert t1.id != t2.id
        assert t1.level != t2.level
