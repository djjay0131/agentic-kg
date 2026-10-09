"""Nightly ingestion pipeline (nightly-pipeline P1).

The pieces:

* :mod:`agentic_kg.pipeline.catalog` — the validated query catalog
  (``config/ingest-queries.yaml``).
* :mod:`agentic_kg.pipeline.plan` — the deterministic, budget-bounded nightly
  planner and its CLI (``python -m agentic_kg.pipeline.plan``).
* :mod:`agentic_kg.pipeline.ingest` — the in-process bridge to the same
  ingestion entrypoint the staging ingest Job uses.
* :mod:`agentic_kg.pipeline.report` — the ``PipelineRun`` report model and its
  two writers (GCS-mounted JSON and the ``(:PipelineRun)`` Neo4j node).
* :mod:`agentic_kg.pipeline.nightly` — the orchestrator and its CLI
  (``python -m agentic_kg.pipeline.nightly``), the nightly Cloud Run Job's
  entrypoint.

The shared report contract lives at
``docs/design/nightly-pipeline-contract.md``.
"""

from agentic_kg.pipeline.catalog import QueryCatalog, QuerySpec, load_catalog

__all__ = ["QueryCatalog", "QuerySpec", "load_catalog"]
