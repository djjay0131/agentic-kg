"""E-3 dedup eval — AC-10 precision + recall tripwire gates.

Marked ``costly + integration`` because the real eval requires:
- A live OpenAI embedding service (the dedup decision depends on the
  real embedding distances)
- A Neo4j instance with the schema initialized
- The canonical seed YAML loaded

The test reads the 10-pair hand-labeled fixture, calls
``create_or_merge_model`` for each input, and asserts:
- 10/10 precision (every input lands at its expected canonical name or
  correctly creates a new node when expected is NEW)
- A recall tripwire: at least 6 of the 8 merge-expecting pairs must
  merge (not create new)

Threshold changes must clear both gates — see governance note in the
spec (AC-10, pattern iii).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from agentic_kg.knowledge_graph.seed_models import load_seed_models

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "e3_dedup" / "dedup_pairs.yml"

PRECISION_GATE = 10  # all 10 must resolve correctly
RECALL_TRIPWIRE = 6  # at least 6 of 8 merge-expecting pairs must merge


# =============================================================================
# Pure scoring helpers — exercised by unit tests below
# =============================================================================


def _load_pairs() -> list[dict[str, Any]]:
    return yaml.safe_load(FIXTURE_PATH.read_text())


def evaluate_pair(
    pair: dict[str, Any], merged_name: str | None, created: bool
) -> tuple[bool, str]:
    """Return (correct, explanation). One pair at a time.

    - ``expected: NEW`` is satisfied when ``created=True``.
    - ``expected: <name>`` is satisfied when ``merged_name == name`` AND
      ``created=False``.
    """
    expected = pair["expected"]
    if expected == "NEW":
        if created:
            return True, "correctly created new node"
        return False, f"expected NEW but merged into {merged_name!r}"
    if not created and merged_name == expected:
        return True, f"correctly merged into {expected!r}"
    if created:
        return False, f"expected merge into {expected!r} but created new"
    return False, f"expected {expected!r} but merged into {merged_name!r}"


def scoring_summary(pair_results: list[tuple[dict, bool, str]]) -> dict:
    """Return {precision_passed, merge_count, ...} for AC-10 gates."""
    precision_passed = sum(1 for _, ok, _ in pair_results if ok)
    merge_expecting = [p for p, _, _ in pair_results if p["expected"] != "NEW"]
    merges = sum(
        1
        for p, ok, _ in pair_results
        if p["expected"] != "NEW" and ok
    )
    return {
        "total_pairs": len(pair_results),
        "precision_correct": precision_passed,
        "merge_expecting": len(merge_expecting),
        "merge_count": merges,
    }


# =============================================================================
# Unit tests for the scoring helpers (run by default — no Neo4j / OpenAI)
# =============================================================================


class TestEvaluatePair:
    def test_new_expected_and_created(self):
        ok, _ = evaluate_pair({"expected": "NEW"}, merged_name=None, created=True)
        assert ok is True

    def test_new_expected_but_merged(self):
        ok, _ = evaluate_pair(
            {"expected": "NEW"}, merged_name="BERT", created=False
        )
        assert ok is False

    def test_name_expected_and_merged(self):
        ok, _ = evaluate_pair(
            {"expected": "BERT"}, merged_name="BERT", created=False
        )
        assert ok is True

    def test_name_expected_but_created(self):
        ok, _ = evaluate_pair(
            {"expected": "BERT"}, merged_name="BERT", created=True
        )
        assert ok is False

    def test_name_expected_but_wrong_merge(self):
        ok, _ = evaluate_pair(
            {"expected": "BERT"}, merged_name="GPT-4", created=False
        )
        assert ok is False


class TestScoringSummary:
    def test_all_correct(self):
        pairs = _load_pairs()
        # Synthesize a perfect run: every pair correct.
        results = [
            (p, True, "ok") for p in pairs
        ]
        s = scoring_summary(results)
        assert s["precision_correct"] == PRECISION_GATE
        assert s["merge_count"] == s["merge_expecting"]

    def test_one_false_negative_caught(self):
        pairs = _load_pairs()
        results = []
        flipped = False
        for p in pairs:
            if not flipped and p["expected"] != "NEW":
                results.append((p, False, "missed merge"))
                flipped = True
            else:
                results.append((p, True, "ok"))
        s = scoring_summary(results)
        assert s["precision_correct"] == 9
        assert s["merge_count"] == s["merge_expecting"] - 1


# =============================================================================
# Fixture-shape tests (run by default — protect against bad edits)
# =============================================================================


class TestFixtureShape:
    def test_fixture_loads(self):
        pairs = _load_pairs()
        assert isinstance(pairs, list)
        assert len(pairs) == 10  # AC-10 contract

    def test_every_pair_has_input_and_expected(self):
        for p in _load_pairs():
            assert "input" in p
            assert "expected" in p

    def test_exactly_two_NEW_expecteds(self):
        """8 merge-expecting + 2 NEW = 10. Changing these counts requires
        a spec update (AC-10's 6/8 recall tripwire is tied to the 8)."""
        pairs = _load_pairs()
        new_count = sum(1 for p in pairs if p["expected"] == "NEW")
        assert new_count == 2


# =============================================================================
# Costly + integration — the actual eval (skipped by default)
# =============================================================================


@pytest.mark.costly
@pytest.mark.integration
class TestDedupEvalGate:
    """Real eval against live embeddings + Neo4j. Opt in with ``-m costly``."""

    def test_precision_and_recall_gates(self, neo4j_repository):
        """AC-10: 10/10 precision + 6/8 merge tripwire."""
        load_seed_models(neo4j_repository)

        pair_results: list[tuple[dict, bool, str]] = []
        for pair in _load_pairs():
            model, created = neo4j_repository.create_or_merge_model(
                name=pair["input"]
            )
            ok, explanation = evaluate_pair(
                pair, merged_name=model.name, created=created
            )
            pair_results.append((pair, ok, explanation))
            print(
                f"  [{'PASS' if ok else 'FAIL'}] {pair['input']!r} → "
                f"{model.name!r} (created={created}): {explanation}"
            )

        s = scoring_summary(pair_results)
        # Precision gate.
        assert s["precision_correct"] == PRECISION_GATE, (
            f"precision: {s['precision_correct']}/10 correct (expected 10/10)"
        )
        # Recall tripwire.
        assert s["merge_count"] >= RECALL_TRIPWIRE, (
            f"recall tripwire: {s['merge_count']} of {s['merge_expecting']} "
            f"merged (expected ≥ {RECALL_TRIPWIRE})"
        )
