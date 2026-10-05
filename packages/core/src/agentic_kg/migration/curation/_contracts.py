"""The single door onto the optional KGCS packages for the curation path.

Mirrors ``migration/neo4j/_contracts.py`` and ``migration/ingestion/_contracts.py``:
every ``kgcs`` / ``kg_contracts`` name this subpackage uses is re-exported from
here, and *nothing else* under ``migration/curation/`` may import those
packages directly.

The reason is ADR-0004 decision 3. A guard placed only in ``__init__.py`` is
bypassable — ``import agentic_kg.migration.curation.pipeline`` reaches the
submodule without executing the package ``__init__``, and the caller gets a
bare ``ModuleNotFoundError: No module named 'kgcs'`` from several frames deep
instead of "install the migration extra". One door, and the guard runs *before*
the first optional import in it.

``test_import_gate.py`` enforces both halves by AST walk: only this module may
name the optional packages, and the ``require_*`` calls must appear at a lower
line number than the first import of what they guard.
"""

from __future__ import annotations

from agentic_kg.migration.imports import require_kg_contracts, require_kgcs

# The guard runs at import time, before anything below it. Its ordering
# relative to the imports that follow is asserted by a test, because a guard
# that runs *after* the thing it guards is decoration, not protection.
require_kg_contracts()
require_kgcs()

from kg_contracts.candidates import (  # noqa: E402
    ArtifactCandidate,
    AttributeAssertionCandidate,
    Candidate,
    EntityCandidate,
    RelationCandidate,
)
from kg_contracts.curation import (  # noqa: E402
    AuditRecord,
    CurationOperation,
    CurationOperationType,
    CurationPlan,
    ResolutionDecision,
    ValidationDecision,
)
from kg_contracts.identity import is_identity_id  # noqa: E402
from kg_contracts.policy import AdjudicationRoute, ConfidencePolicy  # noqa: E402
from kg_contracts.stores import GraphMutationStore, GraphReader  # noqa: E402
from kgcs.audit import AuditSink  # noqa: E402
from kgcs.clock import Clock, FixedClock  # noqa: E402
from kgcs.engine import CandidateOutcome, CurationEngine, EngineResult  # noqa: E402
from kgcs.executor import PlanExecutor  # noqa: E402
from kgcs.executor.compensate import CompensationResult, Compensator  # noqa: E402
from kgcs.executor.executor import (  # noqa: E402
    EpochPublisher,
    ExecutionAuditSink,
    ExecutionOutcome,
    ExecutionRecord,
)
from kgcs.ids import DerivedIdFactory, IdFactory  # noqa: E402
from kgcs.memory import InMemoryAuditSink  # noqa: E402
from kgcs.memory.execution import (  # noqa: E402
    InMemoryEpochPublisher,
    InMemoryExecutionAuditSink,
)

__all__ = [
    "AdjudicationRoute",
    "ArtifactCandidate",
    "AttributeAssertionCandidate",
    "AuditRecord",
    "AuditSink",
    "Candidate",
    "CandidateOutcome",
    "Clock",
    "CompensationResult",
    "Compensator",
    "ConfidencePolicy",
    "CurationEngine",
    "CurationOperation",
    "CurationOperationType",
    "CurationPlan",
    "DerivedIdFactory",
    "EngineResult",
    "EntityCandidate",
    "EpochPublisher",
    "ExecutionAuditSink",
    "ExecutionOutcome",
    "ExecutionRecord",
    "FixedClock",
    "GraphMutationStore",
    "GraphReader",
    "IdFactory",
    "InMemoryAuditSink",
    "InMemoryEpochPublisher",
    "InMemoryExecutionAuditSink",
    "PlanExecutor",
    "RelationCandidate",
    "ResolutionDecision",
    "ValidationDecision",
    "is_identity_id",
]
