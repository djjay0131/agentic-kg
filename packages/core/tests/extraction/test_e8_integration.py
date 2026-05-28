"""E-8 Unit 11 — integrate_paper_entities writer (mocked-repo unit tests).

Covers AC-5b (extraction_incomplete flag), AC-6 (BELONGS_TO edges),
AC-7 (DISCUSSES + concept dedup), AC-15 (Paper.taxonomy_hash).

Integration coverage with a live Neo4j happens during Phase 6
verification; these unit tests pin the wiring contract — which repo
methods get called, with what args, in what order.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction.kg_integration_v2 import (
    EntityIntegrationResult,
    integrate_paper_entities,
)
from agentic_kg.extraction.pipeline import (
    ExtractionFailure,
    PaperExtractionResult,
)
from agentic_kg.extraction.schemas import (
    ExtractedResearchConcept,
    _ExtractedTopicAssignmentBase,
)

# =============================================================================
# Mock repo + factories
# =============================================================================


@pytest.fixture
def mock_repo():
    repo = MagicMock()

    # get_topic_by_name returns a stub Topic for any closed-set name.
    def _get_topic_by_name(name: str):
        topic = MagicMock()
        topic.id = f"topic-{name.lower().replace(' ', '-')}"
        topic.name = name
        return topic

    repo.get_topic_by_name.side_effect = _get_topic_by_name

    # create_or_merge_research_concept returns (concept, created) — the
    # concept always has a deterministic id derived from the name.
    def _merge_concept(name, description=None, aliases=None, **_):
        concept = MagicMock()
        concept.id = f"rc-{name.lower().replace(' ', '-')}"
        concept.name = name
        return concept, True

    repo.create_or_merge_research_concept.side_effect = _merge_concept

    repo.assign_entity_to_topic.return_value = True
    repo.link_paper_to_concept.return_value = True
    repo.link_problem_to_concept.return_value = True

    # session() is also called for the Paper metadata SET.
    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: None
    session.run.return_value = MagicMock()
    repo.session.return_value = session
    return repo


def _topic(name: str, level: str = "area", confidence: float = 0.9):
    # Build a stub assignment using a dynamic subclass so topic_name is set.
    class _Assignment(_ExtractedTopicAssignmentBase):
        topic_name: str = "default"

    return _Assignment(topic_name=name, level=level, confidence=confidence)


def _concept(name: str, confidence: float = 0.9, aliases: list[str] = None):
    return ExtractedResearchConcept(
        name=name,
        aliases=aliases or [],
        quoted_text="grounding text here for this concept",
        confidence=confidence,
    )


def _mention(concept_id: str, statement: str = "Default mention statement here."):
    m = MagicMock()
    m.concept_id = concept_id
    m.statement = statement
    m.quoted_text = "grounding"
    m.id = f"mention-for-{concept_id}"
    return m


# =============================================================================
# Topic edges (AC-6)
# =============================================================================


class TestTopicEdgeWriting:
    def test_belongs_to_edge_written_per_topic(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[_topic("NLP"), _topic("Computer Vision")],
                concepts=[],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="abc123",
            repo=mock_repo,
        )
        assert mock_repo.assign_entity_to_topic.call_count == 2
        calls = mock_repo.assign_entity_to_topic.call_args_list
        topic_ids_called = {c.kwargs["topic_id"] for c in calls}
        assert "topic-nlp" in topic_ids_called
        assert "topic-computer-vision" in topic_ids_called
        # All calls used the paper DOI as entity_id, labeled as Paper.
        for c in calls:
            assert c.kwargs["entity_id"] == "10.1/abc"
            assert c.kwargs["entity_label"] == "Paper"

        assert result.topics_assigned == 2

    def test_low_confidence_topic_dropped(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[_topic("NLP", confidence=0.5)],
                concepts=[],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.assign_entity_to_topic.assert_not_called()

    def test_unknown_topic_name_skipped_not_crashed(self, mock_repo):
        # Simulate get_topic_by_name raising NotFoundError for a mid-batch
        # taxonomy mutation.
        from agentic_kg.knowledge_graph.repository import NotFoundError

        def _raise_for_x(name):
            if name == "Gone":
                raise NotFoundError("Topic not found")
            t = MagicMock()
            t.id = f"topic-{name.lower()}"
            return t

        mock_repo.get_topic_by_name.side_effect = _raise_for_x

        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[_topic("NLP"), _topic("Gone")],
                concepts=[],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        # NLP's edge gets written, Gone is skipped, integration continues.
        assert mock_repo.assign_entity_to_topic.call_count == 1
        assert result.topics_assigned == 1


# =============================================================================
# Concept edges (AC-7)
# =============================================================================


class TestConceptEdgeWriting:
    def test_concept_merged_and_discusses_edge_written(self, mock_repo):
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[_concept("attention mechanism")],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.create_or_merge_research_concept.assert_called_once()
        mock_repo.link_paper_to_concept.assert_called_once_with(
            paper_doi="10.1/abc",
            research_concept_id="rc-attention-mechanism",
        )
        assert result.concepts_linked == 1

    def test_low_confidence_concept_dropped(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[_concept("attention", confidence=0.5)],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.create_or_merge_research_concept.assert_not_called()

    def test_concept_exactly_at_threshold_kept(self, mock_repo):
        """Adversarial: confidence exactly at the default 0.7 must pass —
        the comparison is ``<`` (strict), so 0.7 is in-bounds."""
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[_concept("attention", confidence=0.7)],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.create_or_merge_research_concept.assert_called_once()

    def test_merged_concept_still_counts_as_linked(self, mock_repo):
        """Adversarial: when create_or_merge returns (concept, created=False),
        the DISCUSSES edge is still written and concepts_linked still ticks.
        Otherwise dedupes would underreport graph activity."""
        existing = MagicMock()
        existing.id = "rc-existing"
        mock_repo.create_or_merge_research_concept.side_effect = None
        mock_repo.create_or_merge_research_concept.return_value = (existing, False)

        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[_concept("attention")],
                failures=[],
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        assert result.concepts_linked == 1
        mock_repo.link_paper_to_concept.assert_called_once_with(
            paper_doi="10.1/abc",
            research_concept_id="rc-existing",
        )


# =============================================================================
# B3 wiring (AC-8 — driven by linker, AC-7 follow-on)
# =============================================================================


class TestB3WiringFromIntegration:
    def test_problem_to_concept_edges_drawn(self, mock_repo):
        # The mention text contains "attention" — the per-paper alias would match.
        result = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[
                    _concept(
                        "attention mechanism",
                        aliases=["self-attention"],
                    )
                ],
                failures=[],
            ),
            mentions=[
                _mention(
                    concept_id="pc-1",
                    statement="The attention mechanism dominates inference time.",
                )
            ],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.link_problem_to_concept.assert_called_once_with(
            problem_concept_id="pc-1",
            research_concept_id="rc-attention-mechanism",
        )
        assert result.problem_concept_edges_drawn == 1

    def test_mention_without_match_no_edge(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[],
                topics=[],
                concepts=[_concept("attention")],
                failures=[],
            ),
            mentions=[_mention(concept_id="pc-1", statement="No matching keywords here.")],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        mock_repo.link_problem_to_concept.assert_not_called()


# =============================================================================
# Partial-extraction flag + taxonomy hash (AC-5b, AC-15)
# =============================================================================


class TestPaperMetadataWrite:
    def test_extraction_incomplete_flag_set_on_failure(self, mock_repo):
        failure = ExtractionFailure(
            extractor="topic",
            exception_type="TimeoutError",
            message="t",
            traceback="trace",
            occurred_at=datetime.now(timezone.utc),
        )
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[], topics=[], concepts=[], failures=[failure]
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        # Paper SET was issued.
        session = mock_repo.session.return_value
        run_calls = session.run.call_args_list
        cypher_used = " ".join(c.args[0] for c in run_calls if c.args)
        assert "extraction_incomplete" in cypher_used

    def test_no_failures_clears_incomplete_flag(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[], topics=[], concepts=[], failures=[]
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        session = mock_repo.session.return_value
        # The flag is parameterized (not literal in the Cypher) — sound
        # practice, and the parameter value carries the cleared state.
        params_used = {}
        for c in session.run.call_args_list:
            params_used.update(c.kwargs)
        assert params_used.get("extraction_incomplete") is False
        # Empty extractor list (no failures) ⇒ empty string.
        assert params_used.get("extraction_failed_extractors") == ""

    def test_taxonomy_hash_written(self, mock_repo):
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[], topics=[], concepts=[], failures=[]
            ),
            mentions=[],
            taxonomy_hash="abc123def456",
            repo=mock_repo,
        )
        session = mock_repo.session.return_value
        cypher_used = " ".join(c.args[0] for c in session.run.call_args_list if c.args)
        assert "taxonomy_hash" in cypher_used
        # The hash value was passed as a parameter (kwargs in run()).
        params_used = {}
        for c in session.run.call_args_list:
            params_used.update(c.kwargs)
        assert params_used.get("taxonomy_hash") == "abc123def456"

    def test_failed_extractor_names_serialized(self, mock_repo):
        failures = [
            ExtractionFailure(
                extractor="topic",
                exception_type="TimeoutError",
                message="t",
                traceback="trace",
                occurred_at=datetime.now(timezone.utc),
            ),
            ExtractionFailure(
                extractor="concept",
                exception_type="RuntimeError",
                message="r",
                traceback="trace",
                occurred_at=datetime.now(timezone.utc),
            ),
        ]
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[], topics=[], concepts=[], failures=failures
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        session = mock_repo.session.return_value
        params_used = {}
        for c in session.run.call_args_list:
            params_used.update(c.kwargs)
        # Names stored as a sortable, audit-friendly string.
        assert "topic" in params_used.get("extraction_failed_extractors", "")
        assert "concept" in params_used.get("extraction_failed_extractors", "")


# =============================================================================
# Result object
# =============================================================================


class TestEntityIntegrationResult:
    def test_counts_default_zero(self):
        r = EntityIntegrationResult(paper_doi="10.1/abc")
        assert r.topics_assigned == 0
        assert r.concepts_linked == 0
        assert r.problem_concept_edges_drawn == 0
        assert r.paper_marked_incomplete is False

    def test_marked_incomplete_when_failures_present(self, mock_repo):
        failure = ExtractionFailure(
            extractor="topic",
            exception_type="TimeoutError",
            message="t",
            traceback="trace",
            occurred_at=datetime.now(timezone.utc),
        )
        r = integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(
                problems=[], topics=[], concepts=[], failures=[failure]
            ),
            mentions=[],
            taxonomy_hash="h",
            repo=mock_repo,
        )
        assert r.paper_marked_incomplete is True
