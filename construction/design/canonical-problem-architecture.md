# Canonical Problem Architecture - Design Specification

**Created:** 2026-02-07
**Status:** Complete
**Related ADRs:** ADR-003 (Problems as First-Class Entities), ADR-005 (Hybrid Retrieval)
**Last Updated:** 2026-02-09
**Revision:** Added Security, Operations & Debugging, and UX Specifications sections. Enhanced Verification Strategy with golden dataset testing, consensus testing methodology, performance benchmarks, and property-based testing. Expanded Review Queue System with workflow state machine, work item tracking, priority & SLA management, and draft saves & checkpointing. Design marked Complete - ready for sprint creation.

---

## Executive Summary

This design introduces a **canonical problem architecture** that separates paper-specific problem mentions from canonical problem concepts. The system enables:

- **Aggregated research tracking**: See all papers working on the same underlying problem
- **Consensus tracking**: Know how many papers agree a problem is unsolved
- **Clear provenance**: Preserve how each paper framed the problem
- **Logical graph traversal**: Flow from problem to problem to discover important research directions

**Key Innovation**: Each paper creates `ProblemMention` nodes that are matched to canonical `ProblemConcept` nodes using embedding similarity, citation analysis, and multi-agent consensus.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution Approach](#2-solution-approach)
3. [Data Model](#3-data-model)
4. [Agent Workflows](#4-agent-workflows)
5. [Extraction Pipeline](#5-extraction-pipeline)
6. [API Design](#6-api-design)
7. [Security](#7-security) *(NEW)*
8. [Operations & Debugging](#8-operations--debugging) *(NEW)*
9. [Review Queue System](#9-review-queue-system) *(with workflow state machine, work item tracking, SLA management)*
10. [Verification Strategy](#10-verification-strategy) *(with golden dataset testing, consensus testing, performance benchmarks)*
11. [UX Specifications](#11-ux-specifications) *(NEW)*
12. [Implementation Phases](#12-implementation-phases)
13. [Open Questions](#13-open-questions)
14. [Appendices](#appendix-a-example-queries)

---

## 1. Problem Statement

### Current State

The existing system creates a new `Problem` node for each problem extracted from a paper. This leads to:

1. **Duplicate canonical problems**: "Hallucination in LLMs" appears as 50 separate nodes across 50 papers
2. **Lost paper-specific context**: Cannot see how different papers framed the same problem
3. **No consensus tracking**: Cannot tell if 100 papers agree a problem is important
4. **Difficult graph traversal**: Cannot flow logically from problem to related problems across the literature

### Desired State

A knowledge graph where:
- Users can query "Show me research on Problem X" and see an aggregated view with all papers and mentions
- The system makes it obvious which problems are important (mentioned by many papers)
- Each problem has clear provenance (where it came from, how it was stated)
- Users can flow from problem to problem to discover research directions

### How We'll Know It Works

**Success Criteria:**
1. **Graph traversal**: Users can easily navigate from problem to related concepts
2. **Citation hop validation**: Import a paper + N-hops of cited papers, verify correct concept linking
3. **Consensus visible**: Query returns "Problem X mentioned by 15 papers" with list
4. **Provenance clear**: Each mention shows original paper statement + canonical synthesis
5. **Fast queries**: Response time <500ms for concept lookup

**Test Domain:** Computer Science - Knowledge Graph Retrieval

---

## 2. Solution Approach

### Architecture Overview

```
Paper → Extract Mentions → Match to Concepts → Store in KG
                ↓                  ↓
         ProblemMention     ProblemConcept
                ↓                  ↓
         [Agent Review]     [Synthesis Agent]
```

### Core Components

1. **ProblemMention** (Paper-Specific)
   - Created for each problem extracted from a paper
   - Preserves original statement, context, and metadata
   - Has embedding for similarity matching
   - Links to one ProblemConcept (or pending review)

2. **ProblemConcept** (Canonical)
   - Represents the underlying research problem
   - Has AI-synthesized canonical statement
   - Aggregates metadata from all mentions
   - Tracks consensus (mention count, paper count)

3. **Concept Matcher**
   - Uses embedding similarity + citation analysis
   - Classifies matches by confidence (HIGH/MEDIUM/LOW)
   - Routes to appropriate workflow:
     - HIGH (>95%): Auto-link
     - MEDIUM (80-95%): Agent review
     - LOW (50-80%): Multi-agent consensus
     - <50%: Create new concept

4. **Review Queue**
   - Tracks pending concept assignments
   - Integrates with multi-agent debate system
   - Provides human approval interface

5. **Synthesis Agent** (LangGraph)
   - Creates canonical statements from mentions
   - Aggregates metadata with provenance
   - Validates baselines before promotion

### Key Design Decisions

**Decision 1: Promotion Model**
- ProblemMentions accumulate first
- Promoted to ProblemConcept when:
  - First mention (auto-creates concept)
  - Subsequent mentions (matched to existing concept)

**Decision 2: Similarity Thresholds**
- HIGH: >95% similarity → Auto-link
- MEDIUM: 80-95% → Single agent review
- LOW: 50-80% → Multi-agent consensus
- <50% → Create new concept

**Decision 3: Citation Boosting**
- If Paper B cites Paper A and both mention similar problem:
  - Boost similarity score by 10%
  - Increase confidence in canonical link

---

## 3. Data Model

### 3.1 ProblemMention Node

```python
class ProblemMention(BaseModel):
    """Paper-specific mention of a research problem."""

    # Identity
    id: str  # UUID
    statement: str  # Problem as stated in this paper
    paper_doi: str
    section: str  # Where in paper

    # Rich metadata (same structure as current Problem)
    domain: Optional[str]
    assumptions: list[Assumption]
    constraints: list[Constraint]
    datasets: list[Dataset]
    metrics: list[Metric]
    baselines: list[Baseline]

    # Extraction provenance
    quoted_text: str
    extraction_metadata: ExtractionMetadata
    embedding: Optional[list[float]]  # 1536 dims

    # Concept linking
    concept_id: Optional[str]
    match_confidence: MatchConfidence  # HIGH/MEDIUM/LOW/REJECTED
    match_score: Optional[float]  # 0-1

    # Review tracking
    review_status: ReviewStatus  # PENDING/APPROVED/REJECTED/NEEDS_CONSENSUS
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    agent_consensus: Optional[dict]  # Maker/hater debate results

    # Timestamps
    created_at: datetime
    updated_at: datetime
```

### 3.2 ProblemConcept Node

```python
class ProblemConcept(BaseModel):
    """Canonical representation of a research problem."""

    # Identity
    id: str  # UUID
    canonical_statement: str  # AI-synthesized
    domain: str
    status: ProblemStatus  # OPEN/IN_PROGRESS/RESOLVED/DEPRECATED

    # Aggregated metadata
    assumptions: list[Assumption]  # Union of all mentions
    constraints: list[Constraint]
    datasets: list[Dataset]
    metrics: list[Metric]

    # Baselines with validation
    verified_baselines: list[Baseline]  # Reproducible
    claimed_baselines: list[Baseline]  # Unverified

    # Synthesis metadata
    synthesis_method: str  # "llm_synthesis"
    synthesis_model: Optional[str]  # "gpt-4"
    synthesized_at: Optional[datetime]
    synthesized_by: Optional[str]  # Agent or human
    human_edited: bool

    # Aggregation stats
    mention_count: int  # Number of mentions
    paper_count: int  # Number of unique papers
    first_mentioned_year: Optional[int]
    last_mentioned_year: Optional[int]

    # Semantic search
    embedding: Optional[list[float]]

    # Versioning
    created_at: datetime
    updated_at: datetime
    version: int
```

### 3.3 Relationships

```cypher
// Mention to Concept
(m:ProblemMention)-[:INSTANCE_OF {
    confidence: float,
    matched_at: datetime,
    match_method: string  // "embedding" | "citation_boost" | "agent_consensus"
}]->(c:ProblemConcept)

// Mention to Paper
(m:ProblemMention)-[:EXTRACTED_FROM {
    section: string,
    extraction_date: datetime
}]->(p:Paper)

// Concept relationships (canonical level)
(c1:ProblemConcept)-[:EXTENDS {
    confidence: float,
    evidence_doi: string
}]->(c2:ProblemConcept)

(c1:ProblemConcept)-[:CONTRADICTS]->(c2:ProblemConcept)
(c1:ProblemConcept)-[:DEPENDS_ON]->(c2:ProblemConcept)
(c1:ProblemConcept)-[:REFRAMES]->(c2:ProblemConcept)

// Mention relationships (paper-specific level)
(m1:ProblemMention)-[:EXTENDS]->(m2:ProblemMention)
(m1:ProblemMention)-[:CONTRADICTS]->(m2:ProblemMention)
// ... etc
```

### 3.4 Review Queue Node

```python
class PendingReview(BaseModel):
    """Tracks mentions awaiting concept assignment review."""

    id: str
    mention_id: str
    suggested_concepts: list[dict]  # [{concept_id, score, reasoning}]
    agent_consensus: Optional[dict]  # Debate results
    priority: int  # 1=highest, 10=lowest

    created_at: datetime
    assigned_to: Optional[str]
    reviewed_at: Optional[datetime]
```

### 3.5 Neo4j Constraints and Indexes

```cypher
// Constraints
CREATE CONSTRAINT mention_id_unique IF NOT EXISTS
FOR (m:ProblemMention) REQUIRE m.id IS UNIQUE;

CREATE CONSTRAINT concept_id_unique IF NOT EXISTS
FOR (c:ProblemConcept) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT review_id_unique IF NOT EXISTS
FOR (r:PendingReview) REQUIRE r.id IS UNIQUE;

// Indexes
CREATE INDEX mention_concept_idx IF NOT EXISTS
FOR (m:ProblemMention) ON (m.concept_id);

CREATE INDEX mention_review_status_idx IF NOT EXISTS
FOR (m:ProblemMention) ON (m.review_status);

CREATE INDEX concept_domain_idx IF NOT EXISTS
FOR (c:ProblemConcept) ON (c.domain);

CREATE INDEX concept_status_idx IF NOT EXISTS
FOR (c:ProblemConcept) ON (c.status);

CREATE INDEX review_priority_idx IF NOT EXISTS
FOR (r:PendingReview) ON (r.priority);

// Vector indexes
CREATE VECTOR INDEX mention_embedding_idx IF NOT EXISTS
FOR (m:ProblemMention)
ON m.embedding
OPTIONS {
    indexConfig: {
        `vector.dimensions`: 1536,
        `vector.similarity_function`: 'cosine'
    }
};

CREATE VECTOR INDEX concept_embedding_idx IF NOT EXISTS
FOR (c:ProblemConcept)
ON c.embedding
OPTIONS {
    indexConfig: {
        `vector.dimensions`: 1536,
        `vector.similarity_function`: 'cosine'
    }
};
```

---

## 4. Agent Workflows

### 4.1 Agent Roles

**Concept Matcher Agent**
- Input: ProblemMention + domain
- Output: List of candidate concepts with scores
- Uses: Embedding similarity + citation analysis

**Evaluator Agent** (MEDIUM confidence)
- Input: Mention + suggested concept
- Output: APPROVE / REJECT / NEEDS_CONSENSUS
- Reasoning: Single-agent decision with explanation

**Maker Agent** (LOW confidence)
- Role: Argues FOR the match
- Input: Mention + suggested concept
- Output: Arguments supporting the match

**Hater Agent** (LOW confidence)
- Role: Argues AGAINST the match
- Input: Mention + suggested concept
- Output: Arguments against the match

**Consensus Agent** (LOW confidence)
- Input: Maker arguments + Hater arguments
- Output: Final decision + confidence score
- Method: Weighs both perspectives

**Synthesis Agent**
- Input: List of ProblemMentions for a concept
- Output: Canonical statement + aggregated metadata
- Method: LLM synthesis with human override

### 4.2 Matching Workflow (LangGraph)

```python
# Agent workflow state
class MatchingState(TypedDict):
    mention: ProblemMention
    candidates: list[dict]  # {concept_id, score, reasoning}
    top_match: Optional[dict]
    confidence: MatchConfidence
    decision: Optional[str]  # "auto_link" | "approved" | "rejected" | "new_concept"
    reasoning: Optional[str]
    maker_args: Optional[str]
    hater_args: Optional[str]
    consensus: Optional[dict]

# Workflow graph
workflow = StateGraph(MatchingState)

workflow.add_node("find_candidates", find_matching_concepts)
workflow.add_node("classify_confidence", classify_match_confidence)
workflow.add_node("auto_link", auto_link_high_confidence)
workflow.add_node("evaluator_review", evaluator_agent_review)
workflow.add_node("maker_debate", maker_agent_debate)
workflow.add_node("hater_debate", hater_agent_debate)
workflow.add_node("reach_consensus", consensus_agent_decision)
workflow.add_node("create_new_concept", create_new_concept)
workflow.add_node("queue_for_human", queue_for_human_review)

# Conditional routing
workflow.add_conditional_edges(
    "classify_confidence",
    route_by_confidence,
    {
        "high": "auto_link",
        "medium": "evaluator_review",
        "low": "maker_debate",
        "none": "create_new_concept"
    }
)

workflow.add_conditional_edges(
    "evaluator_review",
    route_evaluator_decision,
    {
        "approved": "auto_link",
        "rejected": "create_new_concept",
        "needs_consensus": "maker_debate"
    }
)

workflow.add_edge("maker_debate", "hater_debate")
workflow.add_edge("hater_debate", "reach_consensus")

workflow.add_conditional_edges(
    "reach_consensus",
    route_consensus_decision,
    {
        "high_confidence_approve": "auto_link",
        "high_confidence_reject": "create_new_concept",
        "low_confidence": "queue_for_human"
    }
)

workflow.set_entry_point("find_candidates")
```

### 4.3 Agent Prompts

**Matcher Agent Prompt:**
```
You are a research problem matcher. Your task is to find existing canonical problem concepts
that match a newly extracted problem mention from a paper.

Given:
- Problem mention: "{mention.statement}"
- Domain: "{mention.domain}"
- Paper: "{mention.paper_doi}"

You have identified these candidate concepts through embedding similarity:
{candidates}

For each candidate, explain:
1. Why this might be the same underlying problem
2. Key similarities in assumptions, constraints, or framing
3. Any differences that suggest they might be distinct problems
4. Your confidence in the match (0-1)

Return a ranked list of candidates with scores and reasoning.
```

**Maker Agent Prompt:**
```
You are the MAKER agent in a debate about whether two research problems are the same.
Your role is to argue FOR the match - find evidence that these are the same underlying problem.

Problem Mention: "{mention.statement}"
From paper: {mention.paper_doi}

Candidate Concept: "{concept.canonical_statement}"
Mentioned by {concept.paper_count} papers

Argue for why these should be linked:
- Semantic similarity in problem framing
- Overlapping assumptions and constraints
- Similar metrics and datasets
- Citation relationships between papers
- Domain expertise perspective

Be persuasive but honest. If there's weak evidence, acknowledge it.
```

**Hater Agent Prompt:**
```
You are the HATER agent in a debate about whether two research problems are the same.
Your role is to argue AGAINST the match - find evidence that these are distinct problems.

Problem Mention: "{mention.statement}"
From paper: {mention.paper_doi}

Candidate Concept: "{concept.canonical_statement}"
Mentioned by {concept.paper_count} papers

Argue for why these should NOT be linked:
- Differences in problem framing or scope
- Different assumptions or constraints
- Different evaluation criteria or metrics
- Different research communities or venues
- Risk of conflating related but distinct problems

Be critical but fair. If the match seems strong, acknowledge it.
```

**Consensus Agent Prompt:**
```
You are the CONSENSUS agent. You've heard arguments from the MAKER (pro-match) and HATER (anti-match).

MAKER Arguments:
{maker_args}

HATER Arguments:
{hater_args}

Your task:
1. Weigh both perspectives
2. Consider false positive vs false negative risk
   - False positive (linking unrelated): ~5% acceptable
   - False negative (missing duplicates): MUST be near 0%
3. Make a final decision: LINK or CREATE_NEW
4. Provide confidence score (0-1)
5. Explain your reasoning

Remember: It's worse to miss a duplicate than to link similar problems.
```

**Synthesis Agent Prompt:**
```
You are a research problem synthesis agent. Your task is to create a canonical problem statement
from multiple paper-specific mentions.

You have {mention_count} mentions of this problem from {paper_count} papers:

{mentions_list}

Create:
1. A canonical statement that captures the essence of all mentions
2. Aggregated list of assumptions (union of all mentions)
3. Aggregated list of constraints
4. Aggregated list of datasets and metrics
5. Note any conflicting information (e.g., different baseline values)

The canonical statement should:
- Be clear and concise (1-2 sentences)
- Capture the core problem without paper-specific details
- Be general enough to encompass all mentions
- Be specific enough to distinguish from related problems

Include provenance notes for any aggregated metadata.
```

### 4.4 Synthesis Workflow

When a ProblemConcept gets a new mention or is first created:

1. **Trigger**: New INSTANCE_OF relationship created
2. **Collect**: Gather all ProblemMentions for this concept
3. **Synthesize**:
   - Generate new canonical statement
   - Aggregate metadata with provenance
   - Validate baselines (check for reproducibility)
4. **Update**: Update ProblemConcept node
5. **Version**: Increment version number
6. **Notify**: Queue for human review if statement changed significantly

---

## 5. Extraction Pipeline

### 5.1 Updated Pipeline Flow

```
Paper → Section Segmentation → Problem Extraction → CHANGED: Mention Creation & Matching
```

### 5.2 Problem Extractor Changes

**Current behavior:**
```python
# Old: Creates Problem nodes directly
def extract_problems(paper: Paper) -> list[Problem]:
    ...
    return [Problem(...), Problem(...)]
```

**New behavior:**
```python
# New: Creates ProblemMentions and matches to concepts
async def extract_mentions(paper: Paper) -> list[ProblemMention]:
    """
    Extract problem mentions from paper.
    Also infers problems not explicitly mentioned using maker/hater model.
    """
    # Extract explicit problems (current logic)
    explicit_mentions = await extract_explicit_problems(paper)

    # Infer additional problems (new logic)
    inferred_mentions = await infer_problems(paper, explicit_mentions)

    # Combine
    all_mentions = explicit_mentions + inferred_mentions

    # Generate embeddings
    for mention in all_mentions:
        mention.embedding = await generate_embedding(mention.statement)

    return all_mentions

async def match_and_store(mentions: list[ProblemMention], paper: Paper):
    """
    Match mentions to concepts and store in KG.
    """
    for mention in mentions:
        # Find matching concepts
        matcher = ConceptMatcher()
        candidates = await matcher.find_matching_concepts(mention)

        # Classify confidence
        if candidates:
            top_match = candidates[0]
            confidence = matcher.classify_confidence(top_match['score'])

            # Route to appropriate workflow
            if confidence == MatchConfidence.HIGH:
                # Auto-link
                await link_mention_to_concept(mention, top_match['concept_id'])
            elif confidence == MatchConfidence.MEDIUM:
                # Single agent review
                decision = await evaluator_agent_review(mention, top_match)
                if decision['approved']:
                    await link_mention_to_concept(mention, top_match['concept_id'])
                else:
                    await create_new_concept(mention)
            elif confidence == MatchConfidence.LOW:
                # Multi-agent consensus
                result = await multi_agent_consensus(mention, top_match)
                if result['decision'] == 'link':
                    await link_mention_to_concept(mention, top_match['concept_id'])
                elif result['decision'] == 'new':
                    await create_new_concept(mention)
                else:
                    # Queue for human review
                    await queue_for_review(mention, candidates)
        else:
            # No candidates - create new concept
            await create_new_concept(mention)
```

### 5.3 Inference Agent (New)

Uses maker/hater model to infer problems not explicitly stated:

```python
async def infer_problems(paper: Paper, explicit_mentions: list[ProblemMention]) -> list[ProblemMention]:
    """
    Infer research problems implied but not explicitly stated in paper.

    Example: Paper discusses "fine-tuning BERT" but doesn't explicitly state
    the underlying problem "efficient adaptation of pre-trained models".
    """
    # Maker: Generate candidate inferred problems
    maker_candidates = await maker_agent_infer(paper, explicit_mentions)

    # Hater: Challenge each candidate
    validated_inferences = []
    for candidate in maker_candidates:
        hater_review = await hater_agent_challenge(candidate, paper)
        if hater_review['accept']:
            validated_inferences.append(candidate)

    # Convert to ProblemMention with lower confidence
    inferred_mentions = [
        ProblemMention(
            statement=inf.statement,
            paper_doi=paper.doi,
            section="inferred",
            quoted_text=inf.supporting_text,
            extraction_metadata=ExtractionMetadata(
                extraction_method="agent_inference",
                confidence_score=inf.confidence
            )
        )
        for inf in validated_inferences
    ]

    return inferred_mentions
```

---

## 6. API Design

### 6.1 New Endpoints

**GET /concepts**
```python
@router.get("/concepts", response_model=ConceptListResponse)
async def list_concepts(
    domain: Optional[str] = None,
    status: Optional[ProblemStatus] = None,
    min_mention_count: int = 1,
    limit: int = 50,
    offset: int = 0
):
    """
    List canonical problem concepts with aggregation stats.

    Returns:
    {
        "concepts": [
            {
                "id": "uuid",
                "canonical_statement": "...",
                "domain": "NLP",
                "status": "open",
                "mention_count": 15,
                "paper_count": 12,
                "first_mentioned_year": 2020,
                "last_mentioned_year": 2024
            }
        ],
        "total": 100,
        "limit": 50,
        "offset": 0
    }
    """
```

**GET /concepts/{concept_id}**
```python
@router.get("/concepts/{concept_id}", response_model=ConceptDetail)
async def get_concept(concept_id: str):
    """
    Get detailed view of a concept including:
    - Canonical statement and metadata
    - List of all mentions with paper info
    - Aggregated datasets, metrics, baselines
    - Related concepts (EXTENDS, CONTRADICTS, etc.)

    Returns:
    {
        "id": "uuid",
        "canonical_statement": "...",
        "domain": "NLP",
        "mention_count": 15,
        "mentions": [
            {
                "id": "uuid",
                "statement": "...",
                "paper": {
                    "doi": "10.1234/...",
                    "title": "...",
                    "year": 2023
                },
                "match_confidence": "high",
                "match_score": 0.97
            }
        ],
        "aggregated_metadata": {
            "assumptions": [...],
            "datasets": [...],
            "metrics": [...],
            "verified_baselines": [...],
            "claimed_baselines": [...]
        },
        "related_concepts": {
            "extends": [...],
            "contradicts": [...],
            "depends_on": [...]
        }
    }
    """
```

**GET /mentions**
```python
@router.get("/mentions", response_model=MentionListResponse)
async def list_mentions(
    paper_doi: Optional[str] = None,
    concept_id: Optional[str] = None,
    review_status: Optional[ReviewStatus] = None,
    limit: int = 50,
    offset: int = 0
):
    """List problem mentions with filters."""
```

**GET /mentions/{mention_id}**
```python
@router.get("/mentions/{mention_id}", response_model=MentionDetail)
async def get_mention(mention_id: str):
    """Get detailed view of a mention."""
```

**POST /concepts/{concept_id}/synthesize**
```python
@router.post("/concepts/{concept_id}/synthesize", response_model=SynthesisResult)
async def synthesize_concept(concept_id: str):
    """
    Trigger re-synthesis of canonical statement from all mentions.
    Used when new mentions are added or human wants to refresh.
    """
```

**GET /reviews/pending**
```python
@router.get("/reviews/pending", response_model=PendingReviewListResponse)
async def list_pending_reviews(
    priority: Optional[int] = None,
    limit: int = 20
):
    """
    Get pending reviews sorted by priority.

    Returns:
    {
        "reviews": [
            {
                "id": "uuid",
                "mention": {...},
                "suggested_concepts": [
                    {
                        "concept_id": "uuid",
                        "concept_statement": "...",
                        "score": 0.85,
                        "reasoning": "..."
                    }
                ],
                "agent_consensus": {...},
                "priority": 3
            }
        ]
    }
    """
```

**POST /reviews/{review_id}/approve**
```python
@router.post("/reviews/{review_id}/approve", response_model=ReviewApprovalResult)
async def approve_review(
    review_id: str,
    decision: ReviewDecision  # {concept_id: str} or {create_new: true}
):
    """
    Approve a pending review - link mention to concept or create new.
    """
```

### 6.2 Search Endpoints (Updated)

**POST /search/concepts**
```python
@router.post("/search/concepts", response_model=ConceptSearchResponse)
async def search_concepts(
    query: str,
    domain: Optional[str] = None,
    filters: Optional[dict] = None,
    limit: int = 10
):
    """
    Semantic search for problem concepts.
    Uses embedding similarity on canonical statements.
    """
```

**POST /search/mentions**
```python
@router.post("/search/mentions", response_model=MentionSearchResponse)
async def search_mentions(
    query: str,
    domain: Optional[str] = None,
    paper_doi: Optional[str] = None,
    limit: int = 10
):
    """
    Semantic search for problem mentions.
    Searches paper-specific problem statements.
    """
```

---

## 7. Security

### 7.1 Prompt Injection Protection

**Threat:** Malicious input in problem statements, paper metadata, or user queries could manipulate agent behavior.

**Mitigations:**
- **Input Validation**: Validate all user inputs against strict schemas before processing
- **Structured Outputs**: Use JSON schema mode for all LLM outputs to prevent free-form injection
- **Prompt Isolation**: Never concatenate user input directly into system prompts
- **Sanitization**: Strip HTML, script tags, and control characters from all text inputs
- **Agent Sandboxing**: Run agents with limited permissions, no direct database access

**Example:**
```python
class SafeExtraction:
    def extract_problems(self, user_text: str):
        # Validate input
        if not self.is_safe_text(user_text):
            raise ValidationError("Invalid characters in input")

        # Use structured output
        response = llm.complete(
            prompt=self.build_safe_prompt(),
            response_format={"type": "json_schema", "schema": ProblemSchema}
        )

        # Never do this:
        # prompt = f"Extract problems from: {user_text}"  # UNSAFE!
```

**References:**
- OWASP LLM Top 10: LLM01 - Prompt Injection
- ADR-XXX: Agent Security Model (to be created)

### 7.2 Access Control

**Requirements:**

**Role-Based Access Control (RBAC):**
- **Viewer Role**:
  - Read concepts, mentions, papers
  - Search and query
  - No write access
- **Editor Role**:
  - All viewer permissions
  - Create/update problem mentions
  - Approve/reject reviews
  - Edit canonical statements
- **Admin Role**:
  - All editor permissions
  - Manage blacklist
  - Rollback changes
  - Delete concepts (hard delete)
  - View audit logs

**Authentication:**
- API key authentication for programmatic access
- OAuth2/OIDC for web UI (Google Workspace integration)
- Service account for agent-to-agent calls
- Rate limiting per API key (1000 requests/hour)

**Audit Logging:**
- Log all mutations (create, update, delete, approve, reject, blacklist)
- Include: user ID, action, timestamp, affected entities, reasoning
- Store in immutable append-only log
- Retention: 90 days minimum

**Implementation:**
```python
@router.post("/reviews/{review_id}/approve")
@require_role("editor")
async def approve_review(
    review_id: str,
    decision: ReviewDecision,
    current_user: User = Depends(get_current_user)
):
    # Log the action
    await audit_log.record(
        user_id=current_user.id,
        action="review_approve",
        resource_type="PendingReview",
        resource_id=review_id,
        details={"decision": decision.dict()}
    )

    # Perform action
    result = await review_service.approve(review_id, decision)
    return result
```

### 7.3 Data Integrity

**Validation Layers:**

1. **API Layer Validation**:
   - Pydantic models enforce schemas
   - Required fields, type checking, value constraints
   - Reject malformed requests with 400 Bad Request

2. **Business Logic Validation**:
   - Verify concept exists before linking mention
   - Check for duplicate mentions from same paper
   - Validate baseline claims have DOI evidence
   - Ensure state transitions are legal (e.g., can't approve already-rejected review)

3. **Database Constraints**:
   - Unique constraints on IDs
   - Foreign key relationships (via application logic in Neo4j)
   - Index constraints for required fields

**Transaction Rollback:**
- All multi-step operations wrapped in transactions
- Automatic rollback on errors
- Example: If synthesis fails, don't update concept version

```python
async def link_mention_to_concept(mention_id: str, concept_id: str):
    async with neo4j_session.transaction() as tx:
        try:
            # Step 1: Create INSTANCE_OF relationship
            await tx.run(create_relationship_query)

            # Step 2: Update mention status
            await tx.run(update_mention_query)

            # Step 3: Increment concept mention_count
            await tx.run(update_concept_stats_query)

            # Step 4: Trigger synthesis
            await synthesis_agent.run(concept_id)

            await tx.commit()
        except Exception as e:
            await tx.rollback()
            raise IntegrityError(f"Failed to link mention: {e}")
```

**Immutable Audit Trail:**
- All changes create audit log entries
- Audit logs are append-only (never delete or modify)
- Include before/after snapshots for reversibility

### 7.4 API Security

**Rate Limiting:**
- Per API key: 1000 requests/hour
- Per IP (unauthenticated): 100 requests/hour
- Burst allowance: 20 requests/minute
- 429 Too Many Requests response with Retry-After header

**Input Sanitization:**
- HTML encoding for all text outputs
- SQL injection prevention (not applicable, using Neo4j with parameterized queries)
- Cypher injection prevention via parameterized queries only
- File upload validation (if added): max size 10MB, allowed extensions only

**CORS Configuration:**
- Restrict to known origins (production UI domain)
- Allow credentials for authenticated requests
- Preflight cache: 1 hour

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://agentic-kg-ui-staging-542888988741.us-central1.run.app"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    max_age=3600
)
```

**API Versioning:**
- URL-based versioning: `/api/v1/concepts`, `/api/v2/concepts`
- Current version: v1
- Deprecation policy: 6 months notice before removing old versions
- Version header: `X-API-Version: 1`

### 7.5 Secrets Management

**GCP Secret Manager:**
- Store all credentials in Secret Manager:
  - Neo4j password
  - OpenAI API key
  - Anthropic API key
  - JWT signing secret
  - API keys for service accounts
- Access via IAM service account with least-privilege permissions

**No Secrets in Code:**
- Never commit secrets to git
- Use environment variables loaded from Secret Manager
- Scan commits with pre-commit hooks (detect-secrets)

**Rotation Policies:**
- Rotate API keys: every 90 days
- Rotate database passwords: every 180 days
- Automated rotation with zero-downtime deployment
- Alert on rotation failures

**Example:**
```python
from google.cloud import secretmanager

class SecretManager:
    def __init__(self, project_id: str):
        self.client = secretmanager.SecretManagerServiceClient()
        self.project_id = project_id

    def get_secret(self, secret_id: str) -> str:
        name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
        response = self.client.access_secret_version(request={"name": name})
        return response.payload.data.decode('UTF-8')

# Usage
secrets = SecretManager(project_id="vt-gcp-00042")
neo4j_password = secrets.get_secret("neo4j-password")
openai_api_key = secrets.get_secret("openai-api-key")
```

**Logging Safety:**
- Never log secrets or API keys
- Redact sensitive fields in logs (passwords, tokens)
- Use structured logging with automatic redaction

---

## 8. Operations & Debugging

### 8.1 Debug Runbook

**Purpose:** Enable rapid debugging of production issues without compromising security or data integrity.

**Runbook Location:** `construction/runbooks/debug-docker-instance.md`

**Debug Instance Setup:**

```bash
# 1. Spin up debug Docker container with Neo4j access
docker run -it --rm \
  --name agentic-kg-debug \
  --network host \
  -e NEO4J_URI="neo4j://34.173.74.125:7687" \
  -e NEO4J_PASSWORD="$(gcloud secrets versions access latest --secret=neo4j-password)" \
  -e OPENAI_API_KEY="$(gcloud secrets versions access latest --secret=openai-api-key)" \
  -v $(pwd):/workspace \
  agentic-kg-api:latest \
  /bin/bash

# 2. Inside container: Run diagnostic queries
python -m agentic_kg.tools.diagnose --trace-id <TRACE_ID>

# 3. Query Neo4j directly
cypher-shell -u neo4j -p <password> \
  "MATCH (m:ProblemMention {id: '<mention-id>'}) RETURN m"

# 4. Test agent workflows
python -m agentic_kg.agents.test_matcher \
  --mention-id <mention-id> \
  --dry-run
```

**Access Control:**
- Only admin role can access debug instance
- Requires multi-factor authentication
- Sessions logged and time-limited (4 hours max)
- Read-only access by default, write requires approval

**Safety Guidelines:**
- Never modify production data directly (use API endpoints)
- Always test changes in staging first
- Document all queries run during debugging
- Close debug session when done

### 8.2 Trace IDs

**Format:** `{timestamp}-{mention_id}-{operation}`

**Example:** `20260209143022-a1b2c3d4-matching`

**Implementation:**

```python
import uuid
from datetime import datetime
from contextvars import ContextVar

# Context variable for request-scoped trace ID
trace_id_var: ContextVar[str] = ContextVar('trace_id', default=None)

class TraceIDMiddleware:
    """Middleware to inject trace IDs into all requests."""

    async def __call__(self, request: Request, call_next):
        # Generate or extract trace ID
        trace_id = request.headers.get('X-Trace-ID') or self.generate_trace_id()
        trace_id_var.set(trace_id)

        # Add to request state
        request.state.trace_id = trace_id

        # Add to response headers
        response = await call_next(request)
        response.headers['X-Trace-ID'] = trace_id

        return response

    def generate_trace_id(self) -> str:
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        unique_id = uuid.uuid4().hex[:8]
        return f"{timestamp}-{unique_id}"

# Usage in logging
logger.info(
    "Starting concept matching",
    extra={"trace_id": trace_id_var.get(), "mention_id": mention.id}
)
```

**Propagation:**
- HTTP headers: `X-Trace-ID`
- Log entries: `trace_id` field
- Database operations: stored in audit log
- Agent calls: passed as context parameter
- Error responses: included in error payload

**Querying by Trace ID:**

```python
# Find all operations for a specific request
logs = await log_store.query(
    trace_id="20260209143022-a1b2c3d4-matching"
)

# Find all work items in review queue
reviews = await neo4j.run("""
    MATCH (w:WorkItem {trace_id: $trace_id})
    RETURN w
""", trace_id=trace_id)
```

### 8.3 Draft Saves & Checkpointing

**Checkpointing Strategy:**

Checkpoint state at each workflow stage:
1. **After Extraction**: Save ProblemMention with status=EXTRACTED
2. **After Matching**: Save candidate concepts with scores
3. **After Agent Review**: Save agent consensus and decision
4. **Before Final Commit**: Save complete work item for rollback

**Implementation:**

```python
class WorkflowCheckpoint:
    """Manages workflow checkpointing."""

    async def checkpoint(
        self,
        trace_id: str,
        stage: str,
        state: dict,
        mention: ProblemMention
    ):
        """Save checkpoint for current workflow stage."""
        checkpoint = {
            "trace_id": trace_id,
            "stage": stage,
            "timestamp": datetime.utcnow(),
            "state": state,
            "mention_snapshot": mention.dict()
        }

        # Store in Neo4j
        await self.neo4j.run("""
            CREATE (c:Checkpoint {
                trace_id: $trace_id,
                stage: $stage,
                timestamp: datetime($timestamp),
                state: $state
            })
        """, **checkpoint)

        return checkpoint

    async def get_last_checkpoint(self, trace_id: str) -> Optional[dict]:
        """Retrieve most recent checkpoint for trace ID."""
        result = await self.neo4j.run("""
            MATCH (c:Checkpoint {trace_id: $trace_id})
            RETURN c
            ORDER BY c.timestamp DESC
            LIMIT 1
        """, trace_id=trace_id)

        return result.single() if result else None

# Usage in workflow
async def matching_workflow(mention: ProblemMention):
    trace_id = trace_id_var.get()

    # Checkpoint after extraction
    await checkpoint.save(trace_id, "extraction", {}, mention)

    # Find candidates
    candidates = await matcher.find_candidates(mention)

    # Checkpoint after matching
    await checkpoint.save(trace_id, "matching", {"candidates": candidates}, mention)

    # Agent review
    decision = await evaluator.review(mention, candidates[0])

    # Checkpoint before commit
    await checkpoint.save(trace_id, "review_complete", {"decision": decision}, mention)

    # Final commit
    if decision["approved"]:
        await link_mention_to_concept(mention.id, decision["concept_id"])
```

**Draft State:**
- Drafts stored with status=DRAFT
- Not visible in production queries
- Can be edited multiple times
- Final commit changes status to APPROVED/REJECTED

### 8.4 Reprocess Capability

**Use Cases:**
- Re-run matching after threshold tuning
- Reprocess after agent prompt improvements
- Fix batch of incorrectly linked mentions

**Implementation:**

```python
@router.post("/admin/reprocess")
@require_role("admin")
async def reprocess_mentions(
    filters: ReprocessFilters,
    current_user: User = Depends(get_current_user)
):
    """
    Reprocess mentions with new matching logic.

    Filters:
    - mention_ids: list[str] - specific mentions
    - date_range: (start, end) - mentions created in range
    - domain: str - all mentions in domain
    - concept_id: str - all mentions linked to concept
    """

    # Fetch mentions to reprocess
    mentions = await mention_repo.find_by_filters(filters)

    # Create reprocess job
    job = ReprocessJob(
        id=uuid.uuid4(),
        user_id=current_user.id,
        mention_count=len(mentions),
        status="pending"
    )
    await job_repo.create(job)

    # Queue for background processing
    for mention in mentions:
        # Unlink from current concept (save in checkpoint)
        await checkpoint.save_before_reprocess(mention)

        # Re-run matching workflow
        await matching_workflow(mention)

    return {"job_id": job.id, "mention_count": len(mentions)}
```

**Safety:**
- Checkpoints created before reprocessing
- Can rollback if reprocess produces worse results
- Audit log records reprocess operations
- Requires admin approval

### 8.5 Rollback

**Rollback Capabilities:**

1. **Single Mention Rollback**: Undo concept assignment
2. **Batch Rollback**: Undo all changes from reprocess job
3. **Time-Based Rollback**: Undo all changes after timestamp
4. **Concept Rollback**: Revert concept to previous version

**Implementation:**

```python
@router.post("/admin/rollback")
@require_role("admin")
async def rollback_changes(
    rollback_request: RollbackRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Rollback changes with audit trail preservation.

    Types:
    - mention: Rollback single mention to previous concept
    - batch: Rollback all changes from job_id
    - time_based: Rollback all changes after timestamp
    - concept_version: Rollback concept to version N
    """

    # Fetch checkpoint data
    checkpoints = await checkpoint_repo.get_for_rollback(rollback_request)

    # Apply rollback
    for cp in checkpoints:
        # Restore from checkpoint
        await restore_from_checkpoint(cp)

        # Log rollback action
        await audit_log.record(
            user_id=current_user.id,
            action="rollback",
            resource_id=cp.trace_id,
            details={"checkpoint": cp.dict(), "reason": rollback_request.reason}
        )

    return {"rolled_back_count": len(checkpoints)}
```

**Audit Trail Preservation:**
- Original actions remain in audit log
- Rollback creates new audit entry
- Can see full history: original action → rollback → current state
- Supports compliance requirements

### 8.6 Blacklist System

**Purpose:** Permanently block incorrect concept assignments that agents keep suggesting.

**Use Cases:**
- Prevent false positive that agents consistently make
- Block problematic paper/concept pairs
- Remember human rejections to avoid re-suggesting

**Data Model:**

```python
class BlacklistEntry(BaseModel):
    id: str
    mention_id: Optional[str]  # Specific mention (if applicable)
    concept_id: Optional[str]  # Specific concept (if applicable)
    pattern: Optional[str]  # Text pattern to block (e.g., "keyword in statement")
    reason: str  # Why blocked
    created_by: str  # User who blacklisted
    created_at: datetime
    never_allow: bool = True  # Permanent block

# Neo4j representation
CREATE (b:BlacklistEntry {
    id: $id,
    mention_id: $mention_id,
    concept_id: $concept_id,
    pattern: $pattern,
    reason: $reason,
    created_by: $created_by,
    created_at: datetime(),
    never_allow: true
})
```

**Enforcement:**

```python
class ConceptMatcher:
    async def find_candidates(self, mention: ProblemMention) -> list[dict]:
        # Find similar concepts
        candidates = await self.similarity_search(mention.embedding)

        # Filter out blacklisted
        filtered = []
        for candidate in candidates:
            is_blacklisted = await self.check_blacklist(
                mention_id=mention.id,
                concept_id=candidate['concept_id']
            )

            if not is_blacklisted:
                filtered.append(candidate)
            else:
                logger.info(
                    "Filtered blacklisted candidate",
                    extra={
                        "mention_id": mention.id,
                        "concept_id": candidate['concept_id'],
                        "reason": "blacklist"
                    }
                )

        return filtered

    async def check_blacklist(
        self,
        mention_id: str,
        concept_id: str
    ) -> bool:
        """Check if mention-concept pair is blacklisted."""
        result = await self.neo4j.run("""
            MATCH (b:BlacklistEntry)
            WHERE (b.mention_id = $mention_id AND b.concept_id = $concept_id)
               OR (b.mention_id = $mention_id AND b.concept_id IS NULL)
               OR (b.concept_id = $concept_id AND b.mention_id IS NULL)
            RETURN b
            LIMIT 1
        """, mention_id=mention_id, concept_id=concept_id)

        return result.single() is not None
```

**UI:**
- Easy "Block this match" button in review queue
- Shows blacklist reason when filtering candidates
- Admin page to view and manage blacklist entries
- Can remove from blacklist with justification

**Rationale:**
- Prevents wasted agent compute on known bad matches
- Learns from human feedback permanently
- Reduces review queue size by filtering obvious mistakes

### 8.7 Disaster Recovery

**Status:** Important future work, to be designed separately

**Considerations:**
- Neo4j backup and restore procedures
- Point-in-time recovery (PITR)
- Redis queue recovery
- Checkpoint-based recovery for in-flight work items
- RTO/RPO targets (Recovery Time/Point Objective)
- Geo-redundancy for critical data

**Reference:** ADR-XXX: Disaster Recovery Plan (to be created)

---

## 9. Review Queue System

### 9.1 Options Analysis

**Option A: Graph-Native (PendingReview Node)**

Pros:
- Natural fit with Neo4j graph structure
- Can query relationships (e.g., "reviews for papers citing X")
- Single source of truth
- Graph queries can filter by domain, priority, etc.

Cons:
- Neo4j not optimized for queue operations
- Harder to implement concurrency control (assignment locking)
- No built-in notification system

**Option B: Separate Queue Table (PostgreSQL)**

Pros:
- Better queue semantics (FIFO, priority, locking)
- Easier concurrency control
- Can use triggers for notifications
- Clearer separation of concerns

Cons:
- Dual-database complexity
- Need to keep in sync with Neo4j
- Harder to query graph relationships

**Option C: Hybrid (Recommended)**

**Decision: Use Graph-Native with Redis for Queue Management**

Structure:
- **Neo4j**: Store PendingReview nodes for historical record and relationships
- **Redis**: Maintain active queue with priority, assignment, and locking
- **Sync**: When review created → add to Neo4j + push to Redis queue

Implementation:
```python
class ReviewQueue:
    """Hybrid review queue using Neo4j + Redis."""

    def __init__(self, neo4j_repo: Neo4jRepository, redis_client: Redis):
        self.neo4j = neo4j_repo
        self.redis = redis_client

    async def enqueue(self, mention: ProblemMention, candidates: list[dict], priority: int = 5):
        """Add review to queue."""
        # Create PendingReview node in Neo4j
        review = PendingReview(
            mention_id=mention.id,
            suggested_concepts=candidates,
            priority=priority
        )
        await self.neo4j.create_pending_review(review)

        # Push to Redis sorted set (score = priority)
        await self.redis.zadd(
            "review_queue",
            {review.id: priority}
        )

        return review.id

    async def dequeue(self, assigned_to: str) -> Optional[PendingReview]:
        """Get next review from queue."""
        # Pop highest priority (lowest score) from Redis
        result = await self.redis.zpopmin("review_queue")
        if not result:
            return None

        review_id = result[0][0]

        # Load from Neo4j and lock
        review = await self.neo4j.get_pending_review(review_id)
        review.assigned_to = assigned_to
        await self.neo4j.update_pending_review(review)

        return review

    async def complete(self, review_id: str, decision: dict):
        """Mark review as complete."""
        review = await self.neo4j.get_pending_review(review_id)
        review.reviewed_at = datetime.now(timezone.utc)
        await self.neo4j.update_pending_review(review)

        # Apply decision
        if decision.get('concept_id'):
            await self.link_mention_to_concept(
                review.mention_id,
                decision['concept_id']
            )
        elif decision.get('create_new'):
            await self.create_new_concept_from_mention(review.mention_id)
```

**Rationale:**
- Neo4j provides graph context (e.g., "show reviews for papers in same domain")
- Redis provides efficient queue operations
- Clear separation: Neo4j = historical record, Redis = active queue
- Scales well (Redis can handle high throughput)

### 9.2 Review UI Flow

1. **Queue Page**: List of pending reviews sorted by priority
2. **Review Detail**:
   - Show mention statement + paper context
   - Show suggested concepts with scores
   - Show agent consensus (maker/hater arguments)
   - Options: Link to concept | Create new | Reject
3. **Bulk Review**: Approve multiple high-confidence suggestions at once

### 9.3 Workflow State Machine

**State Diagram:**

```
EXTRACTED
    ↓
MATCHING (find similar concepts via embedding)
    ↓
    ├─→ HIGH_CONFIDENCE (>95%) → AUTO_LINKED → END
    ├─→ MEDIUM_CONFIDENCE (80-95%) → AGENT_REVIEW
    │                                     ↓
    │                              ├─→ APPROVED → AUTO_LINKED → END
    │                              └─→ NEEDS_CONSENSUS → PENDING_REVIEW
    ├─→ LOW_CONFIDENCE (50-80%) → PENDING_REVIEW
    └─→ NO_MATCH (<50%) → CREATE_NEW_CONCEPT → END

PENDING_REVIEW (human review required)
    ↓
    ├─→ APPROVED (human decision) → AUTO_LINKED → END
    ├─→ REJECTED (human decision) → CREATE_NEW_CONCEPT → END
    └─→ BLACKLISTED (human decision) → BLACKLIST_ENTRY → CREATE_NEW_CONCEPT → END
```

**State Definitions:**

| State | Description | Next States | Duration Target |
|-------|-------------|-------------|-----------------|
| EXTRACTED | Mention created from paper | MATCHING | Immediate |
| MATCHING | Finding candidate concepts | HIGH_CONFIDENCE, MEDIUM_CONFIDENCE, LOW_CONFIDENCE, NO_MATCH | <1s |
| HIGH_CONFIDENCE | Match score >95% | AUTO_LINKED | Immediate |
| MEDIUM_CONFIDENCE | Match score 80-95% | AGENT_REVIEW | <5s |
| LOW_CONFIDENCE | Match score 50-80% | PENDING_REVIEW | <10s |
| NO_MATCH | No good candidates found | CREATE_NEW_CONCEPT | Immediate |
| AGENT_REVIEW | Single agent evaluating | APPROVED, NEEDS_CONSENSUS | <10s |
| NEEDS_CONSENSUS | Multi-agent debate required | PENDING_REVIEW | <30s |
| PENDING_REVIEW | Waiting for human decision | APPROVED, REJECTED, BLACKLISTED | <7 days |
| AUTO_LINKED | Successfully linked to concept | END | Immediate |
| CREATE_NEW_CONCEPT | Creating new canonical concept | END | <5s |
| BLACKLISTED | Permanently blocked pairing | CREATE_NEW_CONCEPT | Immediate |

**State Transition Triggers:**

```python
class WorkflowStateMachine:
    """Manages state transitions for mention matching workflow."""

    TRANSITIONS = {
        "EXTRACTED": ["MATCHING"],
        "MATCHING": ["HIGH_CONFIDENCE", "MEDIUM_CONFIDENCE", "LOW_CONFIDENCE", "NO_MATCH"],
        "HIGH_CONFIDENCE": ["AUTO_LINKED"],
        "MEDIUM_CONFIDENCE": ["AGENT_REVIEW"],
        "LOW_CONFIDENCE": ["PENDING_REVIEW"],
        "NO_MATCH": ["CREATE_NEW_CONCEPT"],
        "AGENT_REVIEW": ["APPROVED", "NEEDS_CONSENSUS"],
        "NEEDS_CONSENSUS": ["PENDING_REVIEW"],
        "APPROVED": ["AUTO_LINKED"],
        "PENDING_REVIEW": ["APPROVED", "REJECTED", "BLACKLISTED"],
        "REJECTED": ["CREATE_NEW_CONCEPT"],
        "BLACKLISTED": ["CREATE_NEW_CONCEPT"],
        "AUTO_LINKED": [],  # Terminal state
        "CREATE_NEW_CONCEPT": []  # Terminal state
    }

    async def transition(
        self,
        work_item: WorkItem,
        new_state: str,
        reason: str,
        metadata: Optional[dict] = None
    ):
        """Execute state transition with validation."""
        current_state = work_item.current_state

        # Validate transition is allowed
        if new_state not in self.TRANSITIONS[current_state]:
            raise InvalidTransitionError(
                f"Cannot transition from {current_state} to {new_state}"
            )

        # Create checkpoint before transition
        await checkpoint.save(
            trace_id=work_item.trace_id,
            stage=current_state,
            state=work_item.dict(),
            mention=work_item.mention
        )

        # Update state
        work_item.current_state = new_state
        work_item.state_history.append({
            "from_state": current_state,
            "to_state": new_state,
            "timestamp": datetime.utcnow(),
            "reason": reason,
            "metadata": metadata or {}
        })

        # Persist to Neo4j
        await self.neo4j.update_work_item(work_item)

        # Log transition
        logger.info(
            "State transition",
            extra={
                "trace_id": work_item.trace_id,
                "from_state": current_state,
                "to_state": new_state,
                "reason": reason
            }
        )

        # Trigger next action
        await self.handle_state_entry(work_item)
```

**Validation Rules:**

- Cannot skip states (must go through workflow sequentially)
- Cannot transition from terminal state (AUTO_LINKED, CREATE_NEW_CONCEPT)
- Checkpoints created before each state transition
- All transitions logged with reason and metadata
- Failed transitions rollback to previous state

**Rollback Conditions:**

- Agent timeout (>60s) → rollback to MATCHING, retry
- API failure during AUTO_LINKED → rollback to previous state
- Invalid decision from agent → rollback to PENDING_REVIEW
- User requests rollback → restore from checkpoint

### 9.4 Work Item Tracking

**Work Item Data Model:**

```python
class WorkItem(BaseModel):
    """Tracks a mention through the matching workflow."""

    # Identity
    id: str  # UUID
    trace_id: str  # Format: {timestamp}-{mention_id}-{operation}
    mention_id: str

    # Current state
    current_state: WorkflowState
    state_history: list[StateTransition]

    # Matching data
    candidate_concepts: list[dict]  # [{concept_id, score, reasoning}]
    selected_concept_id: Optional[str]
    match_method: Optional[MatchMethod]  # AUTO | AGENT | HUMAN

    # Review tracking (if in review queue)
    assigned_to: Optional[str]  # User ID
    assigned_at: Optional[datetime]
    priority: int  # 1=highest, 10=lowest
    sla_deadline: Optional[datetime]

    # Retry tracking
    retry_count: int = 0
    max_retries: int = 3
    last_error: Optional[str]

    # Timestamps
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    # Checkpoints
    checkpoints: list[str]  # List of checkpoint IDs

class StateTransition(BaseModel):
    """Records a state transition."""
    from_state: WorkflowState
    to_state: WorkflowState
    timestamp: datetime
    reason: str
    metadata: dict
    user_id: Optional[str]  # If human decision
```

**Trace ID Format:**

```python
def generate_trace_id(mention_id: str, operation: str = "matching") -> str:
    """
    Generate trace ID for end-to-end tracking.

    Format: {timestamp}-{mention_id_prefix}-{operation}
    Example: 20260209143022-a1b2c3d4-matching
    """
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    mention_prefix = mention_id.split('-')[0][:8]  # First 8 chars
    return f"{timestamp}-{mention_prefix}-{operation}"
```

**Work Item Queries:**

```cypher
// Find work item by trace ID
MATCH (w:WorkItem {trace_id: $trace_id})
RETURN w

// Find all work items for a mention
MATCH (w:WorkItem {mention_id: $mention_id})
RETURN w
ORDER BY w.created_at DESC

// Find work items by state
MATCH (w:WorkItem {current_state: $state})
RETURN w
ORDER BY w.created_at ASC

// Find stuck work items (in same state >1 hour)
MATCH (w:WorkItem)
WHERE w.current_state <> 'AUTO_LINKED'
  AND w.current_state <> 'CREATE_NEW_CONCEPT'
  AND duration.between(w.updated_at, datetime()).minutes > 60
RETURN w

// Find work items with high retry count
MATCH (w:WorkItem)
WHERE w.retry_count >= 2
RETURN w
ORDER BY w.retry_count DESC
```

**Monitoring Dashboard:**

- Real-time count of work items by state
- Average time spent in each state
- Work items exceeding SLA
- Failed work items requiring attention
- Daily throughput (completed work items per day)

### 9.5 Priority & SLA Management

**Priority Scoring Algorithm:**

```python
def calculate_priority(
    mention: ProblemMention,
    match_confidence: float,
    concept: Optional[ProblemConcept] = None
) -> int:
    """
    Calculate priority score for review queue.

    Lower score = higher priority (1 is highest).

    Scoring factors:
    - Confidence score (inverted): Lower confidence = higher priority
    - Citation count: More citations = higher priority
    - Domain importance: Critical domains = higher priority
    - Age: Older items = higher priority (after SLA threshold)
    """
    base_priority = 5  # Default medium priority

    # Factor 1: Confidence (inverted)
    # Lower confidence needs more careful review
    confidence_factor = int((1 - match_confidence) * 5)  # 0-5 range

    # Factor 2: Citation count
    # High-impact papers prioritized
    citation_count = concept.mention_count if concept else 0
    citation_factor = -1 if citation_count > 10 else 0

    # Factor 3: Domain importance
    # Critical domains get priority
    domain_factor = -2 if mention.domain in ["NLP", "CV", "RL"] else 0

    # Factor 4: Age escalation
    # Items pending >7 days get priority boost
    age_days = (datetime.utcnow() - mention.created_at).days
    age_factor = -3 if age_days > 7 else 0

    # Calculate final priority (clamp to 1-10)
    priority = base_priority + confidence_factor + citation_factor + domain_factor + age_factor
    return max(1, min(10, priority))
```

**SLA Tracking:**

| Queue | SLA Target | Action on Breach |
|-------|------------|------------------|
| High Priority (1-3) | 24 hours | Alert admin |
| Medium Priority (4-6) | 7 days | Escalate to high priority |
| Low Priority (7-10) | 30 days | Move to backlog |

**SLA Monitoring:**

```python
class SLAMonitor:
    """Monitor and enforce SLA for review queue."""

    async def check_sla_breaches(self):
        """Find work items exceeding SLA."""
        now = datetime.utcnow()

        # Query work items in PENDING_REVIEW state
        work_items = await self.neo4j.run("""
            MATCH (w:WorkItem {current_state: 'PENDING_REVIEW'})
            RETURN w
        """)

        breaches = []
        for item in work_items:
            # Calculate SLA deadline based on priority
            sla_hours = self.get_sla_hours(item.priority)
            deadline = item.created_at + timedelta(hours=sla_hours)

            if now > deadline:
                breaches.append({
                    "work_item_id": item.id,
                    "trace_id": item.trace_id,
                    "priority": item.priority,
                    "age_hours": (now - item.created_at).total_seconds() / 3600,
                    "sla_hours": sla_hours,
                    "breach_hours": (now - deadline).total_seconds() / 3600
                })

        return breaches

    async def escalate_breaches(self, breaches: list[dict]):
        """Escalate SLA breaches."""
        for breach in breaches:
            work_item = await self.neo4j.get_work_item(breach["work_item_id"])

            # Escalate priority
            old_priority = work_item.priority
            new_priority = max(1, old_priority - 3)  # Boost by 3 levels

            work_item.priority = new_priority

            # Update in Neo4j and Redis
            await self.neo4j.update_work_item(work_item)
            await self.redis.zadd("review_queue", {work_item.id: new_priority})

            # Send alert
            await self.send_alert(
                f"SLA breach: Work item {work_item.trace_id} escalated from priority {old_priority} to {new_priority}"
            )

    def get_sla_hours(self, priority: int) -> int:
        """Get SLA hours for priority level."""
        sla_map = {
            1: 24, 2: 24, 3: 24,      # High: 24 hours
            4: 168, 5: 168, 6: 168,   # Medium: 7 days
            7: 720, 8: 720, 9: 720, 10: 720  # Low: 30 days
        }
        return sla_map.get(priority, 168)
```

**Timeout Handling:**

```python
async def handle_stuck_work_items():
    """Handle work items stuck in non-terminal states."""

    # Find stuck items (in same state >1 hour)
    stuck_items = await neo4j.run("""
        MATCH (w:WorkItem)
        WHERE w.current_state <> 'AUTO_LINKED'
          AND w.current_state <> 'CREATE_NEW_CONCEPT'
          AND duration.between(datetime(w.updated_at), datetime()).minutes > 60
        RETURN w
    """)

    for item in stuck_items:
        if item.retry_count < item.max_retries:
            # Retry the workflow
            item.retry_count += 1
            await workflow.retry_from_checkpoint(item.trace_id)
        else:
            # Max retries exceeded, move to manual review
            await workflow.transition(
                work_item=item,
                new_state="PENDING_REVIEW",
                reason="Max retries exceeded, requires human review"
            )

            # Alert admin
            await send_alert(
                f"Work item {item.trace_id} stuck after {item.retry_count} retries"
            )
```

### 9.6 Draft Saves & Checkpointing

**Checkpoint Strategy:**

Save state at each critical workflow stage:

1. **After Extraction** (`EXTRACTED` state)
   - Save: ProblemMention with all metadata
   - Purpose: Can reprocess if matching fails

2. **After Matching** (`MATCHING` complete)
   - Save: Candidate concepts with scores
   - Purpose: Can re-evaluate matches without re-running similarity search

3. **After Agent Review** (`AGENT_REVIEW` complete)
   - Save: Agent consensus and decision
   - Purpose: Can audit agent decisions, retry with different prompts

4. **Before Final Commit** (before `AUTO_LINKED`)
   - Save: Complete work item state
   - Purpose: Can rollback if commit fails

**Implementation:**

```python
class WorkflowWithCheckpoints:
    """Workflow with automatic checkpointing."""

    async def process_mention(self, mention: ProblemMention):
        """Process mention through workflow with checkpoints."""
        trace_id = generate_trace_id(mention.id, "matching")

        # Create work item
        work_item = WorkItem(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            mention_id=mention.id,
            current_state=WorkflowState.EXTRACTED
        )

        try:
            # Checkpoint 1: After extraction
            cp1 = await checkpoint.save(
                trace_id=trace_id,
                stage="extraction",
                state={"mention": mention.dict()},
                mention=mention
            )
            work_item.checkpoints.append(cp1.id)

            # Transition to MATCHING
            await state_machine.transition(work_item, "MATCHING", "Starting matching")

            # Find candidates
            candidates = await matcher.find_matching_concepts(mention)

            # Checkpoint 2: After matching
            cp2 = await checkpoint.save(
                trace_id=trace_id,
                stage="matching",
                state={"candidates": candidates},
                mention=mention
            )
            work_item.checkpoints.append(cp2.id)
            work_item.candidate_concepts = candidates

            # Classify confidence
            if not candidates:
                # No match
                await state_machine.transition(work_item, "NO_MATCH", "No candidates found")
                await self.create_new_concept(mention)
                return

            top_match = candidates[0]
            confidence = matcher.classify_confidence(top_match['score'])

            if confidence == MatchConfidence.HIGH:
                # High confidence: auto-link
                await state_machine.transition(work_item, "HIGH_CONFIDENCE", f"Confidence: {top_match['score']}")

                # Checkpoint 3: Before commit
                cp3 = await checkpoint.save(
                    trace_id=trace_id,
                    stage="before_commit",
                    state={"decision": "auto_link", "concept_id": top_match['concept_id']},
                    mention=mention
                )
                work_item.checkpoints.append(cp3.id)

                # Final commit
                await self.link_mention_to_concept(mention.id, top_match['concept_id'])
                await state_machine.transition(work_item, "AUTO_LINKED", "Successfully linked")

            elif confidence == MatchConfidence.MEDIUM:
                # Medium confidence: agent review
                await state_machine.transition(work_item, "MEDIUM_CONFIDENCE", f"Confidence: {top_match['score']}")

                # Run evaluator agent
                decision = await evaluator_agent.review(mention, top_match)

                # Checkpoint 4: After agent review
                cp4 = await checkpoint.save(
                    trace_id=trace_id,
                    stage="agent_review",
                    state={"decision": decision},
                    mention=mention
                )
                work_item.checkpoints.append(cp4.id)

                if decision['approved']:
                    await state_machine.transition(work_item, "APPROVED", "Agent approved")
                    await self.link_mention_to_concept(mention.id, top_match['concept_id'])
                    await state_machine.transition(work_item, "AUTO_LINKED", "Successfully linked")
                else:
                    await state_machine.transition(work_item, "NEEDS_CONSENSUS", "Agent rejected, needs consensus")
                    await self.enqueue_for_review(work_item, candidates)

            else:
                # Low confidence: pending review
                await state_machine.transition(work_item, "LOW_CONFIDENCE", f"Confidence: {top_match['score']}")
                await self.enqueue_for_review(work_item, candidates)

        except Exception as e:
            logger.error(f"Workflow failed: {e}", extra={"trace_id": trace_id})

            # Rollback to last checkpoint
            await self.rollback_to_checkpoint(work_item, work_item.checkpoints[-1])

            # Retry or escalate
            if work_item.retry_count < work_item.max_retries:
                work_item.retry_count += 1
                await self.process_mention(mention)  # Retry
            else:
                await self.enqueue_for_review(work_item, candidates)  # Escalate to human
```

**Rollback Implementation:**

```python
async def rollback_to_checkpoint(
    work_item: WorkItem,
    checkpoint_id: str
):
    """Rollback work item to previous checkpoint."""

    # Load checkpoint
    checkpoint = await checkpoint_repo.get(checkpoint_id)

    # Restore mention state
    restored_mention = ProblemMention(**checkpoint.state['mention'])

    # Update work item to checkpoint state
    work_item.current_state = checkpoint.stage
    work_item.updated_at = datetime.utcnow()

    # Add to state history
    work_item.state_history.append({
        "from_state": work_item.current_state,
        "to_state": checkpoint.stage,
        "timestamp": datetime.utcnow(),
        "reason": "Rollback to checkpoint",
        "metadata": {"checkpoint_id": checkpoint_id}
    })

    # Persist
    await neo4j.update_work_item(work_item)

    # Log rollback
    logger.warning(
        "Rolled back to checkpoint",
        extra={
            "trace_id": work_item.trace_id,
            "checkpoint_id": checkpoint_id,
            "stage": checkpoint.stage
        }
    )
```

**Draft State:**

Work items in `PENDING_REVIEW` state are considered "draft":
- Not visible in production concept queries
- Can be edited by reviewers before approval
- Changes saved as draft until final commit
- Final commit transitions to `AUTO_LINKED` or `CREATE_NEW_CONCEPT`

---

## 10. Verification Strategy

### 10.1 Testing Approach

**Phase 1: Unit Tests**
- Test data models (validation, serialization)
- Test embedding similarity calculation
- Test confidence classification
- Test agent prompts (mock LLM responses)

**Phase 2: Integration Tests**
- Test full extraction → matching → storage pipeline
- Test citation boost logic
- Test synthesis agent
- Test review queue operations

**Phase 3: End-to-End Tests**
- Import real papers with known overlapping problems
- Validate concept linking accuracy
- Measure false positive/negative rates

**Test Domain:** Computer Science - Knowledge Graph Retrieval

**Test Data:**
- Import 1 seed paper + 2 hops of citations
- Manually identify ground truth concepts
- Compare system output to ground truth

### 10.2 Acceptance Criteria

**Functional:**
- [ ] Paper import creates ProblemMentions (not Problems)
- [ ] High-confidence matches (>95%) auto-link to concepts
- [ ] Medium-confidence matches (80-95%) go to agent review
- [ ] Low-confidence matches (50-80%) go to multi-agent consensus
- [ ] Synthesis agent creates canonical statements
- [ ] API endpoints return correct aggregated views
- [ ] Review queue allows human approval

**Quality:**
- [ ] False positive rate <5% (incorrectly linked problems)
- [ ] False negative rate <1% (missed duplicates)
- [ ] Canonical statements are clear and accurate
- [ ] Metadata provenance is preserved

**Performance:**
- [ ] Concept lookup query <500ms
- [ ] Embedding similarity search <1s
- [ ] Agent consensus completes within 30s
- [ ] Synthesis completes within 60s

### 10.3 Testing Process Documentation

**Step 1: Select Test Papers**
```
1. Choose seed paper: "Neural Knowledge Graph Reasoning" (example)
2. Identify 2-3 cited papers that mention similar problems
3. Manually extract ground truth:
   - Expected concepts (e.g., "Knowledge Graph Completion")
   - Expected mentions per paper
   - Expected links between mentions and concepts
```

**Step 2: Import and Validate**
```
1. Import seed paper
2. Verify ProblemMentions created (not Problem nodes)
3. Check first mention creates new ProblemConcept
4. Import cited papers
5. Verify mentions link to existing concept (if ground truth says they should)
6. Check for false positives (incorrect links)
```

**Step 3: Review Queue Testing**
```
1. Check pending reviews for low-confidence matches
2. Review agent consensus output
3. Manually approve/reject
4. Verify final graph structure matches expected
```

**Step 4: Query Testing**
```
1. Query: "Show me research on Knowledge Graph Completion"
2. Expected: Canonical concept + N mentions + list of papers
3. Verify mention count, paper count accurate
4. Verify metadata aggregation correct
```

### 10.4 Golden Dataset Testing

**Purpose:** Establish ground truth for evaluating matching accuracy with curated test cases.

**Dataset Composition:** 20 curated test cases covering edge cases

**Test Case Categories:**

1. **Obvious Duplicates** (4 cases)
   - Identical problem statements with minor wording differences
   - Example: "Hallucination in LLMs" vs "LLM hallucination problem"
   - Expected: >99% similarity, auto-link

2. **Subtle Paraphrases** (4 cases)
   - Same problem, significantly different phrasing
   - Example: "Lack of explainability in neural networks" vs "Black-box nature of deep learning models"
   - Expected: 85-95% similarity, agent review required

3. **Domain Overlaps** (3 cases)
   - Similar problems in different domains
   - Example: "Data quality issues in NLP" vs "Data quality in computer vision"
   - Expected: 70-85% similarity, should create separate concepts

4. **Citation-Strengthened Matches** (3 cases)
   - Papers cite each other and mention similar problems
   - Expected: Citation boost increases match confidence by 10%

5. **False Positive Traps** (3 cases)
   - Similar wording but fundamentally different problems
   - Example: "Model compression" (reducing size) vs "Model compression" (data compression in models)
   - Expected: Should NOT link, create separate concepts

6. **Temporal Evolution** (3 cases)
   - Same problem evolving over time
   - Example: "Pre-training compute efficiency" (2018) vs "Green AI and carbon footprint" (2023)
   - Expected: Link to same concept, track temporal progression

**Test Execution:**

```python
class GoldenDatasetTest:
    """Test suite for golden dataset validation."""

    def __init__(self, golden_dataset_path: str):
        self.dataset = self.load_dataset(golden_dataset_path)

    async def run_test_case(self, test_case: dict) -> dict:
        """
        Run single test case through matching pipeline.

        Returns:
        {
            "test_id": str,
            "mention_1": str,
            "mention_2": str,
            "expected_link": bool,
            "actual_link": bool,
            "confidence_score": float,
            "passed": bool,
            "failure_reason": Optional[str]
        }
        """
        mention_1 = test_case["mention_1"]
        mention_2 = test_case["mention_2"]
        expected = test_case["expected_link"]

        # Run through matching pipeline
        concept_1 = await self.extract_and_match(mention_1)
        concept_2 = await self.extract_and_match(mention_2)

        actual = (concept_1.id == concept_2.id)

        return {
            "test_id": test_case["id"],
            "mention_1": mention_1,
            "mention_2": mention_2,
            "expected_link": expected,
            "actual_link": actual,
            "confidence_score": concept_2.match_score if concept_2 else 0.0,
            "passed": (expected == actual),
            "failure_reason": None if (expected == actual) else "Link mismatch"
        }

    async def run_full_suite(self, iterations: int = 5) -> dict:
        """
        Run all test cases N times, compute aggregate metrics.

        Pass criteria: 4/5 runs must agree on outcome.
        """
        results = []

        for i in range(iterations):
            logger.info(f"Golden dataset run {i+1}/{iterations}")

            run_results = []
            for test_case in self.dataset:
                result = await self.run_test_case(test_case)
                run_results.append(result)

            results.append(run_results)

        # Compute consensus
        test_outcomes = {}
        for test_id in [tc["id"] for tc in self.dataset]:
            outcomes = [
                r["passed"]
                for run in results
                for r in run if r["test_id"] == test_id
            ]
            consensus = sum(outcomes) >= 4  # 4/5 runs passed
            test_outcomes[test_id] = {
                "passed_count": sum(outcomes),
                "total_runs": len(outcomes),
                "consensus_pass": consensus
            }

        # Aggregate metrics
        total_tests = len(self.dataset)
        passed_tests = sum(1 for outcome in test_outcomes.values() if outcome["consensus_pass"])

        return {
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "pass_rate": passed_tests / total_tests,
            "test_outcomes": test_outcomes,
            "overall_pass": (passed_tests == total_tests)
        }
```

**Test Data Location:** `construction/test-data/golden-dataset.json`

**Example Test Case:**
```json
{
  "id": "test-001",
  "category": "obvious_duplicate",
  "mention_1": {
    "statement": "Large language models suffer from hallucination",
    "domain": "NLP",
    "paper_doi": "10.1234/paper1"
  },
  "mention_2": {
    "statement": "LLMs produce hallucinated outputs",
    "domain": "NLP",
    "paper_doi": "10.1234/paper2"
  },
  "expected_link": true,
  "rationale": "Same problem, minor wording difference"
}
```

### 10.5 Consensus Testing Methodology

**Maker/Hater Model Validation:**

**Test Objectives:**
- Validate that maker/hater debate produces reliable decisions
- Measure agreement rate between agent consensus and human judgment
- Tune agent prompts to minimize false positives and false negatives

**Testing Process:**

```python
class ConsensusValidator:
    """Validate maker/hater consensus model."""

    async def validate_decision(
        self,
        mention: ProblemMention,
        concept: ProblemConcept,
        ground_truth: bool
    ) -> dict:
        """
        Run maker/hater debate and compare to ground truth.

        Args:
            mention: Problem mention to evaluate
            concept: Candidate concept
            ground_truth: Should they be linked? (from golden dataset)

        Returns:
            {
                "maker_args": str,
                "hater_args": str,
                "consensus_decision": bool,
                "consensus_confidence": float,
                "ground_truth": bool,
                "correct": bool,
                "error_type": Optional["false_positive" | "false_negative"]
            }
        """
        # Run maker agent
        maker_result = await maker_agent.argue_for_match(mention, concept)

        # Run hater agent
        hater_result = await hater_agent.argue_against_match(mention, concept)

        # Run consensus agent
        consensus_result = await consensus_agent.decide(
            maker_args=maker_result["arguments"],
            hater_args=hater_result["arguments"]
        )

        decision = consensus_result["decision"] == "LINK"
        correct = (decision == ground_truth)

        error_type = None
        if not correct:
            if decision and not ground_truth:
                error_type = "false_positive"
            elif not decision and ground_truth:
                error_type = "false_negative"

        return {
            "maker_args": maker_result["arguments"],
            "hater_args": hater_result["arguments"],
            "consensus_decision": decision,
            "consensus_confidence": consensus_result["confidence"],
            "ground_truth": ground_truth,
            "correct": correct,
            "error_type": error_type
        }

    async def compute_error_rates(
        self,
        golden_dataset: list[dict]
    ) -> dict:
        """Compute false positive and false negative rates."""
        results = []

        for test_case in golden_dataset:
            mention = test_case["mention"]
            concept = test_case["concept"]
            ground_truth = test_case["expected_link"]

            result = await self.validate_decision(mention, concept, ground_truth)
            results.append(result)

        false_positives = sum(1 for r in results if r["error_type"] == "false_positive")
        false_negatives = sum(1 for r in results if r["error_type"] == "false_negative")
        total = len(results)

        return {
            "false_positive_rate": false_positives / total,
            "false_negative_rate": false_negatives / total,
            "accuracy": sum(1 for r in results if r["correct"]) / total,
            "results": results
        }
```

**Acceptance Thresholds:**

| Metric | Target | Critical |
|--------|--------|----------|
| False Positive Rate | <5% | Must not exceed 10% |
| False Negative Rate | 0% | Absolutely no false negatives allowed |
| Overall Accuracy | >95% | >90% minimum |

**Rationale for Asymmetric Thresholds:**
- **False positives** (linking unrelated problems): Annoying but correctable via review queue
- **False negatives** (missing duplicates): Defeats the purpose of canonical architecture, creates fragmentation

### 10.6 Performance Benchmarks

**Target Metrics:**

| Operation | Target | Max Acceptable |
|-----------|--------|----------------|
| Concept Matching | <100ms per mention | <200ms |
| Review Queue Query | <50ms | <100ms |
| Full Extraction Pipeline | <30s per paper | <60s |
| Agent Consensus | <10s per decision | <30s |
| Synthesis | <5s per concept | <15s |
| Vector Similarity Search | <100ms for top-10 | <500ms |
| API Response Time (p95) | <500ms | <1s |

**Load Testing:**

```python
class PerformanceTest:
    """Performance benchmark suite."""

    async def benchmark_matching(self, mention_count: int = 100):
        """Benchmark concept matching speed."""
        mentions = await self.generate_test_mentions(mention_count)

        start = time.time()
        for mention in mentions:
            candidates = await matcher.find_matching_concepts(mention)
        elapsed = time.time() - start

        avg_time = (elapsed / mention_count) * 1000  # ms per mention

        return {
            "operation": "concept_matching",
            "total_mentions": mention_count,
            "total_time_s": elapsed,
            "avg_time_ms": avg_time,
            "target_ms": 100,
            "passed": avg_time < 100
        }

    async def benchmark_review_queue(self, query_count: int = 100):
        """Benchmark review queue query speed."""
        times = []

        for _ in range(query_count):
            start = time.time()
            await review_queue.get_pending_reviews(limit=20)
            elapsed = (time.time() - start) * 1000  # ms
            times.append(elapsed)

        avg = sum(times) / len(times)
        p95 = sorted(times)[int(len(times) * 0.95)]

        return {
            "operation": "review_queue_query",
            "query_count": query_count,
            "avg_time_ms": avg,
            "p95_time_ms": p95,
            "target_ms": 50,
            "passed": p95 < 50
        }

    async def run_full_benchmark_suite(self):
        """Run all performance benchmarks."""
        results = [
            await self.benchmark_matching(),
            await self.benchmark_review_queue(),
            await self.benchmark_full_pipeline(),
            await self.benchmark_agent_consensus(),
            await self.benchmark_synthesis()
        ]

        all_passed = all(r["passed"] for r in results)

        return {
            "results": results,
            "all_passed": all_passed,
            "summary": self.generate_summary(results)
        }
```

**Continuous Monitoring:**
- Run performance tests in CI/CD before deployment
- Alert if performance degrades >20% from baseline
- Track trends over time (performance regression detection)

### 10.7 Property-Based Testing

**Purpose:** Generate synthetic test cases to explore edge cases and boundary conditions.

**Strategy:** Use property-based testing (Hypothesis library) to generate problem pairs and test matching behavior.

**Properties to Test:**

1. **Reflexivity**: A problem mention should match itself with 100% confidence
2. **Symmetry**: If A matches B, then B should match A (with same confidence)
3. **Transitivity**: If A matches B and B matches C, evaluate if A should match C
4. **Threshold Consistency**: Problems just above/below similarity threshold should behave predictably
5. **Embedding Stability**: Small text perturbations shouldn't drastically change similarity

**Implementation:**

```python
from hypothesis import given, strategies as st

class PropertyBasedTests:
    """Property-based tests for concept matching."""

    @given(st.text(min_size=20, max_size=200))
    async def test_reflexivity(self, problem_text: str):
        """Test that a problem matches itself."""
        mention = ProblemMention(statement=problem_text, ...)

        # Generate embedding
        embedding = await self.embedding_service.embed(problem_text)

        # Check similarity with itself
        similarity = cosine_similarity(embedding, embedding)

        assert similarity > 0.99, f"Reflexivity failed: {similarity}"

    @given(
        st.text(min_size=20, max_size=200),
        st.text(min_size=20, max_size=200)
    )
    async def test_symmetry(self, text_a: str, text_b: str):
        """Test that similarity is symmetric."""
        emb_a = await self.embedding_service.embed(text_a)
        emb_b = await self.embedding_service.embed(text_b)

        sim_ab = cosine_similarity(emb_a, emb_b)
        sim_ba = cosine_similarity(emb_b, emb_a)

        assert abs(sim_ab - sim_ba) < 0.001, f"Symmetry failed: {sim_ab} vs {sim_ba}"

    async def test_threshold_edge_cases(self):
        """Test behavior at similarity thresholds (94%, 95%, 96%)."""
        # Generate pairs with specific similarity scores
        test_cases = [
            (0.94, "MEDIUM"),  # Just below high threshold
            (0.95, "HIGH"),    # Exactly at high threshold
            (0.96, "HIGH"),    # Just above high threshold
            (0.79, "MEDIUM"),  # Just below medium threshold
            (0.80, "MEDIUM"),  # Exactly at medium threshold
            (0.81, "MEDIUM"),  # Just above medium threshold
        ]

        for target_similarity, expected_confidence in test_cases:
            # Generate mention pair with target similarity
            mention_a, mention_b = await self.generate_similar_pair(target_similarity)

            # Run matching
            candidates = await matcher.find_matching_concepts(mention_a)
            top_match = candidates[0] if candidates else None

            if top_match:
                confidence = matcher.classify_confidence(top_match['score'])
                assert confidence.value == expected_confidence, \
                    f"Threshold test failed: {target_similarity} -> {confidence.value}, expected {expected_confidence}"

    async def test_embedding_stability(self):
        """Test that small text changes don't drastically change similarity."""
        base_text = "Large language models suffer from hallucination problems"

        # Generate perturbations
        perturbations = [
            "Large language models suffer from hallucination issues",  # Synonym
            "LLMs suffer from hallucination problems",  # Abbreviation
            "Large language models suffer from hallucination problems.",  # Punctuation
            "Large language models experience hallucination problems",  # Similar verb
        ]

        base_emb = await self.embedding_service.embed(base_text)

        for perturbed_text in perturbations:
            perturbed_emb = await self.embedding_service.embed(perturbed_text)
            similarity = cosine_similarity(base_emb, perturbed_emb)

            assert similarity > 0.90, \
                f"Stability test failed: '{perturbed_text}' similarity={similarity}"
```

**Failure Analysis:**
- When property tests fail, save failing examples to golden dataset
- Use failures to refine agent prompts or similarity thresholds
- Track failure patterns to identify systematic issues

---

## 11. UX Specifications

### 11.1 Perfect Quality Requirement

**Core Principle:** Users must trust the system accuracy completely. There is no tolerance for bad matches.

**Rationale:**
- Research depends on accurate knowledge graphs
- False links corrupt research insights
- Trust is earned through consistent quality
- One bad match can undermine confidence in the entire system

**Quality Standards:**
- False positive rate <5% (incorrectly linked problems)
- False negative rate 0% (missed correct links)
- Every match decision must be explainable
- Users can always challenge and correct decisions

**Quality Assurance:**
- Golden dataset testing before deployment
- Continuous monitoring of match quality
- User feedback loop to identify issues
- Quarterly review of agent performance

### 11.2 Transparency

**What Users See:**

1. **Confidence Scores**
   - Display for every concept-mention link
   - Visual indicators:
     - Green badge: >95% confidence (auto-linked)
     - Yellow badge: 80-95% confidence (agent reviewed)
     - Orange badge: <80% confidence (multi-agent consensus)
   - Hovering shows exact score (e.g., "Confidence: 0.87")

2. **Agent Agreement**
   - Show which agents agreed/disagreed on low-confidence matches
   - Example: "✓ Evaluator: Approve | ✓ Maker: Link | ✗ Hater: Reject | → Consensus: Link (confidence: 0.72)"
   - Display agent reasoning summaries

3. **Match Reasoning**
   - Show why system linked mention to concept
   - Example:
     ```
     Matched based on:
     - Embedding similarity: 0.94
     - Citation boost: +0.03 (Paper A cites Paper B)
     - Domain match: NLP
     - Overlapping metrics: F1, BLEU
     ```

4. **Agent Debate Transcripts**
   - For low-confidence cases, show full maker/hater debate
   - Collapsible section: "View Agent Discussion"
   - Include arguments for and against linking

**UI Components:**

```typescript
// Confidence Badge Component
<ConfidenceBadge
  score={0.94}
  matchMethod="auto_linked"
  tooltip="High confidence match (>95%). Automatically linked based on embedding similarity."
/>

// Match Details Panel
<MatchDetails
  mentionId={mention.id}
  conceptId={concept.id}
  confidence={0.87}
  reasoning={{
    embeddingSimilarity: 0.84,
    citationBoost: 0.03,
    domainMatch: true,
    agentReview: "approved"
  }}
  agentDebate={agentDebateData}  // For low-confidence matches
/>
```

### 11.3 Traceability

**Navigation Flows:**

1. **Concept → Mentions**
   - Click any canonical problem
   - See list of all mentions with source papers
   - Each mention shows:
     - Original statement from paper
     - Paper title, DOI, year
     - Match confidence
     - Match method (auto/agent/human)

2. **Mention → Concept**
   - Click any mention
   - See which concept it's linked to
   - Show matching decision trace:
     - Timestamp of matching
     - Candidate concepts considered
     - Why chosen concept was selected
     - Agent decisions (if applicable)

3. **Concept → Related Concepts**
   - Click canonical problem
   - See graph of related concepts
   - Relationships: EXTENDS, CONTRADICTS, DEPENDS_ON
   - Each relationship shows confidence and evidence

**Audit Trail:**
- Every human decision logged
- Show who approved/rejected
- Show when decision was made
- Show reasoning provided
- Historical view: see all changes to a concept over time

**Implementation:**

```typescript
// Concept Detail Page with Traceability
const ConceptDetailPage = ({ conceptId }) => {
  const concept = useConceptDetail(conceptId);

  return (
    <div>
      <ConceptHeader concept={concept} />

      {/* Mentions Section */}
      <Section title={`Mentions (${concept.mention_count})`}>
        {concept.mentions.map(mention => (
          <MentionCard
            key={mention.id}
            mention={mention}
            onClick={() => navigateToTrace(mention.id)}
          >
            <Badge confidence={mention.match_confidence} />
            <Paper doi={mention.paper.doi} title={mention.paper.title} />
            <MatchTrace traceId={mention.trace_id} />  {/* Click to see full trace */}
          </MentionCard>
        ))}
      </Section>

      {/* Audit Trail */}
      <Section title="History">
        <AuditTimeline conceptId={conceptId} />
      </Section>
    </div>
  );
};
```

### 11.4 Quality Indicators

**Visual Cues:**

1. **Confidence Levels**
   - **High (>95%)**: Green badge with checkmark ✓
   - **Medium (80-95%)**: Yellow badge with warning ⚠
   - **Low (<80%)**: Orange badge with question mark ❓
   - **Disputed**: Red badge with alert icon ⚠️
   - **Blacklisted**: Black badge with X mark ✖

2. **Status Badges**
   - **Auto-linked**: Blue badge "AUTO"
   - **Agent Reviewed**: Purple badge "AGENT"
   - **Human Approved**: Green badge "HUMAN"
   - **Pending Review**: Gray badge "PENDING"

3. **Quality Scores**
   - Concept quality score based on:
     - Average confidence of all mentions
     - Number of supporting papers
     - Consensus level (% of papers agreeing)
   - Display as star rating (1-5 stars)

**UI Examples:**

```typescript
// Quality Indicator Component
<QualityIndicator
  confidence={0.94}
  status="auto_linked"
  quality_score={4.5}
  consensus_rate={0.85}
/>

// Renders as:
// [✓ 94%] [AUTO] [★★★★☆ 4.5] [85% consensus]
```

**Problem Indicators:**
- **High variance in mention confidence**: Flag for review
- **Recent rejections**: Show warning if similar matches were rejected
- **Low consensus**: Highlight if <50% of similar mentions agree
- **Synthesis conflicts**: Alert if aggregated metadata conflicts

### 11.5 Correction Mechanisms

**User Actions:**

1. **Reject Wrong Match**
   - Big red "Reject Link" button on mention detail page
   - Requires reason selection:
     - Different problem scope
     - Different domain
     - Incorrect interpretation
     - Other (free text)
   - Unlinks mention from concept
   - Adds to blacklist (optional checkbox)
   - Triggers re-matching workflow

2. **Flag Issues**
   - "Flag for Review" button
   - Issue types:
     - Incorrect canonical statement
     - Missing/wrong metadata
     - Suspicious match
     - Synthesis error
   - Creates issue ticket in review queue
   - Notifies admin

3. **Request Re-matching**
   - "Find Better Match" button
   - Options:
     - Use different threshold (relaxed/strict)
     - Force agent review
     - Suggest specific concept
   - Runs matching workflow with new parameters

4. **Edit Canonical Statement**
   - Editor role can modify canonical statements
   - Human edited flag set to true
   - Prevents automatic re-synthesis
   - Logs edit in audit trail

**Feedback Loop:**

```typescript
// Rejection Flow
const RejectMatchButton = ({ mentionId, conceptId }) => {
  const [showDialog, setShowDialog] = useState(false);

  const handleReject = async (reason, addToBlacklist) => {
    await rejectMatch({
      mention_id: mentionId,
      concept_id: conceptId,
      reason: reason,
      blacklist: addToBlacklist
    });

    // Trigger re-matching
    await triggerReMatch(mentionId);

    // Show success message
    toast.success("Match rejected. Re-matching in progress...");
  };

  return (
    <>
      <Button danger onClick={() => setShowDialog(true)}>
        Reject Link
      </Button>

      <RejectDialog
        open={showDialog}
        onConfirm={handleReject}
        onCancel={() => setShowDialog(false)}
      />
    </>
  );
};
```

### 11.6 Review Queue Interface

**Queue Organization:**

1. **Priority Sorting**
   - Default: Lowest confidence first (most uncertain matches)
   - Options:
     - Highest impact (concepts with many mentions)
     - Oldest first (pending longest)
     - Domain filtered

2. **Batch Operations**
   - Select multiple high-confidence items
   - "Approve All" button
   - "Reject All" button
   - Requires confirmation

3. **Context Display**
   - Side-by-side comparison:
     - Left: Mention from paper
     - Right: Candidate concept
   - Show:
     - Full problem statements
     - Domain, assumptions, constraints
     - Existing mentions of concept
     - Paper metadata (title, authors, year)
   - Highlight differences in red

**Review Actions:**

```typescript
const ReviewQueueItem = ({ review }) => {
  return (
    <Card priority={review.priority}>
      {/* Header */}
      <Header>
        <Badge confidence={review.suggested_concepts[0].score} />
        <Priority level={review.priority} />
        <Domain>{review.mention.domain}</Domain>
      </Header>

      {/* Side-by-side comparison */}
      <Comparison>
        <MentionPanel mention={review.mention} />
        <ConceptPanel concept={review.suggested_concepts[0]} />
      </Comparison>

      {/* Agent consensus (if available) */}
      {review.agent_consensus && (
        <AgentConsensus data={review.agent_consensus} />
      )}

      {/* Actions */}
      <Actions>
        <Button primary onClick={() => approve(review.id, review.suggested_concepts[0].id)}>
          ✓ Link to Concept
        </Button>
        <Button onClick={() => createNew(review.id)}>
          + Create New Concept
        </Button>
        <Button danger onClick={() => reject(review.id)}>
          ✖ Reject & Blacklist
        </Button>
      </Actions>
    </Card>
  );
};
```

**Efficiency Features:**
- Keyboard shortcuts (j/k to navigate, a to approve, r to reject)
- Quick filters by domain, confidence range
- Bulk approve for high-confidence matches (>90%)
- Save progress and resume later
- Review session tracking (time spent, decisions made)

**Analytics:**
- Show reviewer stats: approval rate, average time per review, accuracy
- Highlight consistently rejected candidates (may indicate bad agent prompts)
- Track blacklist growth (monitor for patterns)

---

## 12. Implementation Phases

### Phase 1: Data Model & Core Matching (Sprint 1)

**Goal:** Basic concept/mention architecture with auto-linking

**Tasks:**
1. Create ProblemMention and ProblemConcept Pydantic models
2. Update Neo4j schema (nodes, relationships, indexes)
3. Implement ConceptMatcher with embedding similarity
4. Implement confidence classification (HIGH/MEDIUM/LOW)
5. Implement auto-linking for HIGH confidence
6. Update extraction pipeline to create mentions
7. Unit tests

**Deliverable:** Papers create mentions that auto-link to concepts at >95% similarity

**Success:** Import 2 papers mentioning same problem → 1 concept, 2 mentions

---

### Phase 2: Agent Workflows (Sprint 2)

**Goal:** Add agent-based consensus for edge cases

**Tasks:**
1. Set up LangGraph infrastructure (if not exists)
2. Implement Evaluator agent (MEDIUM confidence)
3. Implement Maker/Hater agents (LOW confidence)
4. Implement Consensus agent
5. Create agent prompts
6. Integrate with matching workflow
7. Integration tests

**Deliverable:** Medium and low-confidence matches go through agent review

**Success:** Import paper with ambiguous problem → agent consensus decides → correct linking

---

### Phase 3: Review Queue & UI (Sprint 3)

**Goal:** Human review workflow

**Tasks:**
1. Implement PendingReview model
2. Set up Redis queue (if not using existing)
3. Implement ReviewQueue service
4. Create API endpoints for reviews
5. Build review UI (or CLI for MVP)
6. Human approval workflow
7. End-to-end tests

**Deliverable:** Humans can review and approve pending concept assignments

**Success:** Review queue shows pending matches → human approves → mention linked

---

### Phase 4: Synthesis & Aggregation (Sprint 4)

**Goal:** Canonical statement generation and metadata aggregation

**Tasks:**
1. Implement Synthesis agent (LangGraph)
2. Create synthesis prompts
3. Implement metadata aggregation logic
4. Implement baseline validation
5. Add synthesis trigger (when new mention added)
6. Human override for canonical statements
7. Versioning for concepts

**Deliverable:** Concepts have AI-generated canonical statements updated as mentions accumulate

**Success:** Add 3rd mention to concept → synthesis updates canonical statement

---

### Phase 5: API & Search (Sprint 5)

**Goal:** Complete API and semantic search

**Tasks:**
1. Implement /concepts endpoints
2. Implement /mentions endpoints
3. Implement /reviews endpoints
4. Update /search endpoints
5. Add citation boost to matching
6. Performance optimization (caching, indexing)
7. API documentation

**Deliverable:** Full REST API for concept/mention queries

**Success:** Query "show research on X" returns aggregated concept view in <500ms

---

### Phase 6: Testing & Validation (Sprint 6)

**Goal:** Validate with real data

**Tasks:**
1. Select test papers (KG retrieval domain)
2. Import papers with citation hops
3. Measure accuracy (false positive/negative rates)
4. Tune similarity thresholds
5. Refine agent prompts
6. Performance testing
7. Documentation

**Deliverable:** Validated system with measured accuracy

**Success:** False positive <5%, false negative <1%, queries <500ms

---

## 13. Open Questions

### Design Questions

**Q1: Concept Promotion Threshold**
- When does a single mention become a concept?
- Current: Every mention creates/links to concept
- Alternative: Require N mentions before promotion?
- **Recommendation:** Start with 1-mention-per-concept, evaluate later

**Q2: Conflict Resolution**
- What if Paper A says "F1 baseline is 0.85", Paper B says "F1 baseline is 0.90"?
- Current design: Keep both with provenance
- Should we attempt to reconcile/validate?
- **Recommendation:** Show both, flag conflicts, require human review for "verified" status

**Q3: Relationship Inference**
- Should we infer concept-level relationships from mention-level relationships?
- Example: If 3 mentions of Concept A EXTEND mentions of Concept B, create A-[:EXTENDS]->B?
- **Recommendation:** Yes, with confidence threshold (e.g., >80% of mentions agree)

**Q4: Temporal Dynamics**
- How to handle problems that evolve over time?
- Example: "LLM hallucination" in 2020 vs 2024 - same concept or different?
- **Recommendation:** Single concept, track temporal metadata (first/last mentioned year)

**Q5: Cross-Domain Concepts**
- Can a concept span multiple domains?
- Example: "Data quality" in NLP vs Computer Vision
- **Recommendation:** Allow multiple domains, synthesize domain-agnostic canonical statement

### Implementation Questions

**Q6: LangGraph Setup**
- Is LangGraph infrastructure already set up?
- **Action:** Check existing agents, set up if needed using Terraform/GCP

**Q7: Redis vs Alternative**
- If Redis not available, alternatives?
- **Options:** PostgreSQL queue table, Celery, RabbitMQ
- **Recommendation:** Redis for simplicity, but design is queue-agnostic

**Q8: Embedding Model**
- Continue using OpenAI text-embedding-3-small?
- Consider local models (sentence-transformers)?
- **Recommendation:** Start with OpenAI, evaluate cost/performance later

**Q9: Batch vs Real-Time**
- Should matching happen real-time during import or batch post-processing?
- **Current design:** Real-time during import
- **Trade-off:** Real-time = slower import, batch = delayed linking
- **Recommendation:** Real-time for MVP, add batch mode for bulk imports

**Q10: Concept Versioning**
- How to handle major canonical statement changes?
- Should old versions be preserved?
- **Recommendation:** Increment version number, keep history in audit log

---

## Appendix A: Example Queries

### Find all mentions of a concept
```cypher
MATCH (c:ProblemConcept {id: $concept_id})<-[:INSTANCE_OF]-(m:ProblemMention)
MATCH (m)-[:EXTRACTED_FROM]->(p:Paper)
RETURN m, p
ORDER BY p.year DESC
```

### Find concepts with most mentions
```cypher
MATCH (c:ProblemConcept)
WHERE c.domain = $domain
RETURN c.canonical_statement, c.mention_count, c.paper_count
ORDER BY c.mention_count DESC
LIMIT 10
```

### Find related concepts
```cypher
MATCH (c1:ProblemConcept {id: $concept_id})-[r:EXTENDS|CONTRADICTS|DEPENDS_ON]->(c2:ProblemConcept)
RETURN c2.canonical_statement, type(r), r.confidence
ORDER BY r.confidence DESC
```

### Find papers working on same problem
```cypher
MATCH (c:ProblemConcept {id: $concept_id})<-[:INSTANCE_OF]-(m:ProblemMention)-[:EXTRACTED_FROM]->(p:Paper)
RETURN DISTINCT p.doi, p.title, p.year, COUNT(m) as mention_count
ORDER BY p.year DESC
```

### Find pending reviews by priority
```cypher
MATCH (r:PendingReview)
WHERE r.reviewed_at IS NULL
RETURN r
ORDER BY r.priority ASC, r.created_at ASC
LIMIT 20
```

---

## Appendix B: Migration Strategy (Future Work)

**Note:** Current design uses clean slate approach. If migration becomes needed:

**Option 1: Dual-Mode Operation**
- Keep existing Problem nodes
- Create new Mention/Concept nodes alongside
- Deprecate Problem nodes gradually

**Option 2: Batch Conversion**
- Script to convert Problem → ProblemMention
- Attempt to cluster into concepts using embedding similarity
- Require human review of clusters

**Option 3: Incremental Migration**
- New imports use new architecture
- Existing data stays as Problem nodes
- Migrate on-demand when queried

**Recommendation:** Only pursue if user requests migration of existing data.

---

## Appendix C: Review Queue Trade-Offs

| Aspect | Graph-Native (Neo4j) | Separate Queue (PostgreSQL) | Hybrid (Neo4j + Redis) |
|--------|---------------------|----------------------------|------------------------|
| Queue Operations | ⚠️ Not optimized | ✅ Built-in | ✅ Redis optimized |
| Graph Queries | ✅ Native | ❌ Complex joins | ✅ Native |
| Concurrency | ⚠️ Manual locking | ✅ Row locking | ✅ Redis atomic ops |
| Scalability | ⚠️ Moderate | ✅ High | ✅ High |
| Complexity | ✅ Simple | ⚠️ Dual-DB | ⚠️ Dual-system |
| Notifications | ❌ Manual | ✅ Triggers | ✅ Pub/Sub |
| Historical Queries | ✅ Native | ⚠️ Separate | ✅ Neo4j for history |

**Decision:** Hybrid (Neo4j + Redis)
- Best of both worlds
- Neo4j for historical record and graph queries
- Redis for active queue operations

---

## Summary

This design provides a complete architecture for canonical problem representation with:

1. **Clear separation**: ProblemMentions (paper-specific) vs ProblemConcepts (canonical)
2. **Smart matching**: Embedding similarity + citation analysis + agent consensus
3. **Quality control**: Multi-threshold approach with agent review and human approval
4. **Provenance**: Every mention tracks its source, every concept tracks its synthesis
5. **Reproducibility**: Baselines validated before promotion to "verified"
6. **Scalability**: Hybrid queue system, efficient vector search
7. **Iterative improvement**: Designed for feedback loops and threshold tuning

**Next Steps:**
1. Review this design with stakeholders
2. Create ADR for key decisions (Concept/Mention split, Hybrid queue)
3. Set up Sprint 1 task breakdown
4. Validate LangGraph infrastructure
5. Begin implementation with Phase 1

