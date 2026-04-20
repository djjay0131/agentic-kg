"""Graph data endpoints for visualization."""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import GraphLink, GraphNode, GraphResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("", response_model=GraphResponse)
def get_graph(
    limit: int = Query(default=100, ge=1, le=500, description="Max nodes to return"),
    topic_id: Optional[str] = Query(
        default=None, description="Filter problems by Topic id (BELONGS_TO)"
    ),
    include_papers: bool = Query(default=True, description="Include paper nodes"),
    include_topics: bool = Query(default=True, description="Include Topic nodes"),
) -> GraphResponse:
    """
    Get graph data for visualization.

    Returns nodes (problems, papers, topics) and links (relations) between them.
    When ``topic_id`` is provided, only problems BELONGS_TO that Topic are returned.
    """
    nodes: list[GraphNode] = []
    links: list[GraphLink] = []
    seen_nodes: set[str] = set()

    try:
        repo = get_repo()
        with repo.session() as session:
            if topic_id:
                problem_query = """
                MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {id: $topic_id})
                RETURN p
                LIMIT $limit
                """
                params = {"limit": limit, "topic_id": topic_id}
            else:
                problem_query = """
                MATCH (p:Problem)
                RETURN p
                LIMIT $limit
                """
                params = {"limit": limit}

            result = session.run(problem_query, **params)

            for record in result:
                node = record["p"]
                node_id = f"problem:{node.element_id}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    statement = node.get("statement", "")
                    label = statement[:50] + "..." if len(statement) > 50 else statement
                    nodes.append(
                        GraphNode(
                            id=node_id,
                            label=label,
                            type="problem",
                            properties={
                                "statement": statement,
                                "status": node.get("status", "open"),
                                "confidence": node.get("confidence"),
                            },
                        )
                    )

            # Relations between problems
            if topic_id:
                rel_query = """
                MATCH (p1:Problem)-[:BELONGS_TO]->(:Topic {id: $topic_id})
                MATCH (p1)-[r]->(p2:Problem)
                RETURN p1, type(r) as rel_type, r, p2
                LIMIT $limit
                """
                rel_params = {"limit": limit * 2, "topic_id": topic_id}
            else:
                rel_query = """
                MATCH (p1:Problem)-[r]->(p2:Problem)
                RETURN p1, type(r) as rel_type, r, p2
                LIMIT $limit
                """
                rel_params = {"limit": limit * 2}

            result = session.run(rel_query, **rel_params)

            for record in result:
                source_id = f"problem:{record['p1'].element_id}"
                target_id = f"problem:{record['p2'].element_id}"

                if source_id in seen_nodes and target_id not in seen_nodes:
                    p2 = record["p2"]
                    statement = p2.get("statement", "")
                    label = statement[:50] + "..." if len(statement) > 50 else statement
                    seen_nodes.add(target_id)
                    nodes.append(
                        GraphNode(
                            id=target_id,
                            label=label,
                            type="problem",
                            properties={
                                "statement": statement,
                                "status": p2.get("status", "open"),
                            },
                        )
                    )

                if source_id in seen_nodes and target_id in seen_nodes:
                    links.append(
                        GraphLink(
                            source=source_id,
                            target=target_id,
                            type=record["rel_type"],
                            properties=dict(record["r"]) if record["r"] else {},
                        )
                    )

            if include_papers:
                result = session.run(
                    """
                    MATCH (p:Problem)-[r:EXTRACTED_FROM]->(paper:Paper)
                    RETURN p, paper
                    LIMIT $limit
                    """,
                    limit=limit,
                )

                for record in result:
                    problem_id = f"problem:{record['p'].element_id}"
                    paper = record["paper"]
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

                    if problem_id in seen_nodes:
                        links.append(
                            GraphLink(
                                source=problem_id,
                                target=paper_id,
                                type="EXTRACTED_FROM",
                            )
                        )

            # Topic nodes (BELONGS_TO edges)
            if include_topics:
                problem_ids = [n.id for n in nodes if n.type == "problem"]
                if problem_ids:
                    element_ids = [pid.replace("problem:", "") for pid in problem_ids]
                    result = session.run(
                        """
                        MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic)
                        WHERE elementId(p) IN $ids
                        RETURN p, t
                        """,
                        ids=element_ids,
                    )
                    for record in result:
                        problem_id = f"problem:{record['p'].element_id}"
                        topic = record["t"]
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
) -> GraphResponse:
    """
    Get neighboring nodes for a given node.

    Useful for expanding the graph from a selected node.
    """
    nodes: list[GraphNode] = []
    links: list[GraphLink] = []
    seen_nodes: set[str] = set()

    try:
        repo = get_repo()
        with repo.session() as session:
            # Parse node_id to determine type
            if node_id.startswith("problem:"):
                element_id = node_id.replace("problem:", "")
                query = """
                    MATCH (p:Problem)
                    WHERE elementId(p) = $element_id
                    OPTIONAL MATCH (p)-[r]-(neighbor)
                    RETURN p, collect({rel: r, rel_type: type(r), neighbor: neighbor}) as connections
                """
                result = session.run(query, element_id=element_id)
                record = result.single()

                if record and record["p"]:
                    # Add the center node
                    node = record["p"]
                    center_id = f"problem:{node.element_id}"
                    seen_nodes.add(center_id)
                    statement = node.get("statement", "")
                    label = statement[:50] + "..." if len(statement) > 50 else statement
                    nodes.append(
                        GraphNode(
                            id=center_id,
                            label=label,
                            type="problem",
                            properties={
                                "statement": statement,
                                "status": node.get("status", "open"),
                            },
                        )
                    )

                    # Add neighbors
                    for conn in record["connections"]:
                        if conn["neighbor"]:
                            neighbor = conn["neighbor"]
                            labels = list(neighbor.labels)
                            if "Problem" in labels:
                                neighbor_id = f"problem:{neighbor.element_id}"
                                neighbor_type = "problem"
                                stmt = neighbor.get("statement", "")
                                neighbor_label = (
                                    stmt[:50] + "..." if len(stmt) > 50 else stmt
                                )
                                props = {
                                    "statement": stmt,
                                    "status": neighbor.get("status", "open"),
                                }
                            elif "Paper" in labels:
                                neighbor_id = f"paper:{neighbor.element_id}"
                                neighbor_type = "paper"
                                title = neighbor.get("title", "Unknown")
                                neighbor_label = (
                                    title[:40] + "..." if len(title) > 40 else title
                                )
                                props = {
                                    "title": title,
                                    "doi": neighbor.get("doi"),
                                }
                            else:
                                continue

                            if neighbor_id not in seen_nodes:
                                seen_nodes.add(neighbor_id)
                                nodes.append(
                                    GraphNode(
                                        id=neighbor_id,
                                        label=neighbor_label,
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
