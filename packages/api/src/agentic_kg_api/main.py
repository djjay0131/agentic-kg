"""
FastAPI application for Agentic Knowledge Graphs.

Run with: uvicorn agentic_kg_api.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentic_kg_api import __version__
from agentic_kg_api.config import get_api_config
from agentic_kg_api.dependencies import get_repo, reset_dependencies
from agentic_kg_api.routers import extract, graph, papers, problems, search
from agentic_kg_api.schemas import HealthResponse, StatsResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    logger.info("Starting Agentic KG API...")
    yield
    logger.info("Shutting down Agentic KG API...")
    reset_dependencies()


app = FastAPI(
    title="Agentic Knowledge Graph API",
    description="API for research problem extraction and knowledge graph management",
    version=__version__,
    lifespan=lifespan,
)

# CORS
config = get_api_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Error handling
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# Include routers
app.include_router(problems.router)
app.include_router(papers.router)
app.include_router(search.router)
app.include_router(extract.router)
app.include_router(graph.router)


# Health and stats endpoints
@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check() -> HealthResponse:
    """Health check endpoint."""
    neo4j_connected = False
    try:
        repo = get_repo()
        neo4j_connected = repo.verify_connectivity()
    except Exception as e:
        logger.warning(f"Neo4j health check failed: {e}")

    return HealthResponse(
        status="ok",
        version=__version__,
        neo4j_connected=neo4j_connected,
    )


@app.get("/api/stats", response_model=StatsResponse, tags=["stats"])
def get_stats() -> StatsResponse:
    """Get system statistics."""
    try:
        repo = get_repo()
        with repo.session() as session:
            # Count problems
            result = session.run("MATCH (p:Problem) RETURN count(p) as count")
            total_problems = result.single()["count"]

            # Count papers
            result = session.run("MATCH (p:Paper) RETURN count(p) as count")
            total_papers = result.single()["count"]

            # Problems by status
            result = session.run(
                "MATCH (p:Problem) RETURN p.status as status, count(p) as count"
            )
            problems_by_status = {r["status"]: r["count"] for r in result}

            # Problems by domain
            result = session.run(
                "MATCH (p:Problem) WHERE p.domain IS NOT NULL "
                "RETURN p.domain as domain, count(p) as count"
            )
            problems_by_domain = {r["domain"]: r["count"] for r in result}

        return StatsResponse(
            total_problems=total_problems,
            total_papers=total_papers,
            problems_by_status=problems_by_status,
            problems_by_domain=problems_by_domain,
        )
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return StatsResponse()


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "name": "Agentic Knowledge Graph API",
        "version": __version__,
        "docs": "/docs",
    }
