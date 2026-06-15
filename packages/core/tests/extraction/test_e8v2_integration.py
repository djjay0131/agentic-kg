"""E-8 V2 Unit 5 — integrate_paper_entities Model + Method writers.

Covers AC-7 (USES_MODEL + create_or_merge_model), AC-8 (APPLIES_METHOD +
create_or_merge_method), AC-9 (canonical merge — exercised at the contract
level: the integrator passes the extraction-emitted name without setting
is_canonical, leaving E-3's dedup-merge behavior unchanged), AC-16
(generate_description NOT passed by the integrator).
"""

from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction.kg_integration_v2 import (
    MIN_METHOD_CONFIDENCE,
    MIN_MODEL_CONFIDENCE,
    integrate_paper_entities,
)
from agentic_kg.extraction.pipeline import PaperExtractionResult
from agentic_kg.extraction.schemas import ExtractedMethod, ExtractedModel


@pytest.fixture
def mock_repo():
    repo = MagicMock()

    # create_or_merge_model returns (model, created).
    def _merge_model(name, **_):
        model = MagicMock()
        model.id = f"m-{name.lower().replace(' ', '-')}"
        model.name = name
        return model, True

    repo.create_or_merge_model.side_effect = _merge_model

    def _merge_method(name, **_):
        method = MagicMock()
        method.id = f"meth-{name.lower().replace(' ', '-')}"
        method.name = name
        return method, True

    repo.create_or_merge_method.side_effect = _merge_method

    repo.link_paper_to_model.return_value = True
    repo.link_paper_to_method.return_value = True

    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: None
    session.run.return_value = MagicMock()
    repo.session.return_value = session
    return repo


def _model(
    name: str, confidence: float = 0.9, **kwargs,
) -> ExtractedModel:
    return ExtractedModel(
        name=name,
        quoted_text="grounding text for the model here",
        confidence=confidence,
        **kwargs,
    )


def _method(
    name: str, confidence: float = 0.9, **kwargs,
) -> ExtractedMethod:
    return ExtractedMethod(
        name=name,
        quoted_text="grounding text for the method here",
        confidence=confidence,
        **kwargs,
    )


# =============================================================================
# AC-7: USES_MODEL writer
# =============================================================================


class TestModelWriter:
    def test_uses_model_edge_per_extraction(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[
                    _model("BERT", architecture="transformer"),
                    _model("ResNet-50", architecture="cnn"),
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        assert result.models_linked == 2
        assert mock_repo.create_or_merge_model.call_count == 2
        assert mock_repo.link_paper_to_model.call_count == 2

    def test_below_threshold_model_filtered(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[
                    _model("BERT", confidence=0.95),
                    _model("XLNet", confidence=0.4),  # below default 0.7
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        assert result.models_linked == 1
        mock_repo.create_or_merge_model.assert_called_once()

    def test_custom_min_model_confidence_respected(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[_model("BERT", confidence=0.8)],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
            min_model_confidence=0.9,  # raise threshold above the extraction
        )
        assert result.models_linked == 0
        mock_repo.create_or_merge_model.assert_not_called()

    def test_model_fields_forwarded_to_repo(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[
                    _model(
                        "BERT",
                        aliases=["bert-base"],
                        architecture="transformer",
                        model_type="language_model",
                        year_introduced=2018,
                        description="encoder-only transformer LM",
                    ),
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        kwargs = mock_repo.create_or_merge_model.call_args.kwargs
        assert kwargs["name"] == "BERT"
        assert kwargs["aliases"] == ["bert-base"]
        assert kwargs["architecture"] == "transformer"
        assert kwargs["model_type"] == "language_model"
        assert kwargs["year_introduced"] == 2018
        assert kwargs["description"] == "encoder-only transformer LM"

    def test_generate_description_not_passed_to_repo(self, mock_repo):
        """AC-16: ingestion path keeps generate_description=False default by
        omitting the kwarg entirely (E-6 AC-11)."""
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[_model("BERT")],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        kwargs = mock_repo.create_or_merge_model.call_args.kwargs
        assert "generate_description" not in kwargs

    def test_link_paper_to_model_uses_returned_id(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[_model("BERT")],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        link_kwargs = mock_repo.link_paper_to_model.call_args.kwargs
        assert link_kwargs["paper_doi"] == "10.1/abc"
        assert link_kwargs["model_id"] == "m-bert"

    def test_integrator_never_sets_is_canonical(self, mock_repo):
        """AC-9: extractor + integrator never mark the model canonical.

        The seed YAML owns is_canonical. If the LLM emits an alias of a
        canonical model (e.g. "bert-base-uncased"), the dedup-merge at
        the repo layer routes to the canonical node. The integrator
        must never pass is_canonical=True regardless.
        """
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                models=[_model("bert-base-uncased")],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        kwargs = mock_repo.create_or_merge_model.call_args.kwargs
        assert "is_canonical" not in kwargs
        # And the extractor-emitted name passes through unchanged — no
        # canonical-name remapping at the integrator layer.
        assert kwargs["name"] == "bert-base-uncased"


# =============================================================================
# AC-8: APPLIES_METHOD writer
# =============================================================================


class TestMethodWriter:
    def test_applies_method_edge_per_extraction(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                methods=[
                    _method("fine-tuning"),
                    _method("contrastive learning"),
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        assert result.methods_linked == 2
        assert mock_repo.create_or_merge_method.call_count == 2
        assert mock_repo.link_paper_to_method.call_count == 2

    def test_below_threshold_method_filtered(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                methods=[
                    _method("RLHF", confidence=0.9),
                    _method("grid search", confidence=0.4),
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        assert result.methods_linked == 1

    def test_method_fields_forwarded_to_repo(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                methods=[
                    _method(
                        "RLHF",
                        aliases=["reinforcement learning from human feedback"],
                        method_type="training",
                        description="Train via human pref feedback",
                    ),
                ],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        kwargs = mock_repo.create_or_merge_method.call_args.kwargs
        assert kwargs["name"] == "RLHF"
        assert kwargs["aliases"] == [
            "reinforcement learning from human feedback"
        ]
        assert kwargs["method_type"] == "training"
        assert kwargs["description"] == "Train via human pref feedback"

    def test_generate_description_not_passed_to_repo(self, mock_repo):
        """AC-16: same as Model — sync path, default False kept."""
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                methods=[_method("fine-tuning")],
            ),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        kwargs = mock_repo.create_or_merge_method.call_args.kwargs
        assert "generate_description" not in kwargs


# =============================================================================
# Constants + backwards-compatibility checks
# =============================================================================


class TestConstants:
    def test_min_model_confidence_default(self):
        assert MIN_MODEL_CONFIDENCE == 0.7

    def test_min_method_confidence_default(self):
        assert MIN_METHOD_CONFIDENCE == 0.7


class TestV2FieldsTolerateOldResult:
    """The integrator must not crash when an old extraction-result object
    (no models/methods attrs) is passed — defensive ``getattr`` keeps
    backward-compat for any pinned PaperExtractionResult callers."""

    def test_extraction_result_without_models_attr(self, mock_repo):
        bare = MagicMock()
        bare.topics = []
        bare.concepts = []
        bare.failures = []
        # Intentionally no `models` / `methods` attrs.
        del bare.models
        del bare.methods

        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=bare,
            mentions=[],
            taxonomy_hash="hash-1",
            repo=mock_repo,
        )
        assert result.models_linked == 0
        assert result.methods_linked == 0
