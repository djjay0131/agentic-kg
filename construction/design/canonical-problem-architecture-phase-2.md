# Canonical Problem Architecture - Phase 2: Agent Workflows

**Created:** 2026-02-10
**Status:** Complete
**Parent Design:** [canonical-problem-architecture.md](./canonical-problem-architecture.md)
**Prerequisite:** Sprint 09 Phase 1 (Complete)
**Related ADRs:** ADR-003 (Problems as First-Class Entities), ADR-005 (Hybrid Retrieval)

---

## Executive Summary

Phase 2 implements agent-based review workflows for MEDIUM and LOW confidence matches that Phase 1's auto-linker cannot handle. This includes:

- **Evaluator Agent** for MEDIUM confidence (80-95%): Single LLM review
- **Maker/Hater/Arbiter Consensus** for LOW confidence (50-80%): Multi-agent debate
- **Human Review Queue** for disputed matches: Database-backed queue with API
- **Concept Refinement** at thresholds: Synthesize canonical statements at 5/10/25/50 mentions

**Key Principle:** Near-zero false negatives. Missing a duplicate is worse than a false link.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution Approach](#2-solution-approach)
3. [Agent Specifications](#3-agent-specifications)
4. [LangGraph Workflow](#4-langgraph-workflow)
5. [Human Review Queue](#5-human-review-queue)
6. [Concept Refinement](#6-concept-refinement)
7. [Data Models](#7-data-models)
8. [Integration Points](#8-integration-points)
9. [Verification Strategy](#9-verification-strategy)
10. [Implementation Tasks](#10-implementation-tasks)

---

## 1. Problem Statement

### Current State (After Phase 1)

Phase 1 successfully implemented:
- `ConceptMatcher`: Embedding similarity with confidence classification
- `AutoLinker`: Automatic linking for HIGH confidence (>95%)
- New concept creation when no HIGH match exists

**Gap:** MEDIUM (80-95%) and LOW (50-80%) confidence matches are not processed. Currently:
- MEDIUM matches return `None` from AutoLinker (no action taken)
- LOW matches also return `None`
- These mentions remain unlinked, creating potential duplicate concepts

### Desired State

A complete matching pipeline where:
1. HIGH confidence (>95%): Auto-link (Phase 1 - done)
2. MEDIUM confidence (80-95%): EvaluatorAgent reviews and decides
3. LOW confidence (50-80%): Maker/Hater/Arbiter consensus decides
4. Disputed/Failed: Human review queue
5. Concepts refined as mentions accumulate

### How We'll Know It Works

**Success Criteria:**
1. MEDIUM confidence matches processed in <5 seconds per mention
2. LOW confidence matches processed in <30 seconds (3 rounds max)
3. EvaluatorAgent achieves >90% agreement with human judgment on golden dataset
4. Consensus workflow achieves >85% agreement with human judgment
5. Human review queue maintains <7 day SLA for high priority items
6. Concept refinement triggers at 5/10/25/50 mention thresholds
7. False negative rate remains 0% (no missed duplicates)
8. False positive rate <5% (acceptable incorrect links)

---

## 2. Solution Approach

### Architecture Overview

```
                    ConceptMatcher
                         |
            +------------+------------+
            |            |            |
         HIGH(>95%)  MEDIUM(80-95) LOW(50-80)
            |            |            |
       AutoLinker   EvaluatorAgent  Maker/Hater
       (Phase 1)         |            |
            |       +----+----+    Arbiter
            |       |    |    |       |
            |    APPROVE REJ ESC   +--+--+
            |       |    |    |    |     |
            v       v    v    +----+  LINK/NEW
         LINKED  LINKED NEW        |
                              +----+----+
                              |         |
                           LINKED    HUMAN
                                    REVIEW
                                       |
                              +--------+--------+
                              |        |        |
                           APPROVE  REJECT  BLACKLIST
```

### Key Design Decisions

**Decision 1: EvaluatorAgent for MEDIUM Confidence**
- Single LLM call with structured JSON output
- Three possible decisions: APPROVE, REJECT, ESCALATE
- ESCALATE sends to Maker/Hater consensus (not directly to human)
- Target: <5 seconds per decision

**Decision 2: Best-of-3 Consensus Protocol**
- Maker argues FOR the match
- Hater argues AGAINST the match
- Arbiter weighs both and decides
- If Arbiter is low-confidence, retry (up to 3 rounds)
- After 3 rounds without consensus: escalate to human

**Decision 3: Simple Database Queue**
- Store pending reviews in Neo4j (PendingReview nodes)
- No external ticketing system
- API endpoints for queue management
- Priority-based ordering with SLA tracking

**Decision 4: Threshold-Based Refinement**
- Refine canonical_statement at mention_count = 5, 10, 25, 50
- LLM synthesizes best statement from all linked mentions
- Track `synthesis_method`: "first_mention" vs "synthesized"
- Human-edited statements never auto-refined

---

## 3. Agent Specifications

### 3.1 EvaluatorAgent

**Purpose:** Quick single-agent review for MEDIUM confidence matches (80-95%)

**Input:**
- `ProblemMention` with statement, domain, paper context
- `MatchCandidate` with concept_id, similarity_score, concept_statement

**Output:**
```python
class EvaluatorResult(BaseModel):
    decision: Literal["approve", "reject", "escalate"]
    confidence: float  # 0.0-1.0
    reasoning: str  # Explanation for audit trail
    key_factors: list[str]  # What influenced decision
```

**Prompt:**
```
You are a research problem matching expert. Your task is to decide whether a
problem mention from a paper should be linked to an existing canonical concept.

## Problem Mention (from paper)
Statement: "{mention.statement}"
Domain: {mention.domain}
Paper: {mention.paper_doi}
Section: {mention.section}

## Candidate Concept
Canonical Statement: "{concept.canonical_statement}"
Domain: {concept.domain}
Current Mentions: {concept.mention_count} from {concept.paper_count} papers
Similarity Score: {candidate.similarity_score:.2%}

## Your Task
Decide whether these represent the SAME underlying research problem:

- APPROVE: They are the same problem (different wording, same meaning)
- REJECT: They are different problems (similar wording, different scope/meaning)
- ESCALATE: Genuinely uncertain, need more analysis

Consider:
1. Semantic equivalence (not just keyword overlap)
2. Problem scope (broad vs narrow framing)
3. Domain context (same research area?)
4. Assumptions and constraints alignment

Return JSON:
{
    "decision": "approve" | "reject" | "escalate",
    "confidence": 0.0-1.0,
    "reasoning": "2-3 sentence explanation",
    "key_factors": ["factor1", "factor2", ...]
}

IMPORTANT: Err on the side of APPROVE. Missing a duplicate is worse than linking
related-but-distinct problems. Only REJECT if clearly different problems.
```

**Performance Requirements:**
- Response time: <5 seconds
- Token limit: ~500 output tokens
- Model: gpt-4o or claude-3-5-sonnet

---

### 3.2 MakerAgent

**Purpose:** Argue FOR linking the mention to the candidate concept

**Input:** Same as EvaluatorAgent

**Output:**
```python
class MakerResult(BaseModel):
    arguments: list[str]  # 3-5 arguments supporting the match
    evidence: list[str]  # Specific evidence from mention/concept
    confidence: float  # How strong is the case for linking
    strongest_argument: str  # Most compelling reason
```

**Prompt:**
```
You are the MAKER agent in a research problem matching debate. Your role is to
argue FOR linking this mention to the candidate concept.

## Problem Mention
{mention_details}

## Candidate Concept
{concept_details}

## Your Task
Build the strongest possible case for why these should be linked:

1. Semantic Similarity: How are the problem statements semantically equivalent?
2. Scope Alignment: Do they address the same scope of research challenge?
3. Domain Evidence: Are they from the same research domain/community?
4. Contextual Clues: Citations, methodology overlap, metric similarity?
5. Conservative Linking: Remember, missing duplicates is worse than over-linking.

Return JSON:
{
    "arguments": ["arg1", "arg2", "arg3", ...],
    "evidence": ["evidence1", "evidence2", ...],
    "confidence": 0.0-1.0,
    "strongest_argument": "The most compelling reason to link"
}

Be persuasive but honest. Acknowledge weak points if they exist.
```

---

### 3.3 HaterAgent

**Purpose:** Argue AGAINST linking the mention to the candidate concept

**Input:** Same as EvaluatorAgent

**Output:**
```python
class HaterResult(BaseModel):
    arguments: list[str]  # 3-5 arguments against the match
    evidence: list[str]  # Specific evidence showing differences
    confidence: float  # How strong is the case against linking
    strongest_argument: str  # Most compelling reason NOT to link
```

**Prompt:**
```
You are the HATER agent in a research problem matching debate. Your role is to
argue AGAINST linking this mention to the candidate concept.

## Problem Mention
{mention_details}

## Candidate Concept
{concept_details}

## Your Task
Build the strongest possible case for why these should NOT be linked:

1. Semantic Differences: How do the problem statements differ in meaning?
2. Scope Mismatch: Is one broader/narrower than the other?
3. Domain Divergence: Different research communities or contexts?
4. Conflating Risk: Would linking these conflate genuinely distinct problems?
5. Methodological Differences: Different approaches suggesting different problems?

Return JSON:
{
    "arguments": ["arg1", "arg2", "arg3", ...],
    "evidence": ["evidence1", "evidence2", ...],
    "confidence": 0.0-1.0,
    "strongest_argument": "The most compelling reason NOT to link"
}

Be critical but fair. If the match seems strong, acknowledge it honestly.
```

---

### 3.4 ArbiterAgent

**Purpose:** Weigh Maker and Hater arguments, make final decision

**Input:**
- Original mention and candidate
- MakerResult arguments
- HaterResult arguments
- Round number (1, 2, or 3)

**Output:**
```python
class ArbiterResult(BaseModel):
    decision: Literal["link", "create_new", "retry"]
    confidence: float  # Must be >0.7 to finalize, else retry
    reasoning: str  # How arguments were weighed
    maker_weight: float  # How much Maker convinced (0-1)
    hater_weight: float  # How much Hater convinced (0-1)
    decisive_factor: str  # What tipped the scale
```

**Prompt:**
```
You are the ARBITER agent. You've heard arguments from MAKER (pro-link) and
HATER (anti-link). Make a final decision.

## Problem Mention
{mention_details}

## Candidate Concept
{concept_details}

## MAKER Arguments (for linking):
{maker_arguments}

## HATER Arguments (against linking):
{hater_arguments}

## Your Task
Weigh both sides and decide:
- LINK: The problems are the same, link them
- CREATE_NEW: The problems are different, create new concept
- RETRY: Cannot decide with confidence, need another round

Decision Framework:
1. Which arguments are most compelling and evidence-based?
2. Consider false positive vs false negative risk:
   - False positive (wrong link): ~5% acceptable, can be corrected
   - False negative (missed duplicate): MUST be near 0%, creates fragmentation
3. When in doubt, favor LINK (we can correct mistakes later)

Return JSON:
{
    "decision": "link" | "create_new" | "retry",
    "confidence": 0.0-1.0,
    "reasoning": "Explanation of decision",
    "maker_weight": 0.0-1.0,
    "hater_weight": 0.0-1.0,
    "decisive_factor": "What tipped the scale"
}

Rules:
- If confidence < 0.7, you MUST return "retry" (round {round}/3)
- After round 3, the decision goes to human review regardless
```

---

## 4. LangGraph Workflow

### 4.1 Workflow State

```python
class MatchingWorkflowState(TypedDict):
    """State for the concept matching workflow."""

    # Input
    mention: dict  # ProblemMention serialized
    candidates: list[dict]  # List of MatchCandidate
    confidence: str  # MatchConfidence value from ConceptMatcher
    trace_id: str  # For audit trail

    # Workflow tracking
    current_step: str
    round_count: int  # For consensus retries (max 3)

    # Agent outputs
    evaluator_result: Optional[dict]  # EvaluatorResult
    maker_result: Optional[dict]  # MakerResult
    hater_result: Optional[dict]  # HaterResult
    arbiter_result: Optional[dict]  # ArbiterResult

    # Final outcome
    decision: Optional[str]  # "link" | "create_new" | "human_review"
    linked_concept_id: Optional[str]
    new_concept_id: Optional[str]

    # Audit
    messages: list[dict]
    errors: list[str]
```

### 4.2 Workflow Graph

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_matching_workflow(
    evaluator: EvaluatorAgent,
    maker: MakerAgent,
    hater: HaterAgent,
    arbiter: ArbiterAgent,
    linker: AutoLinker,
    checkpointer: Any = None,
) -> StateGraph:
    """Build the concept matching workflow."""

    workflow = StateGraph(MatchingWorkflowState)

    # --- Node functions ---
    async def evaluator_node(state):
        result = await evaluator.run(state)
        return {"evaluator_result": result, "current_step": "evaluator"}

    async def maker_node(state):
        result = await maker.run(state)
        return {"maker_result": result, "current_step": "maker"}

    async def hater_node(state):
        result = await hater.run(state)
        return {"hater_result": result, "current_step": "hater"}

    async def arbiter_node(state):
        result = await arbiter.run(state)
        new_round = state.get("round_count", 0) + 1
        return {"arbiter_result": result, "round_count": new_round, "current_step": "arbiter"}

    async def link_node(state):
        mention = ProblemMention(**state["mention"])
        concept_id = state["candidates"][0]["concept_id"]
        await linker.link_mention_to_concept(mention, concept_id, state["trace_id"])
        return {"decision": "link", "linked_concept_id": concept_id}

    async def create_new_node(state):
        mention = ProblemMention(**state["mention"])
        concept = await linker.create_new_concept(mention, state["trace_id"])
        return {"decision": "create_new", "new_concept_id": concept.id}

    async def queue_human_node(state):
        mention = ProblemMention(**state["mention"])
        await review_queue.enqueue(mention, state["candidates"], state)
        return {"decision": "human_review"}

    # --- Routing functions ---
    def route_by_confidence(state) -> str:
        conf = state["confidence"]
        if conf == "medium":
            return "evaluator"
        elif conf == "low":
            return "maker"
        return "end"  # HIGH handled by Phase 1

    def route_evaluator_decision(state) -> str:
        result = state.get("evaluator_result", {})
        decision = result.get("decision", "escalate")
        if decision == "approve":
            return "link"
        elif decision == "reject":
            return "create_new"
        return "maker"  # escalate to consensus

    def route_arbiter_decision(state) -> str:
        result = state.get("arbiter_result", {})
        decision = result.get("decision", "retry")
        confidence = result.get("confidence", 0)

        if decision == "link" and confidence >= 0.7:
            return "link"
        elif decision == "create_new" and confidence >= 0.7:
            return "create_new"
        elif state.get("round_count", 0) >= 3:
            return "human_review"
        return "maker"  # retry consensus

    # --- Build graph ---
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("maker", maker_node)
    workflow.add_node("hater", hater_node)
    workflow.add_node("arbiter", arbiter_node)
    workflow.add_node("link", link_node)
    workflow.add_node("create_new", create_new_node)
    workflow.add_node("human_review", queue_human_node)

    # Entry point routes by confidence
    workflow.set_entry_point("route_entry")
    workflow.add_node("route_entry", lambda s: s)  # passthrough
    workflow.add_conditional_edges("route_entry", route_by_confidence, {
        "evaluator": "evaluator",
        "maker": "maker",
        "end": END
    })

    # Evaluator routes to link/create_new/consensus
    workflow.add_conditional_edges("evaluator", route_evaluator_decision, {
        "link": "link",
        "create_new": "create_new",
        "maker": "maker"
    })

    # Maker -> Hater -> Arbiter chain
    workflow.add_edge("maker", "hater")
    workflow.add_edge("hater", "arbiter")

    # Arbiter routes to link/create_new/retry/human
    workflow.add_conditional_edges("arbiter", route_arbiter_decision, {
        "link": "link",
        "create_new": "create_new",
        "maker": "maker",  # retry
        "human_review": "human_review"
    })

    # Terminal nodes
    workflow.add_edge("link", END)
    workflow.add_edge("create_new", END)
    workflow.add_edge("human_review", END)

    # Compile with checkpointing
    return workflow.compile(checkpointer=checkpointer or MemorySaver())
```

### 4.3 Workflow Invocation

```python
async def process_medium_low_confidence(
    mention: ProblemMention,
    candidates: list[MatchCandidate],
    confidence: MatchConfidence,
    trace_id: str,
) -> MatchingWorkflowState:
    """Process MEDIUM or LOW confidence matches through agent workflow."""

    initial_state = MatchingWorkflowState(
        mention=mention.model_dump(),
        candidates=[c.model_dump() for c in candidates],
        confidence=confidence.value,
        trace_id=trace_id,
        current_step="start",
        round_count=0,
        evaluator_result=None,
        maker_result=None,
        hater_result=None,
        arbiter_result=None,
        decision=None,
        linked_concept_id=None,
        new_concept_id=None,
        messages=[],
        errors=[],
    )

    workflow = get_matching_workflow()  # Singleton or factory

    result = await workflow.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": trace_id}}
    )

    return result
```

---

## 5. Human Review Queue

### 5.1 Data Model

```python
class PendingReview(BaseModel):
    """A mention awaiting human review."""

    # Identity
    id: str  # UUID
    trace_id: str  # Links to workflow state

    # The mention needing review
    mention_id: str
    mention_statement: str
    paper_doi: str
    domain: Optional[str]

    # Suggested concepts (ranked by score)
    suggested_concepts: list[dict]  # [{concept_id, concept_statement, score, reasoning}]

    # Agent context (why it's in queue)
    escalation_reason: str  # "evaluator_uncertain" | "consensus_failed" | "arbiter_low_confidence"
    agent_results: dict  # Evaluator/Maker/Hater/Arbiter outputs
    rounds_attempted: int

    # Queue management
    priority: int  # 1=highest, 10=lowest
    status: str  # "pending" | "assigned" | "completed"
    assigned_to: Optional[str]  # User ID
    assigned_at: Optional[datetime]

    # SLA tracking
    created_at: datetime
    sla_deadline: datetime

    # Resolution
    resolution: Optional[str]  # "linked" | "created_new" | "blacklisted"
    resolved_concept_id: Optional[str]
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]
```

### 5.2 Neo4j Schema

```cypher
// PendingReview node
CREATE CONSTRAINT pending_review_id_unique IF NOT EXISTS
FOR (r:PendingReview) REQUIRE r.id IS UNIQUE;

CREATE INDEX pending_review_status_idx IF NOT EXISTS
FOR (r:PendingReview) ON (r.status);

CREATE INDEX pending_review_priority_idx IF NOT EXISTS
FOR (r:PendingReview) ON (r.priority);

CREATE INDEX pending_review_sla_idx IF NOT EXISTS
FOR (r:PendingReview) ON (r.sla_deadline);

// Relationship to mention
// (r:PendingReview)-[:REVIEWS]->(m:ProblemMention)
```

### 5.3 Review Queue Service

```python
class ReviewQueueService:
    """Manages the human review queue."""

    def __init__(self, repository: Neo4jRepository):
        self._repo = repository

    async def enqueue(
        self,
        mention: ProblemMention,
        candidates: list[MatchCandidate],
        workflow_state: MatchingWorkflowState,
        priority: Optional[int] = None,
    ) -> PendingReview:
        """Add mention to review queue."""

        # Calculate priority if not provided
        if priority is None:
            priority = self._calculate_priority(mention, candidates)

        # Calculate SLA deadline
        sla_hours = self._get_sla_hours(priority)
        sla_deadline = datetime.now(timezone.utc) + timedelta(hours=sla_hours)

        review = PendingReview(
            id=str(uuid.uuid4()),
            trace_id=workflow_state["trace_id"],
            mention_id=mention.id,
            mention_statement=mention.statement,
            paper_doi=mention.paper_doi,
            domain=mention.domain,
            suggested_concepts=[
                {
                    "concept_id": c.concept_id,
                    "concept_statement": c.concept_statement,
                    "score": c.similarity_score,
                    "reasoning": c.reasoning
                }
                for c in candidates[:5]  # Top 5
            ],
            escalation_reason=self._determine_reason(workflow_state),
            agent_results={
                "evaluator": workflow_state.get("evaluator_result"),
                "maker": workflow_state.get("maker_result"),
                "hater": workflow_state.get("hater_result"),
                "arbiter": workflow_state.get("arbiter_result"),
            },
            rounds_attempted=workflow_state.get("round_count", 0),
            priority=priority,
            status="pending",
            created_at=datetime.now(timezone.utc),
            sla_deadline=sla_deadline,
        )

        await self._store_review(review)
        return review

    async def get_pending(
        self,
        limit: int = 20,
        priority_filter: Optional[int] = None,
        domain_filter: Optional[str] = None,
    ) -> list[PendingReview]:
        """Get pending reviews sorted by priority and SLA."""

        query = """
        MATCH (r:PendingReview {status: 'pending'})
        WHERE ($priority IS NULL OR r.priority <= $priority)
          AND ($domain IS NULL OR r.domain = $domain)
        RETURN r
        ORDER BY r.priority ASC, r.sla_deadline ASC
        LIMIT $limit
        """

        return await self._repo.query(query, {
            "priority": priority_filter,
            "domain": domain_filter,
            "limit": limit
        })

    async def resolve(
        self,
        review_id: str,
        resolution: str,  # "linked" | "created_new" | "blacklisted"
        concept_id: Optional[str],
        user_id: str,
        notes: Optional[str] = None,
    ) -> PendingReview:
        """Resolve a pending review."""

        review = await self._get_review(review_id)

        review.resolution = resolution
        review.resolved_concept_id = concept_id
        review.resolved_by = user_id
        review.resolved_at = datetime.now(timezone.utc)
        review.resolution_notes = notes
        review.status = "completed"

        # Apply the decision
        if resolution == "linked":
            await self._link_mention(review.mention_id, concept_id, user_id)
        elif resolution == "created_new":
            await self._create_new_concept(review.mention_id, user_id)
        elif resolution == "blacklisted":
            await self._blacklist_pair(review.mention_id, concept_id, notes)

        await self._update_review(review)
        return review

    def _calculate_priority(
        self,
        mention: ProblemMention,
        candidates: list[MatchCandidate]
    ) -> int:
        """Calculate priority (1=highest, 10=lowest)."""
        base = 5

        # Lower confidence = higher priority (needs review sooner)
        top_score = candidates[0].similarity_score if candidates else 0
        confidence_factor = int((1 - top_score) * 5)

        # High-impact domains get priority
        domain_factor = -1 if mention.domain in ["NLP", "CV", "ML"] else 0

        priority = base + confidence_factor + domain_factor
        return max(1, min(10, priority))

    def _get_sla_hours(self, priority: int) -> int:
        """Get SLA hours based on priority."""
        if priority <= 3:
            return 24  # High priority: 24 hours
        elif priority <= 6:
            return 168  # Medium: 7 days
        else:
            return 720  # Low: 30 days
```

### 5.4 API Endpoints

```python
# In packages/api/src/agentic_kg_api/routers/reviews.py

@router.get("/reviews/pending")
async def list_pending_reviews(
    limit: int = 20,
    priority: Optional[int] = None,
    domain: Optional[str] = None,
    queue_service: ReviewQueueService = Depends(get_queue_service),
) -> list[PendingReviewResponse]:
    """Get pending reviews sorted by priority."""
    return await queue_service.get_pending(limit, priority, domain)

@router.get("/reviews/{review_id}")
async def get_review(
    review_id: str,
    queue_service: ReviewQueueService = Depends(get_queue_service),
) -> PendingReviewDetailResponse:
    """Get review details including agent debate context."""
    return await queue_service.get_detail(review_id)

@router.post("/reviews/{review_id}/resolve")
async def resolve_review(
    review_id: str,
    resolution: ReviewResolutionRequest,
    current_user: User = Depends(get_current_user),
    queue_service: ReviewQueueService = Depends(get_queue_service),
) -> PendingReviewResponse:
    """Resolve a pending review."""
    return await queue_service.resolve(
        review_id=review_id,
        resolution=resolution.decision,
        concept_id=resolution.concept_id,
        user_id=current_user.id,
        notes=resolution.notes,
    )

@router.post("/reviews/{review_id}/assign")
async def assign_review(
    review_id: str,
    current_user: User = Depends(get_current_user),
    queue_service: ReviewQueueService = Depends(get_queue_service),
) -> PendingReviewResponse:
    """Assign review to current user."""
    return await queue_service.assign(review_id, current_user.id)
```

---

## 6. Concept Refinement

### 6.1 Refinement Triggers

Refine canonical_statement when `mention_count` reaches thresholds:
- **5 mentions**: First refinement (enough context)
- **10 mentions**: Second refinement
- **25 mentions**: Third refinement
- **50 mentions**: Final refinement

### 6.2 Refinement Service

```python
class ConceptRefinementService:
    """Refines canonical statements as mentions accumulate."""

    REFINEMENT_THRESHOLDS = [5, 10, 25, 50]

    def __init__(
        self,
        repository: Neo4jRepository,
        llm_client: BaseLLMClient,
    ):
        self._repo = repository
        self._llm = llm_client

    async def check_and_refine(
        self,
        concept_id: str,
        trace_id: str,
    ) -> Optional[ProblemConcept]:
        """Check if concept needs refinement, refine if so."""

        concept = await self._repo.get_concept(concept_id)

        # Skip if human-edited
        if concept.human_edited:
            return None

        # Check if at threshold
        if concept.mention_count not in self.REFINEMENT_THRESHOLDS:
            return None

        # Check if already refined at this threshold
        last_refined_at = concept.metadata.get("last_refined_at_count", 0)
        if last_refined_at >= concept.mention_count:
            return None

        # Get all mentions for synthesis
        mentions = await self._repo.get_mentions_for_concept(concept_id)

        # Synthesize new canonical statement
        new_statement = await self._synthesize(concept, mentions)

        # Update concept
        concept.canonical_statement = new_statement
        concept.synthesis_method = "synthesized"
        concept.synthesized_at = datetime.now(timezone.utc)
        concept.synthesized_by = "refinement_agent"
        concept.version += 1
        concept.metadata["last_refined_at_count"] = concept.mention_count

        await self._repo.update_concept(concept)

        logger.info(
            f"[{trace_id}] Refined concept {concept_id} at {concept.mention_count} mentions"
        )

        return concept

    async def _synthesize(
        self,
        concept: ProblemConcept,
        mentions: list[ProblemMention],
    ) -> str:
        """Synthesize canonical statement from all mentions."""

        mentions_text = "\n".join([
            f"- Paper {m.paper_doi}: \"{m.statement}\""
            for m in mentions
        ])

        prompt = f"""You are synthesizing a canonical problem statement from multiple paper mentions.

Current Canonical Statement:
"{concept.canonical_statement}"

All {len(mentions)} Mentions:
{mentions_text}

Create a refined canonical statement that:
1. Captures the essence of ALL mentions
2. Is clear and concise (1-2 sentences)
3. Is general enough to encompass all framings
4. Is specific enough to distinguish from related problems
5. Avoids paper-specific details

Return only the refined statement, no explanation.
"""

        response = await self._llm.complete(
            system_prompt="You are a research problem synthesis expert.",
            user_prompt=prompt,
        )

        return response.content.strip()
```

### 6.3 Integration with Linking

```python
# In AutoLinker or KGIntegratorV2

async def link_and_maybe_refine(
    self,
    mention: ProblemMention,
    concept_id: str,
    trace_id: str,
) -> ProblemConcept:
    """Link mention to concept and trigger refinement if needed."""

    # Create link (updates mention_count)
    concept = await self._create_instance_of_relationship(mention, concept_id, trace_id)

    # Check for refinement
    await self._refinement_service.check_and_refine(concept_id, trace_id)

    return concept
```

---

## 7. Data Models

### 7.1 New Models

```python
# In packages/core/src/agentic_kg/agents/matching/

class EvaluatorResult(BaseModel):
    """Result from EvaluatorAgent."""
    decision: Literal["approve", "reject", "escalate"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    key_factors: list[str] = []

class MakerResult(BaseModel):
    """Result from MakerAgent."""
    arguments: list[str]
    evidence: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    strongest_argument: str

class HaterResult(BaseModel):
    """Result from HaterAgent."""
    arguments: list[str]
    evidence: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    strongest_argument: str

class ArbiterResult(BaseModel):
    """Result from ArbiterAgent."""
    decision: Literal["link", "create_new", "retry"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    maker_weight: float = Field(ge=0.0, le=1.0)
    hater_weight: float = Field(ge=0.0, le=1.0)
    decisive_factor: str
```

### 7.2 Updated Enums

```python
# Add to models/enums.py

class EscalationReason(str, Enum):
    """Reason a review was escalated to human queue."""
    EVALUATOR_UNCERTAIN = "evaluator_uncertain"
    CONSENSUS_FAILED = "consensus_failed"
    ARBITER_LOW_CONFIDENCE = "arbiter_low_confidence"
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"

class ReviewResolution(str, Enum):
    """How a human review was resolved."""
    LINKED = "linked"
    CREATED_NEW = "created_new"
    BLACKLISTED = "blacklisted"
```

---

## 8. Integration Points

### 8.1 Integration with Phase 1

Phase 1's `KGIntegratorV2.integrate_extracted_problems()` currently:
1. Creates ProblemMention
2. Calls `AutoLinker.auto_link_high_confidence()`
3. If no HIGH match, calls `AutoLinker.create_new_concept()`

**Change for Phase 2:**
```python
async def integrate_extracted_problems(self, ...):
    for problem in extracted_problems:
        mention = self._create_problem_mention(problem, ...)
        await self._store_mention_node(mention, ...)

        # Find best match
        candidate = await self._matcher.match_mention_to_concept(mention)

        if not candidate:
            # No match at all - create new concept
            await self._auto_linker.create_new_concept(mention, trace_id)
        elif candidate.confidence == MatchConfidence.HIGH:
            # Phase 1: Auto-link
            await self._auto_linker.auto_link_high_confidence(mention, trace_id)
        else:
            # Phase 2: Agent workflow for MEDIUM/LOW
            await self._process_agent_workflow(mention, candidate, trace_id)

async def _process_agent_workflow(
    self,
    mention: ProblemMention,
    candidate: MatchCandidate,
    trace_id: str,
):
    """Route MEDIUM/LOW confidence to agent workflow."""
    from agentic_kg.agents.matching import process_medium_low_confidence

    result = await process_medium_low_confidence(
        mention=mention,
        candidates=[candidate],
        confidence=candidate.confidence,
        trace_id=trace_id,
    )

    # Result handling done by workflow (link/create_new/queue)
    return result
```

### 8.2 File Structure

```
packages/core/src/agentic_kg/
├── agents/
│   ├── matching/                    # NEW: Phase 2 agents
│   │   ├── __init__.py
│   │   ├── evaluator.py            # EvaluatorAgent
│   │   ├── maker.py                # MakerAgent
│   │   ├── hater.py                # HaterAgent
│   │   ├── arbiter.py              # ArbiterAgent
│   │   ├── workflow.py             # LangGraph workflow
│   │   ├── state.py                # MatchingWorkflowState
│   │   └── schemas.py              # Result models
│   └── ...
├── knowledge_graph/
│   ├── review_queue.py             # NEW: ReviewQueueService
│   ├── concept_refinement.py       # NEW: ConceptRefinementService
│   └── ...
└── ...

packages/api/src/agentic_kg_api/
├── routers/
│   └── reviews.py                  # NEW: Review queue endpoints
└── ...
```

---

## 9. Verification Strategy

### 9.1 Unit Tests

```python
# tests/agents/matching/test_evaluator.py
class TestEvaluatorAgent:
    async def test_approve_high_similarity(self):
        """Test that high similarity mention approves."""

    async def test_reject_different_scope(self):
        """Test that different scope rejects."""

    async def test_escalate_uncertain(self):
        """Test that uncertain cases escalate."""

# tests/agents/matching/test_consensus.py
class TestMakerHaterArbiter:
    async def test_maker_generates_arguments(self):
        """Test Maker produces valid arguments."""

    async def test_hater_generates_counter_arguments(self):
        """Test Hater produces valid counter-arguments."""

    async def test_arbiter_decides_with_confidence(self):
        """Test Arbiter makes confident decision."""

    async def test_arbiter_retries_when_uncertain(self):
        """Test Arbiter requests retry when confidence < 0.7."""

# tests/agents/matching/test_workflow.py
class TestMatchingWorkflow:
    async def test_medium_confidence_uses_evaluator(self):
        """Test MEDIUM routes to Evaluator."""

    async def test_low_confidence_uses_consensus(self):
        """Test LOW routes to Maker/Hater/Arbiter."""

    async def test_max_retries_escalates_to_human(self):
        """Test 3 failed rounds escalates to human queue."""
```

### 9.2 Integration Tests

```python
# tests/integration/test_phase2_workflow.py
class TestPhase2Integration:
    async def test_medium_confidence_end_to_end(self):
        """Import paper with 85% match -> Evaluator approves -> linked."""

    async def test_low_confidence_consensus(self):
        """Import paper with 65% match -> consensus decides."""

    async def test_human_queue_creation(self):
        """Disputed match -> appears in review queue."""

    async def test_concept_refinement_at_threshold(self):
        """5th mention triggers canonical refinement."""
```

### 9.3 Golden Dataset Validation

**Target:** >90% agreement with human judgment

```python
# tests/acceptance/test_golden_dataset.py
class TestGoldenDataset:
    async def test_evaluator_accuracy(self):
        """Run Evaluator on golden dataset, measure accuracy."""
        results = []
        for case in GOLDEN_DATASET_MEDIUM:
            result = await evaluator.run(case)
            results.append(result.decision == case.expected)

        accuracy = sum(results) / len(results)
        assert accuracy >= 0.90, f"Evaluator accuracy {accuracy:.2%} < 90%"

    async def test_consensus_accuracy(self):
        """Run full consensus on golden dataset."""
        results = []
        for case in GOLDEN_DATASET_LOW:
            result = await run_consensus_workflow(case)
            results.append(result.decision == case.expected)

        accuracy = sum(results) / len(results)
        assert accuracy >= 0.85, f"Consensus accuracy {accuracy:.2%} < 85%"
```

### 9.4 Performance Tests

```python
class TestPerformance:
    async def test_evaluator_speed(self):
        """Evaluator completes in <5 seconds."""
        start = time.time()
        await evaluator.run(sample_state)
        elapsed = time.time() - start
        assert elapsed < 5.0

    async def test_consensus_speed(self):
        """Full consensus (1 round) completes in <15 seconds."""
        start = time.time()
        await run_single_consensus_round(sample_state)
        elapsed = time.time() - start
        assert elapsed < 15.0

    async def test_review_queue_query(self):
        """Queue query completes in <100ms."""
        start = time.time()
        await queue_service.get_pending(limit=20)
        elapsed = (time.time() - start) * 1000
        assert elapsed < 100
```

---

## 10. Implementation Tasks

### Task 1: Agent Models and Schemas
**Effort:** 2 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/agents/matching/schemas.py`
  - EvaluatorResult, MakerResult, HaterResult, ArbiterResult
  - MatchingWorkflowState TypedDict
- [ ] Add EscalationReason, ReviewResolution enums to models/enums.py
- [ ] Create PendingReview model in models/entities.py
- [ ] Unit tests for model validation

**Acceptance Criteria:**
- All models serialize/deserialize correctly
- Validators catch invalid data
- 100% type coverage

---

### Task 2: EvaluatorAgent Implementation
**Effort:** 3 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/agents/matching/evaluator.py`
- [ ] Implement EvaluatorAgent extending BaseAgent
- [ ] Create structured prompt with JSON output
- [ ] Handle LLM errors gracefully
- [ ] Unit tests with mocked LLM

**Acceptance Criteria:**
- Returns EvaluatorResult in <5 seconds
- Handles edge cases (empty mentions, missing fields)
- JSON parsing errors caught and logged

---

### Task 3: Maker/Hater/Arbiter Agents
**Effort:** 4 hours
**Status:** Not Started

- [ ] Create `maker.py`, `hater.py`, `arbiter.py`
- [ ] Implement MakerAgent with pro-arguments
- [ ] Implement HaterAgent with contra-arguments
- [ ] Implement ArbiterAgent with decision logic
- [ ] Ensure Arbiter respects confidence threshold (0.7)
- [ ] Unit tests for each agent

**Acceptance Criteria:**
- Maker/Hater produce 3-5 relevant arguments
- Arbiter returns "retry" when confidence < 0.7
- All agents handle errors gracefully

---

### Task 4: LangGraph Workflow
**Effort:** 4 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/agents/matching/workflow.py`
- [ ] Implement build_matching_workflow() function
- [ ] Add routing logic for confidence levels
- [ ] Add retry logic (max 3 rounds)
- [ ] Add checkpointing with MemorySaver
- [ ] Integration tests for workflow paths

**Acceptance Criteria:**
- MEDIUM routes to Evaluator
- LOW routes to Maker/Hater/Arbiter
- Retry works up to 3 rounds
- 3 failed rounds escalate to human queue
- Checkpoints enable resume after failure

---

### Task 5: Human Review Queue Service
**Effort:** 4 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/knowledge_graph/review_queue.py`
- [ ] Implement ReviewQueueService with Neo4j storage
- [ ] Add priority calculation algorithm
- [ ] Add SLA deadline calculation
- [ ] Implement enqueue/dequeue/resolve operations
- [ ] Unit tests with mocked repository

**Acceptance Criteria:**
- Reviews stored and queryable
- Priority ordering works correctly
- SLA deadlines calculated per priority tier
- Resolution updates mention state

---

### Task 6: Review Queue API Endpoints
**Effort:** 3 hours
**Status:** Not Started

- [ ] Create `packages/api/src/agentic_kg_api/routers/reviews.py`
- [ ] Implement GET /reviews/pending
- [ ] Implement GET /reviews/{id}
- [ ] Implement POST /reviews/{id}/resolve
- [ ] Implement POST /reviews/{id}/assign
- [ ] Add authentication/authorization
- [ ] API tests

**Acceptance Criteria:**
- All endpoints return correct data
- Authorization required for resolve/assign
- Validation errors return 400
- Audit logging for all mutations

---

### Task 7: Concept Refinement Service
**Effort:** 3 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/knowledge_graph/concept_refinement.py`
- [ ] Implement ConceptRefinementService
- [ ] Add threshold checking (5/10/25/50)
- [ ] Implement synthesis prompt
- [ ] Skip human-edited concepts
- [ ] Unit tests with mocked LLM

**Acceptance Criteria:**
- Refinement triggers at correct thresholds
- Human-edited concepts never auto-refined
- Version incremented on refinement
- synthesis_method updated to "synthesized"

---

### Task 8: Integration with KGIntegratorV2
**Effort:** 3 hours
**Status:** Not Started

- [ ] Update `kg_integration_v2.py` to call agent workflow
- [ ] Add _process_agent_workflow method
- [ ] Ensure proper error handling
- [ ] Integration tests with full pipeline

**Acceptance Criteria:**
- MEDIUM/LOW confidence routes to agents
- HIGH confidence still uses auto-linker
- Errors logged with trace IDs
- End-to-end test passes

---

### Task 9: Neo4j Schema Updates
**Effort:** 2 hours
**Status:** Not Started

- [ ] Add PendingReview constraints and indexes
- [ ] Update SchemaManager for new schema
- [ ] Create migration script
- [ ] Verify schema idempotent

**Acceptance Criteria:**
- Schema migration runs without error
- Indexes improve query performance
- Migration is idempotent (safe to re-run)

---

### Task 10: Golden Dataset Validation
**Effort:** 4 hours
**Status:** Not Started

- [ ] Create golden dataset with MEDIUM confidence cases
- [ ] Create golden dataset with LOW confidence cases
- [ ] Implement accuracy measurement tests
- [ ] Tune prompts if accuracy < threshold
- [ ] Document tuning decisions

**Acceptance Criteria:**
- Evaluator achieves >90% accuracy
- Consensus achieves >85% accuracy
- False negative rate 0%
- False positive rate <5%

---

## Definition of Done

- [ ] All 10 tasks complete with acceptance criteria met
- [ ] Unit test coverage >90% for new code
- [ ] Integration tests pass 100%
- [ ] Golden dataset validation: Evaluator >90%, Consensus >85%
- [ ] Performance: Evaluator <5s, Consensus <30s, Queue query <100ms
- [ ] Review queue accessible via API
- [ ] Concept refinement triggers at thresholds
- [ ] Code reviewed and merged to main
- [ ] Documentation updated

---

## Open Questions

**Q1: LLM Model Selection**
- Should Evaluator use gpt-4o (faster) or claude-3-5-sonnet (better reasoning)?
- **Recommendation:** Start with gpt-4o for speed, switch if accuracy issues

**Q2: Batch Processing**
- Should we batch multiple mentions through workflow for efficiency?
- **Recommendation:** Single mention initially, optimize later if needed

**Q3: Agent Prompt Versioning**
- How to track prompt versions for reproducibility?
- **Recommendation:** Store prompt version in agent_results metadata

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-02-10 | construction-agent | Initial draft |
| 2026-02-10 | construction-agent | Status: Complete - all sections verified |
