"""E-8 V2 Unit 2 — Model + Method prompts + EntityKind dispatch.

Covers prompt constants, builder factories, EntityKind extension, and
dispatcher routing.
"""

from agentic_kg.extraction.prompts.templates import (
    METHOD_SYSTEM_PROMPT_V1,
    METHOD_USER_PROMPT_TEMPLATE_V1,
    MODEL_SYSTEM_PROMPT_V1,
    MODEL_USER_PROMPT_TEMPLATE_V1,
    EntityKind,
    build_method_prompt,
    build_model_prompt,
    get_prompt_pair_for_kind,
)


class TestEntityKindExtension:
    def test_model_kind_present(self):
        assert EntityKind.MODEL.value == "model"

    def test_method_kind_present(self):
        assert EntityKind.METHOD.value == "method"

    def test_v1_kinds_preserved(self):
        # AC-15: V1 enum members are unchanged.
        assert EntityKind.PROBLEM.value == "problem"
        assert EntityKind.TOPIC.value == "topic"
        assert EntityKind.CONCEPT.value == "concept"


class TestModelPromptConstants:
    def test_system_prompt_warns_against_generic_terms(self):
        # The V1 ConceptExtractor system prompt has a similar negative-rule
        # block ("Do not extract overly general terms..."). Mirror that
        # signal here so a future prompt-rework can be caught by an audit.
        assert "transformer architecture" in MODEL_SYSTEM_PROMPT_V1.lower()
        assert "not models" in MODEL_SYSTEM_PROMPT_V1.lower()

    def test_user_template_carries_placeholders(self):
        assert "{paper_title}" in MODEL_USER_PROMPT_TEMPLATE_V1
        assert "{section_text}" in MODEL_USER_PROMPT_TEMPLATE_V1


class TestMethodPromptConstants:
    def test_system_prompt_warns_against_generic_terms(self):
        text = METHOD_SYSTEM_PROMPT_V1.lower()
        # Negative-rule block must call out at least one generic activity
        # as a non-method, and use "do not" framing.
        assert "training" in text
        assert "do not" in text

    def test_user_template_carries_placeholders(self):
        assert "{paper_title}" in METHOD_USER_PROMPT_TEMPLATE_V1
        assert "{section_text}" in METHOD_USER_PROMPT_TEMPLATE_V1


class TestBuilders:
    def test_build_model_prompt_returns_pair(self):
        s, u = build_model_prompt()
        assert s == MODEL_SYSTEM_PROMPT_V1
        assert u == MODEL_USER_PROMPT_TEMPLATE_V1

    def test_build_method_prompt_returns_pair(self):
        s, u = build_method_prompt()
        assert s == METHOD_SYSTEM_PROMPT_V1
        assert u == METHOD_USER_PROMPT_TEMPLATE_V1


class TestDispatcherExtension:
    def test_dispatcher_routes_model(self):
        s, u = get_prompt_pair_for_kind(EntityKind.MODEL)
        assert s == MODEL_SYSTEM_PROMPT_V1
        assert u == MODEL_USER_PROMPT_TEMPLATE_V1

    def test_dispatcher_routes_method(self):
        s, u = get_prompt_pair_for_kind(EntityKind.METHOD)
        assert s == METHOD_SYSTEM_PROMPT_V1
        assert u == METHOD_USER_PROMPT_TEMPLATE_V1

    def test_v1_dispatch_unchanged(self):
        # AC-15: existing CONCEPT route still works.
        from agentic_kg.extraction.prompts.templates import (
            CONCEPT_SYSTEM_PROMPT_V1,
            CONCEPT_USER_PROMPT_TEMPLATE_V1,
        )
        s, u = get_prompt_pair_for_kind(EntityKind.CONCEPT)
        assert s == CONCEPT_SYSTEM_PROMPT_V1
        assert u == CONCEPT_USER_PROMPT_TEMPLATE_V1
