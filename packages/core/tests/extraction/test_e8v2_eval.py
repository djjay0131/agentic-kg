"""E-8 V2 Unit 9 — eval scoring extension.

Covers the V2 scoring helpers ``model_precision``, ``method_precision``,
and the combined Model+Method recall tripwire. Gold-file scaffolding
landed in fixtures/e8_eval/SELECTION.md.

AC-17 floors are documented as draft; the implementation phase calibrates
against a real LLM run before verify.
"""

from tests.extraction.test_e8_eval import (
    METHOD_PRECISION_AVG_MIN,
    METHOD_PRECISION_PAPER_MIN,
    MODEL_METHOD_RECALL_AVG_MIN,
    MODEL_PRECISION_AVG_MIN,
    MODEL_PRECISION_PAPER_MIN,
    method_precision,
    model_method_recall,
    model_precision,
)

# =============================================================================
# Constants — locked by spec AC-17
# =============================================================================


class TestGateConstants:
    def test_model_precision_avg_floor(self):
        assert MODEL_PRECISION_AVG_MIN == 0.70

    def test_model_precision_paper_floor(self):
        assert MODEL_PRECISION_PAPER_MIN == 0.50

    def test_method_precision_avg_floor(self):
        assert METHOD_PRECISION_AVG_MIN == 0.65

    def test_method_precision_paper_floor(self):
        assert METHOD_PRECISION_PAPER_MIN == 0.45

    def test_combined_recall_floor(self):
        assert MODEL_METHOD_RECALL_AVG_MIN == 0.45


# =============================================================================
# model_precision
# =============================================================================


class TestModelPrecision:
    def test_canonical_match(self):
        pred = [{"name": "BERT"}]
        gold = [{"canonical": "BERT", "acceptable_aliases": []}]
        assert model_precision(pred, gold) == 1.0

    def test_alias_match_case_insensitive(self):
        pred = [{"name": "BERT-base-uncased"}]
        gold = [
            {
                "canonical": "BERT",
                "acceptable_aliases": ["bert-base", "bert-base-uncased"],
            }
        ]
        assert model_precision(pred, gold) == 1.0

    def test_no_match_returns_zero(self):
        pred = [{"name": "PyTorch"}]
        gold = [{"canonical": "BERT", "acceptable_aliases": []}]
        assert model_precision(pred, gold) == 0.0

    def test_partial_match(self):
        pred = [{"name": "BERT"}, {"name": "PyTorch"}]
        gold = [{"canonical": "BERT", "acceptable_aliases": []}]
        assert model_precision(pred, gold) == 0.5

    def test_empty_predictions_vacuous_one(self):
        assert model_precision([], [{"canonical": "BERT"}]) == 1.0


# =============================================================================
# method_precision
# =============================================================================


class TestMethodPrecision:
    def test_canonical_match(self):
        pred = [{"name": "fine-tuning"}]
        gold = [{"canonical": "fine-tuning", "acceptable_aliases": []}]
        assert method_precision(pred, gold) == 1.0

    def test_alias_match(self):
        pred = [{"name": "RLHF"}]
        gold = [
            {
                "canonical": "reinforcement learning from human feedback",
                "acceptable_aliases": ["RLHF"],
            }
        ]
        assert method_precision(pred, gold) == 1.0

    def test_no_match(self):
        pred = [{"name": "evaluation"}]
        gold = [{"canonical": "contrastive learning", "acceptable_aliases": []}]
        assert method_precision(pred, gold) == 0.0


# =============================================================================
# model_method_recall — combined anti-gaming tripwire
# =============================================================================


class TestModelMethodRecall:
    def test_both_recovered_returns_one(self):
        models_pred = [{"name": "BERT"}]
        models_gold = [{"canonical": "BERT", "acceptable_aliases": []}]
        methods_pred = [{"name": "fine-tuning"}]
        methods_gold = [
            {"canonical": "fine-tuning", "acceptable_aliases": []}
        ]
        assert model_method_recall(
            models_pred, models_gold, methods_pred, methods_gold,
        ) == 1.0

    def test_half_recovered(self):
        models_pred = [{"name": "BERT"}]
        models_gold = [
            {"canonical": "BERT", "acceptable_aliases": []},
            {"canonical": "GPT-2", "acceptable_aliases": []},
        ]
        # Methods: predict nothing, miss both.
        methods_pred = []
        methods_gold = [
            {"canonical": "fine-tuning", "acceptable_aliases": []},
            {"canonical": "RLHF", "acceptable_aliases": []},
        ]
        # 1 of 4 total gold recovered → 0.25.
        assert model_method_recall(
            models_pred, models_gold, methods_pred, methods_gold,
        ) == 0.25

    def test_empty_gold_vacuous_one(self):
        assert model_method_recall(
            [{"name": "x"}], [], [{"name": "y"}], [],
        ) == 1.0

    def test_alias_recall_match(self):
        """Alias-only match still counts as recall coverage."""
        models_pred = [{"name": "bert-base"}]
        models_gold = [
            {"canonical": "BERT", "acceptable_aliases": ["bert-base"]}
        ]
        assert model_method_recall(
            models_pred, models_gold, [], [],
        ) == 1.0

    def test_predicted_models_dont_recover_method_gold(self):
        """Cross-pollination: a predicted Model that happens to match a
        gold Method's canonical should NOT count as recall — Models match
        Models, Methods match Methods."""
        models_pred = [{"name": "fine-tuning"}]
        models_gold = []
        methods_pred = []
        methods_gold = [
            {"canonical": "fine-tuning", "acceptable_aliases": []}
        ]
        # 0 of 1 gold methods recovered, no model gold to score.
        assert model_method_recall(
            models_pred, models_gold, methods_pred, methods_gold,
        ) == 0.0
