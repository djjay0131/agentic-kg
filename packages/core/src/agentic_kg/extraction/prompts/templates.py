"""
Prompt Templates for Research Problem Extraction.

Provides versioned prompt templates for extracting structured research
problems from academic paper sections. Templates are designed for use
with the instructor library for structured LLM output.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agentic_kg.extraction.section_segmenter import SectionType


class PromptVersion(str, Enum):
    """Version identifiers for prompt templates."""

    V1 = "v1"  # Basic extraction
    V2 = "v2"  # Chain-of-thought reasoning (future)


@dataclass
class PromptTemplate:
    """A versioned prompt template."""

    version: PromptVersion
    system_prompt: str
    user_prompt_template: str
    description: str


# System prompt for research problem extraction
SYSTEM_PROMPT_V1 = """You are an expert research scientist specialized in extracting structured information from academic papers.

Your task is to identify and extract research problems, limitations, and open questions from paper text. For each problem you identify, you should extract:

1. **Problem Statement**: A clear, concise statement of the research problem or limitation
2. **Domain**: The research domain or field (e.g., "Natural Language Processing", "Computer Vision")
3. **Assumptions**: Any assumptions underlying the problem (explicit or implicit)
4. **Constraints**: Practical constraints affecting the problem (computational, data, methodological)
5. **Datasets**: Any datasets mentioned as relevant to the problem
6. **Metrics**: Evaluation metrics mentioned for measuring progress
7. **Baselines**: Baseline methods or current state-of-the-art mentioned

Guidelines:
- Focus on ACTIONABLE research problems that could be worked on
- Prioritize problems that are EXPLICIT in the text over inferred ones
- Include the exact quoted text that supports each problem
- Assign confidence scores based on how clearly the problem is stated
- A section may contain zero, one, or multiple distinct problems
- Do NOT hallucinate problems that are not supported by the text

Output Format:
Return a structured list of problems following the provided schema exactly."""


# User prompt templates for different section types
USER_PROMPT_TEMPLATE_V1 = """Extract research problems from the following {section_type} section of an academic paper.

Paper Title: {paper_title}
{author_info}
Section: {section_type}

---
TEXT TO ANALYZE:
{section_text}
---

Instructions:
1. Read the text carefully and identify any research problems, limitations, or open questions
2. For each problem, extract all available structured information
3. Include the exact quoted text that supports each problem identification
4. If no clear problems are found, return an empty list

Remember:
- Only extract problems that are EXPLICITLY stated or CLEARLY implied
- Focus on problems that are actionable for future research
- Assign appropriate confidence scores (0.0-1.0)"""


# Section-specific prompt variations
SECTION_PROMPTS = {
    SectionType.LIMITATIONS: """Extract research problems from the LIMITATIONS section below.

This section typically contains explicit acknowledgments of:
- Weaknesses in the current approach
- Scope limitations of the study
- Assumptions that may not hold
- Areas where the method underperforms

Focus on extracting problems that future work could address.

Paper Title: {paper_title}
{author_info}

---
LIMITATIONS SECTION TEXT:
{section_text}
---

Extract all limitations as structured research problems. Each limitation should be framed as an open problem that future research could tackle.""",
    SectionType.FUTURE_WORK: """Extract research problems from the FUTURE WORK section below.

This section typically contains explicit statements about:
- Proposed extensions to the current work
- Open questions the authors want to investigate
- Potential improvements suggested
- New directions enabled by this work

Paper Title: {paper_title}
{author_info}

---
FUTURE WORK SECTION TEXT:
{section_text}
---

Extract all future work items as structured research problems. These are typically high-quality problem statements since authors explicitly identify them as open.""",
    SectionType.DISCUSSION: """Extract research problems from the DISCUSSION section below.

Discussion sections may contain:
- Analysis of where the method fails
- Comparison gaps with other approaches
- Theoretical questions raised by results
- Practical deployment challenges

Paper Title: {paper_title}
{author_info}

---
DISCUSSION SECTION TEXT:
{section_text}
---

Look for implicit and explicit problems mentioned during the analysis. Focus on actionable research directions.""",
    SectionType.CONCLUSION: """Extract research problems from the CONCLUSION section below.

