"""Graph data endpoints for visualization.

Since the Canonical Problem Architecture, ingestion writes ``ProblemConcept``
(and ``ProblemMention``) nodes rather than ``:Problem``. These endpoints serve
the concept read model and union in any legacy ``:Problem`` node.
"""

import logging
from typing import Optional

from agentic_kg.knowledge_graph.repository import Neo4jRepository
from fastapi import APIRouter, Depends, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import GraphLink, GraphNode, GraphResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])

_PROBLEM_LABELS = {"ProblemConcept", "Problem", "ProblemMention"}


def _problem_type(labels: set[str]) -> Optional[str]:
    if labels & _PROBLEM_LABELS:
        return "problem"
    if "Paper" in labels:
        return "paper"
    if "Topic" in labels:
        return "topic"
    return None


def _problem_label(node, prefix: str) -> str:
    statement = node.get("canonical_statement") or node.get("statement") or prefix
    return statement[:50] + "..." if len(statement) > 50 else statement


def _problem_properties(node) -> dict:
    return {
        "statement": node.get("canonical_statement") or node.get("statement", ""),
        "status": node.get("status", "open"),
        "confidence": node.get("confidence"),
        "mention_count": node.get("mention_count", 0),
        "paper_count": node.get("paper_count", 0),
    }


@router.get("", response_model=GraphResponse)
def get_graph(
    limit: int = Query(default=100, ge=1, le=500, description="Max nodes to return"),
    topic_id: Optional[str] = Query(
        default=None, description="Filter problems by Topic id (BELONGS_TO)"
    ),
    include_papers: bool = Query(default=True, description="Include paper nodes"),
    include_topics: bool = Query(default=True, description="Include Topic nodes"),
    repo: Neo4jRepository = Depends(get_repo),
) -> GraphResponse:
    """
    Get graph data for visualization.

    Returns nodes (problems, papers, topics) and links (relations) between
    them. When ``topic_id`` is provided, only problems associated with that
    Topic are returned (direct ``BELONGS_TO`` or, concept-side, via a source
    paper that ``RESEARCHES`` the topic).
    """
    nodes: list[GraphNode] = []
    links: list[GraphLink] = []
    seen_nodes: set[str] = set()
    problem_element_ids: list[str] = []

    def add_problem_node(node, kind_hint: str = "problem"):
        node_id = f"problem:{node.element_id}"
        if node_id in seen_nodes:
            return node_id
        seen_nodes.add(node_id)
        problem_element_ids.append(node.element_id)
        nodes.append(
            GraphNode(
                id=node_id,
                label=_problem_label(node, kind_hint),
                type="problem",
                properties=_problem_properties(node),
            )
        )
        return node_id

    try:
        with repo.session() as session:
            if topic_id:
                concept_query = """
                MATCH (c:ProblemConcept)
                WHERE (c)-[:BELONGS_TO]->(:Topic {id: $topic_id})
                   OR EXISTS {
                        (c)<-[:INSTANCE_OF]-(:ProblemMention)-[:EXTRACTED_FROM]
                             ->(:Paper)-[:RESEARCHES]->(:Topic {id: $topic_id})
                   }
                RETURN c
                LIMIT $limit
                """
                legacy_query = """
                MATCH (p:Problem)-[:BELONGS_TO]->(:Topic {id: $topic_id})
                RETURN p
                LIMIT $limit
                """
                params: dict = {"limit": limit, "topic_id": topic_id}
            else:
                concept_query = "MATCH (c:ProblemConcept) RETURN c LIMIT $limit"
                legacy_query = "MATCH (p:Problem) RETURN p LIMIT $limit"
                params = {"limit": limit}

            for record in session.run(concept_query, **params):
                add_problem_node(record["c"], "problem")
            for record in session.run(legacy_query, **params):
                add_problem_node(record["p"], "problem")

            # Legacy Problem -> Problem relations.
            rel_query = """
            MATCH (p1:Problem)-[r]->(p2:Problem)
            RETURN p1, type(r) AS rel_type, r, p2
            LIMIT $limit
            """
            for record in session.run(rel_query, limit=limit * 2):
                source_id = add_problem_node(record["p1"], "problem")
                target_id = add_problem_node(record["p2"], "problem")
                links.append(
                    GraphLink(
                        source=source_id,
                        target=target_id,
                        type=record["rel_type"],
                        properties=dict(record["r"]) if record["r"] else {},
                    )
                )

            if include_papers:
                paper_queries = [
                    """
                    MATCH (c:ProblemConcept)<-[:INSTANCE_OF]-(:ProblemMention)
                          -[:EXTRACTED_FROM]->(paper:Paper)
                    WHERE elementId(c) IN $ids
                    RETURN c AS problem, paper
                    LIMIT $limit
                    """,
                    """
                    MATCH (p:Problem)-[:EXTRACTED_FROM]->(paper:Paper)
                    WHERE elementId(p) IN $ids
                    RETURN p AS problem, paper
                    LIMIT $limit
                    """,
                ]
                for index, query in enumerate(paper_queries):
                    for record in session.run(
                        query, ids=problem_element_ids, limit=limit
                    ):
                        problem = record["problem"]
                        paper = record["paper"]
                        problem_id = (
                            f"problem:{problem.element_id}"
                            if index == 1
                            else add_problem_node(problem, "problem")
                        )
                        paper_id = f"paper:{paper.element_id}"
                        if paper_id not in seen_nodes:
                            seen_nodes.add(paper_id)
                            title = paper.get("title", "Unknown Paper")
                            label = title[:40] + "..." if len(title) > 40 else title
                            nodes.append(
                                GraphNode(
                                    id=paper_id,
                                    label=label,
                                    type="paper",
                                    properties={
                                        "title": title,
                                        "doi": paper.get("doi"),
                                        "year": paper.get("year"),
                                        "authors": paper.get("authors", []),
                                    },
                                )
                            )
                        links.append(
                            GraphLink(
                                source=problem_id,
                                target=paper_id,
                                type="EXTRACTED_FROM",
                            )
                        )

            if include_topics and problem_element_ids:
                topic_queries = [
                    """
                    MATCH (c:ProblemConcept)-[:BELONGS_TO]->(topic:Topic)
                    WHERE elementId(c) IN $ids
                    RETURN c AS problem, topic
                    """,
                    """
                    MATCH (c:ProblemConcept)<-[:INSTANCE_OF]-(:ProblemMention)
                          -[:EXTRACTED_FROM]->(:Paper)-[:RESEARCHES]->(topic:Topic)
                    WHERE elementId(c) IN $ids
                    RETURN c AS problem, topic
                    """,
                    """
                    MATCH (p:Problem)-[:BELONGS_TO]->(topic:Topic)
                    WHERE elementId(p) IN $ids
                    RETURN p AS problem, topic
                    """,
                ]
                for query in topic_queries:
                    for record in session.run(query, ids=problem_element_ids):
                        problem = record["problem"]
                        topic = record["topic"]
                        problem_id = f"problem:{problem.element_id}"
                        if problem_id not in seen_nodes:
                            continue
                        topic_node_id = f"topic:{topic.get('id')}"
                        if topic_node_id not in seen_nodes:
                            seen_nodes.add(topic_node_id)
                            nodes.append(
                                GraphNode(
                                    id=topic_node_id,
                                    label=topic.get("name", "Unknown Topic"),
                                    type="topic",
                                    properties={
                                        "name": topic.get("name"),
                                        "level": topic.get("level"),
                                        "problem_count": topic.get("problem_count", 0),
                                    },
                                )
                            )
                        links.append(
                            GraphLink(
                                source=problem_id,
                                target=topic_node_id,
                                type="BELONGS_TO",
                            )
                        )

    except Exception as e:
        logger.error(f"Failed to get graph data: {e}")

    return GraphResponse(nodes=nodes, links=links)


