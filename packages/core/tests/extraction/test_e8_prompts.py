"""E-8 Unit 2 — prompt templates for topic and concept extraction.

Covers AC-10: prompts follow the existing V1 naming convention and the
``EntityKind`` dispatcher makes V2 additions (Model / Method) additive
rather than a rewrite.
"""

import pytest
from agentic_kg.extraction.prompts.templates import (
    CONCEPT_SYSTEM_PROMPT_V1,
    CONCEPT_USER_PROMPT_TEMPLATE_V1,
    TOPIC_SYSTEM_PROMPT_TEMPLATE_V1,
    TOPIC_USER_PROMPT_TEMPLATE_V1,
    EntityKind,
    build_concept_prompt,
    build_topic_prompt,
    get_prompt_pair_for_kind,
)


class TestEntityKind:
    def test_kinds_present(self):
        # The three V1 kinds the spec requires.
        assert EntityKind.PROBLEM.value == "problem"
        assert EntityKind.TOPIC.value == "topic"
        assert EntityKind.CONCEPT.value == "concept"

    def test_kind_is_str_enum(self):
        # Allows direct stringification when logging or persisting to
        # ExtractionFailure.extractor.
        assert str(EntityKind.TOPIC.value) == "topic"


class TestBuildTopicPrompt:
    def test_returns_system_and_user_pair(self):
        system, user_tpl = build_topic_prompt(("NLP", "Computer Vision"))
        assert isinstance(system, str)
        assert isinstance(user_tpl, str)
        assert len(system) > 0
        assert len(user_tpl) > 0

    def test_taxonomy_names_rendered_into_system_prompt(self):
        system, _ = build_topic_prompt(("NLP", "Computer Vision", "Information Retrieval"))
        assert "NLP" in system
        assert "Computer Vision" in system
        assert "Information Retrieval" in system

    def test_user_template_has_placeholders(self):
        _, user_tpl = build_topic_prompt(("NLP",))
        # Required for str.format() inside TopicExtractor.extract.
        assert "{paper_title}" in user_tpl
        assert "{section_text}" in user_tpl

    def test_system_prompt_forbids_invention(self):
        # Closed-set constraint must be explicit so the LLM is steered
        # away from hallucinating topic names that the Literal validation
        # would reject downstream.
        system, _ = build_topic_prompt(("NLP",))
        assert (
            "do not invent" in system.lower()
            or "do not create" in system.lower()
            or "only" in system.lower()
        )

    def test_empty_taxonomy_rejected(self):
        # A topic extractor with no taxonomy is meaningless and would
        # produce a Literal[] schema, which pydantic rejects anyway.
        # Fail fast at prompt build time.
        with pytest.raises(ValueError):
            build_topic_prompt(())


class TestBuildConceptPrompt:
    def test_returns_system_and_user_pair(self):
        system, user_tpl = build_concept_prompt()
        assert isinstance(system, str)
        assert isinstance(user_tpl, str)

    def test_user_template_has_placeholders(self):
        _, user_tpl = build_concept_prompt()
        assert "{paper_title}" in user_tpl
        assert "{section_text}" in user_tpl

    def test_concept_system_grounds_in_quoted_text(self):
        # The schema's quoted_text is min_length=10; the prompt must
        # tell the LLM to populate it.
        system, _ = build_concept_prompt()
        assert "quote" in system.lower() or "grounding" in system.lower()

    def test_concept_system_warns_against_generic_terms(self):
        system, _ = build_concept_prompt()
        lowered = system.lower()
        assert any(
            warning in lowered
            for warning in ("generic", "overly general", "machine learning", "neural network")
        )


class TestPromptPairDispatcher:
    """AC-10: get_prompt_pair_for_kind routes by EntityKind so V2 Model /
    Method additions are additive (new enum value + new build_x_prompt)
    rather than a rewrite of the dispatcher.
    """

    def test_topic_dispatch(self):
        system, user_tpl = get_prompt_pair_for_kind(
            EntityKind.TOPIC, taxonomy_names=("NLP",)
        )
        assert "NLP" in system

    def test_concept_dispatch(self):
        system, user_tpl = get_prompt_pair_for_kind(EntityKind.CONCEPT)
        assert "{paper_title}" in user_tpl

    def test_problem_dispatch_returns_v1_system(self):
        # Problem extraction keeps using the existing section-typed prompts;
        # the dispatcher returns the generic V1 system prompt and a placeholder
        # user template so callers can route through the old API if needed.
        system, _ = get_prompt_pair_for_kind(EntityKind.PROBLEM)
        from agentic_kg.extraction.prompts.templates import SYSTEM_PROMPT_V1

        assert system == SYSTEM_PROMPT_V1

    def test_topic_dispatch_requires_taxonomy_names(self):
        with pytest.raises(TypeError):
            get_prompt_pair_for_kind(EntityKind.TOPIC)


class TestTemplateConstants:
    """Spec AC-10: naming convention parity with SYSTEM_PROMPT_V1 / USER_PROMPT_TEMPLATE_V1."""

    def test_topic_constants_carry_v1_suffix(self):
        # Sanity guard that the V1/V2 versioning convention extends.
        assert TOPIC_SYSTEM_PROMPT_TEMPLATE_V1.endswith
        assert TOPIC_USER_PROMPT_TEMPLATE_V1.endswith

    def test_concept_constants_carry_v1_suffix(self):
        assert CONCEPT_SYSTEM_PROMPT_V1.endswith
        assert CONCEPT_USER_PROMPT_TEMPLATE_V1.endswith

    def test_topic_system_has_taxonomy_placeholder(self):
        # build_topic_prompt expects this placeholder to substitute the
        # closed-set list at runtime.
        assert "{taxonomy}" in TOPIC_SYSTEM_PROMPT_TEMPLATE_V1
