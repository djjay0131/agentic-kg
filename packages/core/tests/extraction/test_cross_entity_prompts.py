"""E-7 Unit 2 — Disambiguation prompt constants.

Covers the prompt-injection mitigation contract (AC-20) and the
delimiter / security clause structure.
"""

from agentic_kg.extraction.prompts.templates import (
    DISAMBIGUATION_SYSTEM_PROMPT_V1,
    DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1,
    build_disambiguation_prompt,
)


class TestSystemPrompt:
    def test_security_clause_present(self):
        """AC-20: system prompt must call out untrusted-data semantics
        for the paper excerpt block."""
        text = DISAMBIGUATION_SYSTEM_PROMPT_V1.upper()
        assert "SECURITY" in text or "UNTRUSTED" in text
        # The mitigation must explicitly forbid following embedded
        # instructions.
        assert "DO NOT FOLLOW" in text

    def test_definitions_present(self):
        text = DISAMBIGUATION_SYSTEM_PROMPT_V1
        # Each kind name must appear in the definitions section.
        assert "ResearchConcept" in text
        assert "Model" in text
        assert "Method" in text

    def test_rejection_paths_documented(self):
        text = DISAMBIGUATION_SYSTEM_PROMPT_V1
        # System prompt instructs the LLM to use the rejection_reason
        # field when bots gates can't be cleared.
        assert "rejection_reason" in text


class TestUserPromptTemplate:
    def test_pseudo_xml_delimiters_present(self):
        """AC-20: the paper excerpt must be wrapped in pseudo-XML
        delimiters so the system prompt clause can reference them."""
        assert "<paper-excerpt>" in DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1
        assert "</paper-excerpt>" in DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1

    def test_carries_placeholders(self):
        text = DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1
        assert "{paper_title}" in text
        assert "{surface}" in text
        assert "{kinds_block}" in text
        assert "{paper_excerpt}" in text


class TestBuilder:
    def test_returns_pair(self):
        s, u = build_disambiguation_prompt()
        assert s == DISAMBIGUATION_SYSTEM_PROMPT_V1
        assert u == DISAMBIGUATION_USER_PROMPT_TEMPLATE_V1
