"""Topic API endpoints (E-1)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg.knowledge_graph.embeddings import generate_topic_embedding
from agentic_kg.knowledge_graph.models import Topic, TopicLevel
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    NotFoundError,
)

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    ProblemSummary,
    TopicAssignRequest,
    TopicAssignResponse,
    TopicDetail,
    TopicListResponse,
    TopicProblemsResponse,
    TopicSearchResponse,
    TopicSearchResultItem,
    TopicSummary,
    TopicTreeNode,
    TopicTreeResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topics", tags=["topics"])


# =============================================================================
# Helpers
# =============================================================================


def _topic_to_summary(topic: Topic) -> TopicSummary:
    return TopicSummary(
        id=topic.id,
        name=topic.name,
        level=topic.level.value,
        parent_id=topic.parent_id,
        source=topic.source,
        description=topic.description,
        problem_count=topic.problem_count,
        paper_count=topic.paper_count,
    )


def _tree_dict_to_node(tree: dict) -> TopicTreeNode:
    children = [_tree_dict_to_node(c) for c in tree.get("children", [])]
    return TopicTreeNode(
        id=tree["id"],
        name=tree["name"],
        level=tree["level"],
        parent_id=tree.get("parent_id"),
        source=tree.get("source", "manual"),
        description=tree.get("description"),
        problem_count=tree.get("problem_count", 0),
        paper_count=tree.get("paper_count", 0),
        children=children,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=None)
def list_topics(
    tree: bool = Query(
        default=False,
        description="Return a nested hierarchy instead of a flat list",
    ),
    level: Optional[str] = Query(
        default=None,
        description="Filter by TopicLevel (domain / area / subtopic)",
    ),
    repo: Neo4jRepository = Depends(get_repo),
):
    """
    List topics.

    ``tree=true`` returns a hierarchical response with nested ``children``
    arrays; the flat form returns a ``TopicListResponse``.
    """
    if tree and level:
        raise HTTPException(
            status_code=400,
            detail="`tree=true` and `level` cannot be combined.",
        )

    if tree:
        trees = repo.get_topic_tree()
        return TopicTreeResponse(
            roots=[_tree_dict_to_node(t) for t in trees]
        )

    if level:
        try:
            topic_level = TopicLevel(level)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid level: {level}"
            )
        topics = repo.get_topics_by_level(topic_level)
    else:
        topics = []
        for lvl in TopicLevel:
            topics.extend(repo.get_topics_by_level(lvl))

    return TopicListResponse(
        topics=[_topic_to_summary(t) for t in topics],
        total=len(topics),
    )


@router.get("/search", response_model=TopicSearchResponse)
def search_topics(
    q: str = Query(..., min_length=1, description="Free-text query"),
    top_k: int = Query(default=10, ge=1, le=100),
    level: Optional[str] = Query(
        default=None,
        description="Optional TopicLevel filter (domain / area / subtopic)",
    ),
    repo: Neo4jRepository = Depends(get_repo),
) -> TopicSearchResponse:
    """Vector similarity search over topic embeddings."""
    try:
        embedding = generate_topic_embedding(q)
    except Exception as e:
        logger.warning(f"Failed to embed query {q!r}: {e}")
        raise HTTPException(status_code=500, detail="Embedding service unavailable")

    topic_level: Optional[TopicLevel] = None
    if level:
        try:
            topic_level = TopicLevel(level)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid level: {level}"
            )

    results = repo.search_topics_by_embedding(
        embedding=embedding, limit=top_k, level=topic_level
    )
    return TopicSearchResponse(
        query=q,
        results=[
            TopicSearchResultItem(
                topic=_topic_to_summary(topic),
                score=score,
            )
            for topic, score in results
        ],
    )


@router.get("/{topic_id}", response_model=TopicDetail)
def get_topic(
    topic_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> TopicDetail:
    """Get a Topic with its immediate parent and children."""
    try:
        topic = repo.get_topic(topic_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    children = repo.get_topic_children(topic.id)

    parent_summary: Optional[TopicSummary] = None
    if topic.parent_id:
        try:
            parent = repo.get_topic(topic.parent_id)
            parent_summary = _topic_to_summary(parent)
        except NotFoundError:
            parent_summary = None

    summary = _topic_to_summary(topic)
    return TopicDetail(
        **summary.model_dump(),
        parent=parent_summary,
        children=[_topic_to_summary(c) for c in children],
    )


@router.get("/{topic_id}/problems", response_model=TopicProblemsResponse)
def get_topic_problems(
    topic_id: str,
    include_subtopics: bool = Query(
        default=True,
        description=(
            "Include problems belonging to descendant topics via "
            "SUBTOPIC_OF traversal"
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    repo: Neo4jRepository = Depends(get_repo),
) -> TopicProblemsResponse:
    """Return Problems linked to the topic (optionally including descendants)."""
    try:
        repo.get_topic(topic_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Topic not found: {topic_id}")

    if include_subtopics:
        cypher = """
        MATCH (descendant:Topic)-[:SUBTOPIC_OF*0..]->(root:Topic {id: $topic_id})
        WITH collect(descendant) AS topics
        MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic)
        WHERE t IN topics
        RETURN DISTINCT p
        LIMIT $limit
        """
    else:
        cypher = """
        MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id})
        RETURN p
        LIMIT $limit
        """

    def _run(tx, tid, lim):
        result = tx.run(cypher, topic_id=tid, limit=lim)
        return [dict(r["p"]) for r in result]

    with repo.session() as session:
        records = session.execute_read(lambda tx: _run(tx, topic_id, limit))

    summaries = []
    for record in records:
        problem = repo._problem_from_neo4j(record)
        confidence = None
        if problem.extraction_metadata:
            confidence = problem.extraction_metadata.confidence_score
        summaries.append(
            ProblemSummary(
                id=problem.id,
                statement=problem.statement,
                status=(
                    problem.status.value
                    if hasattr(problem.status, "value")
                    else str(problem.status)
                ),
                confidence=confidence,
                created_at=problem.created_at,
            )
        )

    return TopicProblemsResponse(
        topic_id=topic_id,
        problems=summaries,
        total=len(summaries),
        include_subtopics=include_subtopics,
    )


@router.post("/{topic_id}/assign", response_model=TopicAssignResponse)
def assign_entity(
    topic_id: str,
    request: TopicAssignRequest,
    repo: Neo4jRepository = Depends(get_repo),
) -> TopicAssignResponse:
    """Link a Problem/Mention/Concept/Paper to a Topic via BELONGS_TO / RESEARCHES."""
    try:
        created = repo.assign_entity_to_topic(
            entity_id=request.entity_id,
            topic_id=topic_id,
            entity_label=request.entity_label,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return TopicAssignResponse(
        topic_id=topic_id,
        entity_id=request.entity_id,
        entity_label=request.entity_label,
        created=created,
    )
