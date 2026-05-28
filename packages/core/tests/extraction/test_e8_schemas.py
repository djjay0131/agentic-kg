"""E-8 Unit 1 — schemas for topic and concept extraction.

Tests the Pydantic models added to ``extraction.schemas`` by E-8:

- ``_ExtractedTopicAssignmentBase`` — Literal-free base; the dynamic Literal
  field is bolted on by ``TopicExtractor.__init__``.
- ``ExtractedResearchConcept`` — full schema for concept extraction.
- ``ExtractedEntities`` — orchestrator container; not an LLM response model.

Covers AC-1.
"""

import pytest
from agentic_kg.extraction.schemas import (
    ExtractedEntities,
    ExtractedResearchConcept,
    _ExtractedTopicAssignmentBase,
)
from pydantic import ValidationError


class TestExtractedTopicAssignmentBase:
    def test_minimum_valid_instance(self):
        t = _ExtractedTopicAssignmentBase(level="area")
        assert t.level == "area"
        assert t.confidence == 0.8
        assert t.reasoning is None

    def test_level_must_be_one_of_three(self):
        with pytest.raises(ValidationError):
            _ExtractedTopicAssignmentBase(level="bogus")

    def test_confidence_clamped_to_unit_interval(self):
        with pytest.raises(ValidationError):
            _ExtractedTopicAssignmentBase(level="area", confidence=1.5)
        with pytest.raises(ValidationError):
            _ExtractedTopicAssignmentBase(level="area", confidence=-0.1)

    def test_optional_reasoning_persisted(self):
        t = _ExtractedTopicAssignmentBase(
            level="subtopic", confidence=0.92, reasoning="matches the abstract"
        )
        assert t.reasoning == "matches the abstract"

    def test_no_topic_name_field_on_base(self):
        # The dynamic Literal field is added by TopicExtractor.__init__,
        # not by the base class. The base must be schema-stable across
        # taxonomy revisions.
        assert "topic_name" not in _ExtractedTopicAssignmentBase.model_fields


class TestExtractedResearchConcept:
    def test_minimum_valid_instance(self):
        c = ExtractedResearchConcept(
            name="attention mechanism",
            quoted_text="we use multi-head self-attention",
        )
        assert c.name == "attention mechanism"
        assert c.aliases == []
        assert c.description is None
        assert c.confidence == 0.8

    def test_name_min_length(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(name="x", quoted_text="x" * 12)

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(name="x" * 121, quoted_text="x" * 12)

    def test_aliases_max_length(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(
                name="concept",
                aliases=[f"alias{i}" for i in range(11)],
                quoted_text="grounding text",
            )

    def test_description_max_length(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(
                name="concept",
                description="d" * 401,
                quoted_text="grounding text",
            )

    def test_quoted_text_min_length(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(name="concept", quoted_text="short")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            ExtractedResearchConcept(
                name="concept", confidence=1.1, quoted_text="x" * 12
            )


class TestExtractedEntities:
    def test_defaults_empty(self):
        env = ExtractedEntities()
        assert env.topics == []
        assert env.concepts == []

    def test_topics_max_length(self):
        too_many = [
            _ExtractedTopicAssignmentBase(level="area") for _ in range(6)
        ]
        with pytest.raises(ValidationError):
            ExtractedEntities(topics=too_many)

    def test_concepts_max_length(self):
        too_many = [
            ExtractedResearchConcept(
                name=f"concept-{i}", quoted_text="some grounding text"
            )
            for i in range(21)
        ]
        with pytest.raises(ValidationError):
            ExtractedEntities(concepts=too_many)

    def test_populated_envelope(self):
        env = ExtractedEntities(
            topics=[_ExtractedTopicAssignmentBase(level="area", confidence=0.9)],
            concepts=[
                ExtractedResearchConcept(
                    name="attention", quoted_text="self-attention is used"
                )
            ],
        )
        assert len(env.topics) == 1
        assert len(env.concepts) == 1


class TestSchemaModuleIsTaxonomyFree:
    """AC-1 final clause: importing schemas does NOT read seed_taxonomy.yml.

    Spec rationale: taxonomy coupling belongs to TopicExtractor, not the
    schema module. A module-level taxonomy read would force every importer
    of schemas to pay disk-IO cost and break import in environments where
    seed_taxonomy.yml is unavailable.
    """

    def test_schemas_module_does_not_import_taxonomy(self):
        import importlib
        import sys

        # Force a clean import to detect any side-effect read.
        for mod in [
            "agentic_kg.extraction.schemas",
            "agentic_kg.knowledge_graph.taxonomy",
        ]:
            sys.modules.pop(mod, None)

        import agentic_kg.extraction.schemas  # noqa: F401

        importlib.reload(agentic_kg.extraction.schemas)
        # If schemas had imported taxonomy at module scope, it would now
        # appear in sys.modules. Importers that need taxonomy do so lazily.
        assert "agentic_kg.knowledge_graph.taxonomy" not in sys.modules
