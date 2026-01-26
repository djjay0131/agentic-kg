"""
Information Extraction Pipeline for the Agentic Knowledge Graph.

This module provides tools for extracting structured research problems
from scientific papers, including:

- PDF text extraction
- Section segmentation
- LLM-based structured extraction
- Provenance tracking
"""

from agentic_kg.extraction.pdf_extractor import (
    ExtractedPage,
    ExtractedText,
    PDFExtractionError,
    PDFExtractor,
    get_pdf_extractor,
)
from agentic_kg.extraction.section_segmenter import (
    Section,
    SectionSegmenter,
    SectionType,
    SegmentedDocument,
    get_section_segmenter,
)
from agentic_kg.extraction.llm_client import (
    AnthropicClient,
    BaseLLMClient,
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    OpenAIClient,
    TokenUsage,
    create_llm_client,
    get_anthropic_client,
    get_openai_client,
)
from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedAssumption,
    ExtractedBaseline,
    ExtractedConstraint,
    ExtractedDataset,
    ExtractedMetric,
    ExtractedProblem,
    ExtractionResult,
    extracted_to_kg_problem,
)
from agentic_kg.extraction.prompts import (
    ExtractionPrompt,
    PromptTemplate,
    get_extraction_prompt,
    get_system_prompt,
)

__all__ = [
    # PDF extraction
    "PDFExtractor",
    "ExtractedText",
    "ExtractedPage",
    "PDFExtractionError",
    "get_pdf_extractor",
    # Section segmentation
    "SectionSegmenter",
    "Section",
    "SectionType",
    "SegmentedDocument",
    "get_section_segmenter",
    # LLM client
    "BaseLLMClient",
    "OpenAIClient",
    "AnthropicClient",
    "LLMConfig",
    "LLMProvider",
    "LLMResponse",
    "TokenUsage",
    "LLMError",
    "LLMRateLimitError",
    "create_llm_client",
    "get_openai_client",
    "get_anthropic_client",
    # Extraction schemas
    "ExtractedProblem",
    "ExtractedAssumption",
    "ExtractedConstraint",
    "ExtractedDataset",
    "ExtractedMetric",
    "ExtractedBaseline",
    "ExtractionResult",
    "BatchExtractionResult",
    "extracted_to_kg_problem",
    # Prompts
    "PromptTemplate",
    "ExtractionPrompt",
    "get_system_prompt",
    "get_extraction_prompt",
]