Conclusions often briefly mention:
- Key limitations of the work
- Suggested future directions
- Open questions for the field

Paper Title: {paper_title}
{author_info}

---
CONCLUSION SECTION TEXT:
{section_text}
---

Extract problems mentioned, but note that conclusions are typically summaries, so problems may be stated briefly.""",
    SectionType.INTRODUCTION: """Extract research problems from the INTRODUCTION section below.

Introductions typically frame:
- The main problem the paper addresses
- Gaps in existing approaches
- Challenges in the field
- Motivation for the work

Paper Title: {paper_title}
{author_info}

---
INTRODUCTION SECTION TEXT:
{section_text}
---

Focus on problems that remain open after this paper's contribution. The paper may solve some problems but leave others open.""",
}


@dataclass
class ExtractionPrompt:
    """A formatted extraction prompt ready for LLM consumption."""

    system_prompt: str
    user_prompt: str
    version: PromptVersion
    section_type: SectionType
    paper_title: str


def get_system_prompt(version: PromptVersion = PromptVersion.V1) -> str:
    """
    Get the system prompt for a given version.

    Args:
        version: Prompt version to use.

    Returns:
        System prompt string.
    """
    if version == PromptVersion.V1:
        return SYSTEM_PROMPT_V1

    raise ValueError(f"Unknown prompt version: {version}")


def get_extraction_prompt(
    section_text: str,
    section_type: SectionType,
    paper_title: str,
    authors: Optional[list[str]] = None,
    version: PromptVersion = PromptVersion.V1,
) -> ExtractionPrompt:
    """
    Get a formatted extraction prompt for a section.

    Args:
        section_text: The text content to extract from.
        section_type: Type of section being extracted.
        paper_title: Title of the paper.
        authors: List of author names (optional).
        version: Prompt version to use.

    Returns:
        ExtractionPrompt ready for LLM consumption.
    """
    # Format author info
    author_info = ""
    if authors:
        author_info = f"Authors: {', '.join(authors)}"

    # Get section-specific prompt or default
    if section_type in SECTION_PROMPTS:
        user_template = SECTION_PROMPTS[section_type]
    else:
        user_template = USER_PROMPT_TEMPLATE_V1

    # Format the user prompt
    user_prompt = user_template.format(
        section_type=section_type.value.replace("_", " ").title(),
        paper_title=paper_title,
        author_info=author_info,
        section_text=section_text,
    )

    return ExtractionPrompt(
        system_prompt=get_system_prompt(version),
        user_prompt=user_prompt,
        version=version,
        section_type=section_type,
        paper_title=paper_title,
    )


# Few-shot examples for improved extraction (future use)
EXTRACTION_EXAMPLES = [
    {
        "input": """Limitations

Our approach has several limitations. First, the model requires
significant computational resources, making it impractical for
deployment on edge devices. Second, we only evaluated on English
datasets, and performance on other languages is unknown. Third,
the model struggles with very long documents exceeding 10,000 tokens.""",
        "output": {
            "problems": [
                {
                    "statement": "Current deep learning models require significant computational resources, making them impractical for deployment on edge devices or resource-constrained environments.",
                    "domain": "Machine Learning / Edge Computing",
                    "constraints": [
                        {
                            "text": "High computational requirements prevent edge deployment",
                            "type": "computational",
                        }
                    ],
                    "quoted_text": "the model requires significant computational resources, making it impractical for deployment on edge devices",
                    "confidence": 0.95,
                },
                {
                    "statement": "Model performance on non-English languages is unknown and likely degraded compared to English.",
                    "domain": "Multilingual NLP",
                    "constraints": [
                        {"text": "Only evaluated on English datasets", "type": "data"}
                    ],
                    "quoted_text": "we only evaluated on English datasets, and performance on other languages is unknown",
                    "confidence": 0.90,
                },
                {
                    "statement": "Current approach struggles with processing very long documents, with performance degrading beyond 10,000 tokens.",
                    "domain": "Long Document Processing",
                    "constraints": [
                        {"text": "Document length limited to ~10,000 tokens", "type": "methodological"}
                    ],
                    "quoted_text": "the model struggles with very long documents exceeding 10,000 tokens",
                    "confidence": 0.85,
                },
            ]
        },
    }
]
