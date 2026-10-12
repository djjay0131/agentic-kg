"""
FastAPI application for Agentic Knowledge Graphs.

Run with: uvicorn agentic_kg_api.main:app --reload
"""

import os
from contextlib import asynccontextmanager

from agentic_kg.logging_config import get_logger, setup_logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentic_kg_api import __version__
from agentic_kg_api.config import get_api_config
from agentic_kg_api.dependencies import get_relations, get_repo, get_search, reset_dependencies
from agentic_kg_api.routers import (
    agents,
    canonical,
    concepts,
    extract,
    graph,
    ingest,
    papers,
    problems,
    reviews,
    runs,
    search,
    topics,
)
from agentic_kg_api.routers import methods as methods_router
from agentic_kg_api.routers import models as models_router
from agentic_kg_api.schemas import HealthResponse, StatsResponse
from agentic_kg_api.tasks import setup_event_bridge, teardown_event_bridge

# Configure centralized logging
setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format=os.getenv("LOG_FORMAT", "text"),  # Use "json" in production
    enable_cloud_logging=os.getenv("ENABLE_CLOUD_LOGGING", "false").lower() == "true",
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    logger.info("Starting Agentic KG API...")

    # Initialize WorkflowRunner with shared dependencies
    try:
        from agentic_kg.extraction.llm_client import create_llm_client

        llm_client = create_llm_client()
        repo = get_repo()
        search_svc = get_search()
        relation_svc = get_relations()

        from agentic_kg.agents.runner import WorkflowRunner

        runner = WorkflowRunner(
            llm_client=llm_client,
            repository=repo,
            search_service=search_svc,
            relation_service=relation_svc,
        )
        agents.set_workflow_runner(runner)
        setup_event_bridge()
        logger.info("WorkflowRunner initialized successfully")
    except Exception as e:
        logger.warning(f"WorkflowRunner initialization failed: {e}. Agent workflows will be unavailable.")

    yield

    logger.info("Shutting down Agentic KG API...")
    teardown_event_bridge()
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


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all HTTP requests with timing."""
    import time

    start_time = time.time()
    request_id = str(id(request))[-6:]  # Use last 6 digits of object id

    logger.info(f"[{request_id}] {request.method} {request.url.path}")

    response = await call_next(request)

    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} - "
        f"Status: {response.status_code}, Duration: {duration_ms:.0f}ms"
    )

    return response


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
app.include_router(agents.router)
app.include_router(reviews.router)
app.include_router(ingest.router)
app.include_router(topics.router)
app.include_router(concepts.router)
app.include_router(models_router.router)
app.include_router(methods_router.router)
app.include_router(canonical.router)
app.include_router(runs.router)


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
    """Get system statistics.

    Counts the canonical ``ProblemConcept`` read model (unioned with any
    legacy ``:Problem`` nodes). Before the fix this counted only
    ``:Problem``, so a run that ingested 45 problems reported 0.
    """
    try:
        repo = get_repo()
        stats = repo.get_problem_stats()
        return StatsResponse(
            total_problems=stats["total_problems"],
            total_papers=stats["total_papers"],
            total_topics=stats["total_topics"],
            problems_by_status=stats["problems_by_status"],
            problems_by_topic=stats["problems_by_topic"],
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
