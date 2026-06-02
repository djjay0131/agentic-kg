"""E-8 Unit 8 — B3 alias heuristic linker.

Covers AC-8: per-paper-aliases-only matching, deny-list, min length,
case insensitivity, whole-word boundary, pollution immunity.
"""

import logging
import uuid
from unittest.mock import MagicMock

from agentic_kg.extraction.b3_linker import link_problems_to_concepts
from agentic_kg.extraction.fixtures.b3_deny_list import DEFAULT_ALIAS_DENY_LIST
from agentic_kg.extraction.schemas import ExtractedResearchConcept


def _mention(
    statement: str,
    quoted: str,
    concept_id: str = "pc-1",
) -> MagicMock:
    """Build a duck-typed mention object with the fields the linker reads."""
    m = MagicMock()
    m.id = f"mention-{uuid.uuid4().hex[:8]}"
    m.statement = statement
    m.quoted_text = quoted
    m.concept_id = concept_id
    return m


class TestLinkProblemsToConcepts:
    def test_match_in_statement(self):
        mention = _mention(
            statement="The attention mechanism dominates the loss landscape.",
            quoted="we use multi-head attention",
        )
        extracted = ExtractedResearchConcept(
            name="attention mechanism",
            aliases=[],
            quoted_text="we use multi-head attention layers",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-attention")],
        )
        assert edges == [(mention.concept_id, "rc-attention")]

    def test_match_in_quoted_text(self):
        mention = _mention(
            statement="The model converges slowly.",
            quoted="our attention mechanism converges in 10 epochs",
        )
        extracted = ExtractedResearchConcept(
            name="attention mechanism",
            quoted_text="grounding text here",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        assert edges == [(mention.concept_id, "rc-1")]

    def test_case_insensitive(self):
        mention = _mention(
            statement="ATTENTION mechanisms struggle here.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="attention", quoted_text="grounding text"
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        assert len(edges) == 1

    def test_whole_word_only_no_substring(self):
        """`graph` in `graphs` would substring-match without \\b boundaries."""
        mention = _mention(
            statement="Graphical models are useful.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="graph", quoted_text="grounding text"
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        assert edges == []

    def test_min_alias_length_filter(self):
        """3-char aliases like 'GNN' are below the default min_alias_length."""
        mention = _mention(statement="GNN-based models", quoted="grounding")
        extracted = ExtractedResearchConcept(
            name="GNN", quoted_text="grounding text for the gnn"
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        assert edges == []

    def test_min_alias_length_overridable(self):
        mention = _mention(statement="GNN-based models", quoted="grounding")
        extracted = ExtractedResearchConcept(
            name="GNN", quoted_text="grounding text"
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
            min_alias_length=3,
        )
        assert edges == [(mention.concept_id, "rc-1")]

    def test_deny_list_blocks_generic_alias(self):
        """`model` is in DEFAULT_ALIAS_DENY_LIST."""
        mention = _mention(
            statement="Our model achieves 95% accuracy.",
            quoted="grounding",
        )
        # Concept name is non-deny ("attention"), but the alias 'model' is
        # denied even though it's length >= 4.
        extracted = ExtractedResearchConcept(
            name="attention",
            aliases=["model"],
            quoted_text="grounding text here",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        # 'attention' itself is not in mention text, and 'model' is denied.
        assert edges == []

    def test_custom_deny_list_overrides_default(self):
        mention = _mention(
            statement="The TransformerXL beats LSTM.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="TransformerXL",
            quoted_text="grounding text",
        )
        custom_deny = DEFAULT_ALIAS_DENY_LIST | {"transformerxl"}
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
            alias_deny_list=custom_deny,
        )
        assert edges == []

    def test_pollution_immunity_no_historical_alias_match(self):
        """AC-8 critical case: a merged concept's accumulated alias list
        from prior papers must NOT trigger matches. The linker takes the
        per-paper extraction object as source-of-truth for surface forms.

        Setup: this paper's extraction only carries the canonical name.
        Even if downstream code knew about prior aliases ('retrieval',
        'RAG'), the linker only sees this paper's per-extraction object.
        """
        mention = _mention(
            statement="We extend retrieval methods to multi-hop queries.",
            quoted="grounding",
        )
        # This paper's extraction does NOT include 'retrieval' or 'RAG'.
        extracted = ExtractedResearchConcept(
            name="retrieval-augmented generation",
            aliases=[],
            quoted_text="grounding text for this paper",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-rag")],
        )
        # The mention text contains 'retrieval' but the extraction does not.
        assert edges == []

    def test_skips_unlinked_mentions(self):
        unlinked = _mention(
            statement="Attention mechanisms are useful.",
            quoted="grounding",
            concept_id=None,
        )
        linked = _mention(
            statement="Attention also helps reasoning.",
            quoted="grounding",
            concept_id="pc-9",
        )
        extracted = ExtractedResearchConcept(
            name="attention", quoted_text="grounding text"
        )
        edges = link_problems_to_concepts(
            mentions=[unlinked, linked],
            paper_extractions=[(extracted, "rc-1")],
        )
        # Only the linked mention generates an edge.
        assert edges == [("pc-9", "rc-1")]

    def test_no_matches_returns_empty(self):
        mention = _mention(
            statement="Nothing about that concept appears here.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="completely unrelated phrase",
            quoted_text="grounding text",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        assert edges == []

    def test_one_mention_matches_multiple_concepts(self):
        mention = _mention(
            statement="The attention mechanism in our retrieval pipeline matters.",
            quoted="grounding",
        )
        ex_a = ExtractedResearchConcept(
            name="attention mechanism",
            quoted_text="grounding text a",
        )
        ex_b = ExtractedResearchConcept(
            name="retrieval pipeline",
            quoted_text="grounding text b",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(ex_a, "rc-a"), (ex_b, "rc-b")],
        )
        assert (mention.concept_id, "rc-a") in edges
        assert (mention.concept_id, "rc-b") in edges
        assert len(edges) == 2

    def test_empty_paper_extractions_returns_empty(self):
        mention = _mention(statement="some text", quoted="grounding")
        edges = link_problems_to_concepts(
            mentions=[mention], paper_extractions=[]
        )
        assert edges == []

    def test_empty_mentions_returns_empty(self):
        ex = ExtractedResearchConcept(
            name="attention", quoted_text="grounding text"
        )
        edges = link_problems_to_concepts(
            mentions=[], paper_extractions=[(ex, "rc-1")]
        )
        assert edges == []

    def test_all_surface_forms_filtered_no_match(self):
        """When the concept's name itself is denied AND aliases are too
        short, the linker produces no edges and does not even attempt to
        match against mention text."""
        mention = _mention(
            statement="Our model is excellent.",
            quoted="grounding",
        )
        # name="model" is denied, alias "AI" is too short.
        extracted = ExtractedResearchConcept(
            name="model deployment",
            aliases=["AI", "model"],
            quoted_text="grounding text here",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
            # min_alias_length=4 so "AI" is filtered; "model" is denied.
            # "model deployment" length is fine but contains "model"... but
            # whole-word match for "model deployment" against statement
            # "Our model is excellent" should NOT fire (no "deployment" word).
        )
        # The full phrase "model deployment" is not in the statement.
        # No surface form would match the statement.
        assert edges == []

    def test_logs_match_with_alias(self, caplog):
        mention = _mention(
            statement="The attention mechanism dominates.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="attention mechanism", quoted_text="grounding text"
        )
        with caplog.at_level(logging.DEBUG, logger="agentic_kg.extraction.b3_linker"):
            link_problems_to_concepts(
                mentions=[mention],
                paper_extractions=[(extracted, "rc-1")],
            )
        # At least one debug log records the match with the alias text.
        debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("attention mechanism" in msg.lower() for msg in debug_msgs)


class TestLinkProblemsToConceptsAcrossConcepts:
    """When two paper_extractions entries merge to the same research_concept_id
    (e.g. the LLM emitted both 'attention mechanism' and 'attention head'
    and the dedup collapsed them), the linker must not emit two identical
    edges for one mention. The cross-extraction dedup is what line 79 of
    b3_linker.py protects.
    """

    def test_same_concept_id_across_extractions_emits_once(self):
        mention = _mention(
            statement="The attention mechanism dominates our pipeline.",
            quoted="grounding",
        )
        # Both extractions resolved to the same merged ResearchConcept.
        # Both ALSO match the mention text — the cross-extraction dedup
        # is what we're exercising.
        ex_a = ExtractedResearchConcept(
            name="attention mechanism", quoted_text="grounding text a"
        )
        ex_b = ExtractedResearchConcept(
            name="pipeline", quoted_text="grounding text b"
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(ex_a, "rc-shared"), (ex_b, "rc-shared")],
        )
        # ex_a matches "attention mechanism" → edge added.
        # ex_b matches "dominates pipeline" → would-be edge is a duplicate.
        # The cross-extraction `seen` set collapses to one entry.
        assert edges == [(mention.concept_id, "rc-shared")]


class TestLinkProblemsToConceptsEmptyCandidate:
    """``_filter_surface_forms`` skips empty strings defensively. An LLM
    that emits an empty-string alias should not crash the linker."""

    def test_empty_string_alias_is_skipped(self):
        mention = _mention(
            statement="Attention mechanism in retrieval.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="attention mechanism",
            aliases=["", "self-attention"],
            quoted_text="grounding text here",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        # Match still fires on the canonical name; empty alias silently
        # skipped (not a crash).
        assert edges == [(mention.concept_id, "rc-1")]


class TestLinkProblemsToConceptsDuplicates:
    """Linker may emit duplicate (pc, rc) pairs if the same mention's text
    contains multiple surface forms of the same concept (e.g. name and
    alias both appear). The integration layer is responsible for the
    idempotent MERGE on write — but the linker should not produce more
    than one edge per (pc, rc) pair to keep audit logs clean.
    """

    def test_same_pair_emitted_once(self):
        mention = _mention(
            statement="Our attention layer and the attention mechanism work.",
            quoted="grounding",
        )
        extracted = ExtractedResearchConcept(
            name="attention layer",
            aliases=["attention mechanism"],
            quoted_text="grounding text",
        )
        edges = link_problems_to_concepts(
            mentions=[mention],
            paper_extractions=[(extracted, "rc-1")],
        )
        # Both surface forms hit, but the (pc, rc) pair is the same.
        assert edges == [(mention.concept_id, "rc-1")]
