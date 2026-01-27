"""
Prompt templates for LLM-based extraction.

This module provides versioned prompt templates for extracting
structured research problems from academic papers.
"""

from agentic_kg.extraction.prompts.templates import (
    ExtractionPrompt,
    PromptTemplate,
    get_extraction_prompt,
    get_system_prompt,
)

__all__ = [
    "PromptTemplate",
    "ExtractionPrompt",
    "get_system_prompt",
    "get_extraction_prompt",
]
