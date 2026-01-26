"""
Relationship extractor for research problems.

This module extracts relationships between problems using:
- Textual cue detection (explicit relationship markers)
- Semantic similarity-based inference
- Citation-based connections
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.llm_client import BaseLLMClient, LLMResponse
from agentic_kg.extraction.schemas import ExtractedProblem


class RelationType(str, Enum):
    """Types of relationships between problems."""

    EXTENDS = "extends"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    REFRAMES = "reframes"
    RELATED_TO = "related_to"
    SUPERSEDES = "supersedes"
    SPECIALIZES = "specializes"
    GENERALIZES = "generalizes"


# Textual cues for each relation type
RELATION_CUES: dict[RelationType, list[str]] = {
    RelationType.EXTENDS: [
        "builds on",
        "extends",
        "further explores",
        "advances",
        "expands upon",
        "improves upon",
        "enhances",
        "augments",
        "goes beyond",
        "taking further",
    ],
    RelationType.CONTRADICTS: [
        "conflicts with",
        "challenges",
        "contrary to",
        "contradicts",
        "opposes",
        "in contrast to",
        "disputes",
        "refutes",
        "questions",
        "undermines",
    ],
    RelationType.DEPENDS_ON: [
        "requires",
        "prerequisite",
        "depends on",
        "relies on",
        "assumes",
        "presupposes",
        "contingent on",
        "needs",
        "based on",
        "building upon",
    ],
    RelationType.REFRAMES: [
        "redefines",
        "alternative view",
        "new perspective",
        "reconceptualizes",
        "reformulates",
        "recasts",
        "reinterprets",
        "different framing",
        "another way to view",
        "alternative formulation",
    ],
    RelationType.SUPERSEDES: [
        "replaces",
        "supersedes",
        "obsoletes",
        "makes obsolete",
        "renders unnecessary",
        "subsumes",
        "encompasses",
    ],
    RelationType.SPECIALIZES: [
        "specializes",
        "focuses on",
        "narrows",
        "specific case of",
        "particular instance",
        "special case",
        "restricted to",
    ],
    RelationType.GENERALIZES: [
        "generalizes",
        "broader than",
        "extends to",
        "applies more broadly",
        "more general form",
        "abstracts",
        "wider scope",
    ],
}


@dataclass
class RelationConfig:
    """Configuration for relationship extraction."""

    # Minimum confidence for relation detection
    min_confidence: float = 0.5

    # Similarity threshold for semantic inference
    similarity_threshold: float = 0.7

    # Whether to use LLM for relation validation
    use_llm_validation: bool = True

    # Maximum relations to extract per problem pair
    max_relations_per_pair: int = 3

    # Whether to extract bidirectional relations
    extract_bidirectional: bool = True


class ExtractedRelation(BaseModel):
    """A relationship between two problems."""

    source_problem_id: str = Field(
        ..., description="ID or statement of source problem"
    )
    target_problem_id: str = Field(
        ..., description="ID or statement of target problem"
    )
    relation_type: RelationType = Field(..., description="Type of relationship")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score for the relation"
    )
    evidence: str = Field(
        ...,
        min_length=10,
        description="Text evidence supporting this relation",
    )
    extraction_method: str = Field(
        default="textual_cue",
        description="How the relation was extracted",
    )


class RelationExtractionResult(BaseModel):
    """Result of relation extraction."""

    relations: list[ExtractedRelation] = Field(default_factory=list)
    source_text: Optional[str] = Field(
        default=None, description="Text analyzed for relations"
    )

    @property
    def relation_count(self) -> int:
        """Number of relations extracted."""
        return len(self.relations)

    def get_by_type(self, relation_type: RelationType) -> list[ExtractedRelation]:
        """Get relations of a specific type."""
        return [r for r in self.relations if r.relation_type == relation_type]

    def get_for_problem(self, problem_id: str) -> list[ExtractedRelation]:
        """Get all relations involving a problem."""
        return [
            r
            for r in self.relations
            if r.source_problem_id == problem_id or r.target_problem_id == problem_id
        ]


class LLMRelationResult(BaseModel):
    """LLM extraction result for relations."""

    relations: list[ExtractedRelation] = Field(default_factory=list)
    reasoning: Optional[str] = Field(
        default=None, description="Reasoning for relation detection"
    )


@dataclass
class RelationExtractor:
    """
    Extracts relationships between research problems.

    Uses textual cue detection, semantic similarity, and optional
    LLM validation to identify relationships.
    """

    client: Optional[BaseLLMClient] = None
    config: RelationConfig = field(default_factory=RelationConfig)

    def extract_from_text(
        self,
        text: str,
        problems: list[ExtractedProblem],
    ) -> RelationExtractionResult:
        """
        Extract relations from text given a list of problems.

        Args:
            text: Source text to analyze
            problems: List of problems to find relations between

        Returns:
            RelationExtractionResult with detected relations
        """
        relations: list[ExtractedRelation] = []

        # Step 1: Detect relations via textual cues
        cue_relations = self._extract_by_textual_cues(text, problems)
        relations.extend(cue_relations)

        # Step 2: Infer relations from problem statement similarity
        similarity_relations = self._extract_by_similarity(problems)
        relations.extend(similarity_relations)

        # Deduplicate relations
        relations = self._deduplicate_relations(relations)

        # Filter by confidence
        relations = [
            r for r in relations if r.confidence >= self.config.min_confidence
        ]

        return RelationExtractionResult(
            relations=relations,
            source_text=text[:500] if text else None,
        )

    async def extract_from_text_with_llm(
        self,
        text: str,
        problems: list[ExtractedProblem],
        paper_title: Optional[str] = None,
    ) -> RelationExtractionResult:
        """
        Extract relations using LLM analysis.

        Args:
            text: Source text to analyze
            problems: List of problems to find relations between
            paper_title: Title of the source paper

        Returns:
            RelationExtractionResult with LLM-detected relations
        """
        if not self.client:
            # Fall back to non-LLM extraction
            return self.extract_from_text(text, problems)

        if len(problems) < 2:
            return RelationExtractionResult(relations=[], source_text=text[:500])

        # Build problem list for prompt
        problem_list = "\n".join(
            f"Problem {i+1}: {p.statement}" for i, p in enumerate(problems)
        )

        prompt = f"""Analyze the following research problems and identify any relationships between them.