@router.get("/neighbors/{node_id:path}", response_model=GraphResponse)
def get_neighbors(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=3, description="Traversal depth"),
    repo: Neo4jRepository = Depends(get_repo),
) -> GraphResponse:
    """
    Get neighboring nodes for a given node.

    Useful for expanding the graph from a selected node. Handles both
    canonical ``ProblemConcept`` and legacy ``:Problem`` node ids under the
    ``problem:`` prefix.
    """
    nodes: list[GraphNode] = []
    links: list[GraphLink] = []
    seen_nodes: set[str] = set()

    try:
        with repo.session() as session:
            if ":" not in node_id:
                return GraphResponse(nodes=[], links=[])
            prefix, element_id = node_id.split(":", 1)

            result = session.run(
                """
                MATCH (n)
                WHERE elementId(n) = $element_id
                OPTIONAL MATCH (n)-[r]-(neighbor)
                RETURN n,
                    collect({
                        rel_type: type(r),
                        neighbor: neighbor,
                        labels: labels(neighbor)
                    }) AS connections
                """,
                element_id=element_id,
            )
            record = result.single()
            if not record or record["n"] is None:
                return GraphResponse(nodes=[], links=[])

            center = record["n"]
            center_labels = set(center.labels)
            center_type = _problem_type(center_labels) or prefix
            center_id = f"{center_type}:{center.element_id}"
            seen_nodes.add(center_id)
            nodes.append(
                GraphNode(
                    id=center_id,
                    label=_problem_label(center, center_type),
                    type=center_type,
                    properties=(
                        _problem_properties(center)
                        if center_type == "problem"
                        else dict(center)
                    ),
                )
            )

            for conn in record["connections"]:
                neighbor = conn["neighbor"]
                if neighbor is None:
                    continue
                neighbor_type = _problem_type(set(conn["labels"] or []))
                if neighbor_type is None:
                    continue
                neighbor_id = f"{neighbor_type}:{neighbor.element_id}"
                if neighbor_id not in seen_nodes:
                    seen_nodes.add(neighbor_id)
                    if neighbor_type == "problem":
                        label = _problem_label(neighbor, "problem")
                        props = _problem_properties(neighbor)
                    elif neighbor_type == "paper":
                        title = neighbor.get("title", "Unknown")
                        label = title[:40] + "..." if len(title) > 40 else title
                        props = {"title": title, "doi": neighbor.get("doi")}
                    else:
                        label = neighbor.get("name", "Unknown Topic")
                        props = {
                            "name": neighbor.get("name"),
                            "level": neighbor.get("level"),
                        }
                    nodes.append(
                        GraphNode(
                            id=neighbor_id,
                            label=label,
                            type=neighbor_type,
                            properties=props,
                        )
                    )
                links.append(
                    GraphLink(
                        source=center_id,
                        target=neighbor_id,
                        type=conn["rel_type"],
                    )
                )

    except Exception as e:
        logger.error(f"Failed to get neighbors: {e}")

    return GraphResponse(nodes=nodes, links=links)
