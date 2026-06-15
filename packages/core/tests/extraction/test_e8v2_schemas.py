"""E-8 V2 Unit 1 — ExtractedModel + ExtractedMethod schemas.

Covers AC-1.
"""

import pytest
from agentic_kg.extraction.schemas import (
    ExtractedEntities,
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)
from pydantic import ValidationError


class TestExtractedModel:
    def test_minimal_valid(self):
        m = ExtractedModel(name="BERT", quoted_text="we use BERT-base")
        assert m.name == "BERT"
        assert m.aliases == []
        assert m.architecture is None
        assert m.model_type is None
        assert m.year_introduced is None
        assert m.description is None
        assert m.confidence == 0.8
        assert m.quoted_text == "we use BERT-base"

    def test_full_valid(self):
        m = ExtractedModel(
            name="BERT",
            aliases=["bert-base", "bert-large"],
            architecture="transformer",
            model_type="language_model",
            year_introduced=2018,
            description="Bidirectional Encoder Representations from Transformers.",
            confidence=0.95,
            quoted_text="we fine-tune BERT-base-uncased",
        )
        assert m.aliases == ["bert-base", "bert-large"]
        assert m.architecture == "transformer"
        assert m.year_introduced == 2018

    def test_name_below_min_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(name="x", quoted_text="text here ok")

    def test_name_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(name="x" * 121, quoted_text="text here ok")

    def test_aliases_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(
                name="BERT",
                aliases=[f"a{i}" for i in range(11)],
                quoted_text="text here",
            )

    def test_quoted_text_below_min_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(name="BERT", quoted_text="short")

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(name="BERT", quoted_text="text here", confidence=1.5)

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(name="BERT", quoted_text="text here", confidence=-0.1)

    def test_year_introduced_below_min_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(
                name="XX", quoted_text="text here ok", year_introduced=1949,
            )

    def test_year_introduced_above_max_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(
                name="XX", quoted_text="text here ok", year_introduced=2101,
            )

    def test_year_introduced_at_min_accepted(self):
        m = ExtractedModel(
            name="XX", quoted_text="text here ok", year_introduced=1950,
        )
        assert m.year_introduced == 1950

    def test_year_introduced_at_max_accepted(self):
        m = ExtractedModel(
            name="XX", quoted_text="text here ok", year_introduced=2100,
        )
        assert m.year_introduced == 2100

    def test_description_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(
                name="XX", quoted_text="text here ok", description="d" * 401,
            )

    def test_architecture_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedModel(
                name="XX", quoted_text="text here ok", architecture="a" * 41,
            )


class TestExtractedMethod:
    def test_minimal_valid(self):
        m = ExtractedMethod(
            name="fine-tuning", quoted_text="we fine-tune the model",
        )
        assert m.name == "fine-tuning"
        assert m.aliases == []
        assert m.method_type is None
        assert m.confidence == 0.8

    def test_full_valid(self):
        m = ExtractedMethod(
            name="contrastive learning",
            aliases=["contrastive pretraining", "InfoNCE"],
            method_type="training",
            description="Self-supervised pretraining objective.",
            confidence=0.85,
            quoted_text="we adopt contrastive learning",
        )
        assert m.aliases == ["contrastive pretraining", "InfoNCE"]
        assert m.method_type == "training"
        assert m.confidence == 0.85

    def test_name_below_min_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedMethod(name="x", quoted_text="text here")

    def test_name_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedMethod(name="x" * 121, quoted_text="text here")

    def test_aliases_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedMethod(
                name="fine-tuning",
                aliases=[f"a{i}" for i in range(11)],
                quoted_text="text here",
            )

    def test_quoted_text_below_min_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedMethod(name="fine-tuning", quoted_text="short")

    def test_method_type_above_max_length_raises(self):
        with pytest.raises(ValidationError):
            ExtractedMethod(
                name="fine-tuning",
                quoted_text="text here",
                method_type="m" * 41,
            )


class TestExtractedEntitiesEnvelope:
    def test_default_empty(self):
        env = ExtractedEntities()
        assert env.topics == []
        assert env.concepts == []
        assert env.models == []
        assert env.methods == []

    def test_carries_v2_lists(self):
        env = ExtractedEntities(
            concepts=[
                ExtractedResearchConcept(
                    name="attention", quoted_text="self-attention layer"
                )
            ],
            models=[ExtractedModel(name="BERT", quoted_text="we use BERT")],
            methods=[
                ExtractedMethod(
                    name="fine-tuning", quoted_text="we fine-tune the model"
                )
            ],
        )
        assert len(env.models) == 1
        assert len(env.methods) == 1
        assert env.models[0].name == "BERT"
        assert env.methods[0].name == "fine-tuning"

    def test_models_above_max_length_raises(self):
        m = ExtractedModel(name="XX", quoted_text="text here ok")
        with pytest.raises(ValidationError):
            ExtractedEntities(models=[m] * 21)

    def test_methods_above_max_length_raises(self):
        m = ExtractedMethod(name="XX", quoted_text="text here ok")
        with pytest.raises(ValidationError):
            ExtractedEntities(methods=[m] * 21)
