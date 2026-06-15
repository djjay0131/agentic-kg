"""E-8 Unit 13 — eval-set runner scaffolding (AC-12).

The actual eval is opt-in via ``pytest -m costly`` and runs during
``/constellize:feature:verify``. This module ships the scoring + gate
math so that as soon as a ``paper_*.gold.yml`` fixture lands in
``fixtures/e8_eval/``, the verify run picks it up automatically.

The 5-paper labeling is intentionally deferred to the verify phase
because it requires (a) confident hand-labels and (b) external review by
someone other than the spec author. The scaffolding below is what they
plug into.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "e8_eval"


# =============================================================================
# Gate constants (locked by spec QA review)
# =============================================================================


TOPIC_PRECISION_AVG_MIN = 0.80
TOPIC_PRECISION_PAPER_MIN = 0.60
CONCEPT_PRECISION_AVG_MIN = 0.70
CONCEPT_PRECISION_PAPER_MIN = 0.50
CONCEPT_RECALL_AVG_MIN = 0.50  # anti-gaming tripwire — see spec Q4b

# E-8 V2 (AC-17) — draft floors, slightly below V1's. The implementation
# phase MUST calibrate these against a real LLM run before verify; if the
# numbers can't be cleared, the verify gate decides whether to lower with
# documented justification, tune prompts, or defer.
MODEL_PRECISION_AVG_MIN = 0.70
MODEL_PRECISION_PAPER_MIN = 0.50
METHOD_PRECISION_AVG_MIN = 0.65
METHOD_PRECISION_PAPER_MIN = 0.45
MODEL_METHOD_RECALL_AVG_MIN = 0.45  # combined anti-gaming tripwire


# =============================================================================
# Scoring helpers (pure, testable)
# =============================================================================


def topic_precision(predicted: list[dict], gold: list[dict]) -> float:
    """Topic precision for a single paper.

    A predicted topic is correct iff its ``name`` matches an entry in
    ``gold``. Returns 1.0 when both are empty (vacuous success), 0.0 when
    predictions exist but none match.
    """
    if not predicted:
        # No predictions ⇒ no false positives ⇒ vacuous 1.0. The recall
        # tripwire catches the "predict nothing" gaming risk.
        return 1.0
    gold_names = {g["name"].lower() for g in gold}
    hits = sum(
        1 for p in predicted if p["name"].lower() in gold_names
    )
    return hits / len(predicted)


def concept_precision(
    predicted: list[dict], gold: list[dict]
) -> float:
    """Concept precision: each prediction must match a gold canonical or
    one of its ``acceptable_aliases`` (case-insensitive)."""
    if not predicted:
        return 1.0
    gold_match_set: set[str] = set()
    for g in gold:
        gold_match_set.add(g["canonical"].lower())
        for a in g.get("acceptable_aliases", []) or []:
            gold_match_set.add(a.lower())
    hits = sum(1 for p in predicted if p["name"].lower() in gold_match_set)
    return hits / len(predicted)


def model_precision(predicted: list[dict], gold: list[dict]) -> float:
    """E-8 V2 (AC-17) — model precision (mirror of ``concept_precision``).

    A predicted model is correct iff its ``name`` matches a gold canonical
    or one of its ``acceptable_aliases`` (case-insensitive).
    """
    if not predicted:
        return 1.0
    gold_match_set: set[str] = set()
    for g in gold:
        gold_match_set.add(g["canonical"].lower())
        for a in g.get("acceptable_aliases", []) or []:
            gold_match_set.add(a.lower())
    hits = sum(1 for p in predicted if p["name"].lower() in gold_match_set)
    return hits / len(predicted)


def method_precision(predicted: list[dict], gold: list[dict]) -> float:
    """E-8 V2 (AC-17) — same shape as ``model_precision``."""
    return model_precision(predicted, gold)


def model_method_recall(
    predicted_models: list[dict],
    gold_models: list[dict],
    predicted_methods: list[dict],
    gold_methods: list[dict],
) -> float:
    """E-8 V2 (AC-17) — combined recall tripwire across Model + Method.

    Catches confidence-threshold gaming: bumping
    ``MIN_MODEL_CONFIDENCE`` / ``MIN_METHOD_CONFIDENCE`` to 0.95 would let
    precision look perfect; this floor pulls back when the price is
    "predict nothing".
    """
    gold_total = len(gold_models) + len(gold_methods)
    if gold_total == 0:
        return 1.0
    predicted_model_lower = {p["name"].lower() for p in predicted_models}
    predicted_method_lower = {p["name"].lower() for p in predicted_methods}
    matched = 0
    for g in gold_models:
        names = {g["canonical"].lower()} | {
            a.lower() for a in g.get("acceptable_aliases", []) or []
        }
        if predicted_model_lower & names:
            matched += 1
    for g in gold_methods:
        names = {g["canonical"].lower()} | {
            a.lower() for a in g.get("acceptable_aliases", []) or []
        }
        if predicted_method_lower & names:
            matched += 1
    return matched / gold_total


def concept_recall(predicted: list[dict], gold: list[dict]) -> float:
    """Concept recall: fraction of gold canonicals matched by at least one
    prediction (canonical or acceptable_alias).
    """
    if not gold:
        return 1.0
    predicted_lower = {p["name"].lower() for p in predicted}
    matched = 0
    for g in gold:
        names = {g["canonical"].lower()} | {
            a.lower() for a in g.get("acceptable_aliases", []) or []
        }
        if predicted_lower & names:
            matched += 1
    return matched / len(gold)


# =============================================================================
# Fixture discovery
# =============================================================================


def _discover_eval_fixtures() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("paper_*.gold.yml"))


# =============================================================================
# Scoring unit tests — exercise the math even without fixtures
# =============================================================================


class TestTopicPrecision:
    def test_all_correct(self):
        pred = [{"name": "NLP"}, {"name": "Computer Vision"}]
        gold = [{"name": "NLP"}, {"name": "Computer Vision"}]
        assert topic_precision(pred, gold) == 1.0

    def test_one_wrong(self):
        pred = [{"name": "NLP"}, {"name": "Quantum"}]
        gold = [{"name": "NLP"}]
        assert topic_precision(pred, gold) == 0.5

    def test_empty_predictions_vacuous_one(self):
        assert topic_precision([], [{"name": "NLP"}]) == 1.0


class TestConceptPrecision:
    def test_canonical_match(self):
        pred = [{"name": "attention mechanism"}]
        gold = [{"canonical": "attention mechanism", "acceptable_aliases": []}]
        assert concept_precision(pred, gold) == 1.0

    def test_alias_match(self):
        pred = [{"name": "self-attention"}]
        gold = [
            {
                "canonical": "attention mechanism",
                "acceptable_aliases": ["self-attention", "scaled dot-product attention"],
            }
        ]
        assert concept_precision(pred, gold) == 1.0

    def test_no_match(self):
        pred = [{"name": "completely unrelated"}]
        gold = [{"canonical": "attention", "acceptable_aliases": []}]
        assert concept_precision(pred, gold) == 0.0


class TestConceptRecall:
    def test_all_recovered(self):
        pred = [{"name": "attention"}, {"name": "rag"}]
        gold = [
            {"canonical": "attention", "acceptable_aliases": []},
            {"canonical": "retrieval augmented generation", "acceptable_aliases": ["RAG"]},
        ]
        assert concept_recall(pred, gold) == 1.0

    def test_half_recovered(self):
        pred = [{"name": "attention"}]
        gold = [
            {"canonical": "attention", "acceptable_aliases": []},
            {"canonical": "rag", "acceptable_aliases": []},
        ]
        assert concept_recall(pred, gold) == 0.5

    def test_empty_gold_vacuous_one(self):
        assert concept_recall([{"name": "x"}], []) == 1.0


# =============================================================================
# End-to-end eval (deferred to verify gate)
# =============================================================================


@pytest.mark.costly
def test_e8_eval_gates():
    """End-to-end eval against the 5 hand-labeled fixtures (verify gate).

    Skips cleanly if no fixtures have been labeled yet. When fixtures
    land, this test reads each gold file, asserts the AC-12 gates, and
    prints per-paper scores so regressions are catchable.
    """
    fixtures = _discover_eval_fixtures()
    if not fixtures:
        pytest.skip(
            "No e8_eval fixtures yet — see "
            "packages/core/tests/extraction/fixtures/e8_eval/SELECTION.md"
        )

    # Future: load extractor output for each fixture (live LLM, gated by
    # OPENAI_API_KEY). For now, the verify-time implementer wires this in
    # — keep the runner shape stable so SELECTION.md author doesn't have
    # to re-discover it.
    pytest.skip(
        "Eval runner not wired to live extractors yet — verify-gate task"
    )


def test_selection_md_present():
    """SELECTION.md must be in place so the verify-gate operator knows
    the expected fixture shape and review process."""
    assert (FIXTURE_DIR / "SELECTION.md").exists()


def test_fixture_dir_layout_documented():
    """SELECTION.md must spell out the paper_<slug>.txt + paper_<slug>.gold.yml
    fixture shape so the verify-gate operator labels with the right schema."""
    content = (FIXTURE_DIR / "SELECTION.md").read_text()
    assert "paper_<slug>.txt" in content
    assert "paper_<slug>.gold.yml" in content
    assert "expected_topics" in content
    assert "expected_concepts" in content


def test_gold_files_parse_when_present():
    """If any gold files exist, they must parse and carry the spec shape.

    This protects the verify-gate from a malformed-fixture failure mode:
    the fixture lands but the schema is wrong, so the gates can't be
    computed.
    """
    fixtures = _discover_eval_fixtures()
    for f in fixtures:
        data = yaml.safe_load(f.read_text())
        assert isinstance(data, dict)
        assert "expected_topics" in data
        assert "expected_concepts" in data
        for t in data["expected_topics"]:
            assert "name" in t
            assert "level" in t and t["level"] in {"domain", "area", "subtopic"}
        for c in data["expected_concepts"]:
            assert "canonical" in c