Paper: {paper_title or 'Unknown'}

Problems:
{problem_list}

Source text excerpt:
{text[:2000]}

For each relationship you identify:
1. Specify which problems are related (by number)
2. Choose the relationship type: extends, contradicts, depends_on, reframes, related_to, supersedes, specializes, generalizes
3. Provide the text evidence supporting this relationship
4. Assign a confidence score (0.0-1.0)

Only report relationships with clear evidence. Focus on explicit connections stated in the text."""

        system_prompt = """You are an expert at analyzing research literature and identifying relationships between research problems.
You identify relationships such as:
- extends: Problem A builds upon or advances Problem B
- contradicts: Problem A challenges or conflicts with Problem B
- depends_on: Problem A requires Problem B to be solved first
- reframes: Problem A offers an alternative perspective on Problem B
- related_to: Problems are conceptually related
- supersedes: Problem A makes Problem B obsolete
- specializes: Problem A is a specific case of Problem B
- generalizes: Problem A is a broader version of Problem B

Be precise and only report relationships with clear textual evidence."""

        try:
            response: LLMResponse[LLMRelationResult] = await self.client.extract(
                prompt=prompt,
                response_model=LLMRelationResult,
                system_prompt=system_prompt,
            )

            result = response.content
            # Map problem numbers to IDs
            relations = self._map_problem_ids(result.relations, problems)

            # Add textual cue relations
            cue_relations = self._extract_by_textual_cues(text, problems)

            # Combine and deduplicate
            all_relations = relations + cue_relations
            all_relations = self._deduplicate_relations(all_relations)

            # Filter by confidence
            all_relations = [
                r for r in all_relations if r.confidence >= self.config.min_confidence
            ]

            return RelationExtractionResult(
                relations=all_relations,
                source_text=text[:500],
            )

        except Exception:
            # Fall back to textual cue extraction on error
            return self.extract_from_text(text, problems)

    def _extract_by_textual_cues(
        self,
        text: str,
        problems: list[ExtractedProblem],
    ) -> list[ExtractedRelation]:
        """Extract relations by detecting textual cue patterns."""
        relations = []
        text_lower = text.lower()

        for relation_type, cues in RELATION_CUES.items():
            for cue in cues:
                if cue in text_lower:
                    # Find the context around the cue
                    cue_idx = text_lower.find(cue)
                    context_start = max(0, cue_idx - 100)
                    context_end = min(len(text), cue_idx + len(cue) + 100)
                    context = text[context_start:context_end]

                    # Try to match problems to this context
                    matched_problems = self._match_problems_to_context(
                        context, problems
                    )

                    if len(matched_problems) >= 2:
                        # Create relation between first two matched problems
                        relations.append(
                            ExtractedRelation(
                                source_problem_id=matched_problems[0].statement[:100],
                                target_problem_id=matched_problems[1].statement[:100],
                                relation_type=relation_type,
                                confidence=0.6,  # Medium confidence for cue-based
                                evidence=context.strip(),
                                extraction_method="textual_cue",
                            )
                        )

        return relations

    def _extract_by_similarity(
        self,
        problems: list[ExtractedProblem],
    ) -> list[ExtractedRelation]:
        """Extract relations based on problem statement similarity."""
        relations = []

        # Simple word overlap similarity for now
        # In production, use embeddings
        for i, p1 in enumerate(problems):
            for p2 in problems[i + 1 :]:
                similarity = self._compute_similarity(p1.statement, p2.statement)

                if similarity >= self.config.similarity_threshold:
                    relations.append(
                        ExtractedRelation(
                            source_problem_id=p1.statement[:100],
                            target_problem_id=p2.statement[:100],
                            relation_type=RelationType.RELATED_TO,
                            confidence=similarity,
                            evidence=f"High semantic similarity ({similarity:.2f}) between problem statements",
                            extraction_method="semantic_similarity",
                        )
                    )

        return relations

    def _match_problems_to_context(
        self,
        context: str,
        problems: list[ExtractedProblem],
    ) -> list[ExtractedProblem]:
        """Find problems whose statements or quotes appear in the context."""
        matched = []
        context_lower = context.lower()

        for problem in problems:
            # Check if key words from problem appear in context
            problem_words = set(problem.statement.lower().split())
            context_words = set(context_lower.split())

            overlap = len(problem_words & context_words) / max(len(problem_words), 1)

            if overlap > 0.3:  # 30% word overlap threshold
                matched.append(problem)

        return matched

    def _compute_similarity(self, text1: str, text2: str) -> float:
        """Compute simple word overlap similarity."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _map_problem_ids(
        self,
        relations: list[ExtractedRelation],
        problems: list[ExtractedProblem],
    ) -> list[ExtractedRelation]:
        """Map problem numbers in relations to actual problem statements."""
        mapped = []

        for relation in relations:
            try:
                # Try to parse problem numbers from IDs
                source_num = self._parse_problem_number(relation.source_problem_id)
                target_num = self._parse_problem_number(relation.target_problem_id)

                if source_num and target_num:
                    if 1 <= source_num <= len(problems) and 1 <= target_num <= len(
                        problems
                    ):
                        mapped.append(
                            ExtractedRelation(
                                source_problem_id=problems[
                                    source_num - 1
                                ].statement[:100],
                                target_problem_id=problems[
                                    target_num - 1
                                ].statement[:100],
                                relation_type=relation.relation_type,
                                confidence=relation.confidence,
                                evidence=relation.evidence,
                                extraction_method="llm",
                            )
                        )
                else:
                    # Keep original if not numbered
                    mapped.append(relation)
            except (ValueError, IndexError):
                continue

        return mapped

    def _parse_problem_number(self, problem_id: str) -> Optional[int]:
        """Parse problem number from ID like 'Problem 1' or '1'."""
        import re

        # Try "Problem N" format
        match = re.search(r"problem\s*(\d+)", problem_id.lower())
        if match:
            return int(match.group(1))

        # Try plain number
        try:
            return int(problem_id.strip())
        except ValueError:
            return None

    def _deduplicate_relations(
        self,
        relations: list[ExtractedRelation],
    ) -> list[ExtractedRelation]:
        """Remove duplicate relations, keeping highest confidence."""
        seen: dict[tuple, ExtractedRelation] = {}

        for relation in relations:
            key = (
                relation.source_problem_id[:50],
                relation.target_problem_id[:50],
                relation.relation_type,
            )

            if key not in seen or relation.confidence > seen[key].confidence:
                seen[key] = relation

        return list(seen.values())


# Module-level singleton
_relation_extractor: Optional[RelationExtractor] = None


def get_relation_extractor(
    client: Optional[BaseLLMClient] = None,
    config: Optional[RelationConfig] = None,
) -> RelationExtractor:
    """
    Get the singleton RelationExtractor instance.

    Args:
        client: Optional LLM client for LLM-based extraction
        config: Optional configuration

    Returns:
        RelationExtractor instance
    """
    global _relation_extractor

    if _relation_extractor is None:
        _relation_extractor = RelationExtractor(
            client=client,
            config=config or RelationConfig(),
        )

    return _relation_extractor


def reset_relation_extractor() -> None:
    """Reset the singleton (useful for testing)."""
    global _relation_extractor
    _relation_extractor = None
