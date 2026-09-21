"""The single door onto the optional KGIS packages for the shadow-ingestion path.

Mirrors ``migration/neo4j/_contracts.py``: every ``kgis`` / ``kg_contracts``
name this subpackage uses is re-exported from here, and *nothing else* under
``migration/ingestion/`` may import those packages directly.

The reason is the one ADR-0004 decision 3 records. A guard placed only in
``__init__.py`` is bypassable — ``import agentic_kg.migration.ingestion.pipeline``
reaches the submodule without executing the package ``__init__``, and the user
gets a bare ``ModuleNotFoundError: No module named 'kgis'`` from four frames
deep instead of "install the migration extra". One door, and the guard runs
*before* the first optional import in it.

``test_config_injection.py`` enforces both halves of that by AST walk: only
this module may name the optional packages, and the ``require_*`` call must
appear at a lower line number than the first import of what it guards.
"""

from __future__ import annotations

from agentic_kg.migration.imports import require_kg_contracts, require_kgis

# The guard runs at import time, before anything below it. Its ordering
# relative to the imports that follow is asserted by a test, because a guard
# that runs *after* the thing it guards is decoration, not protection.
require_kg_contracts()
require_kgis()

from kg_contracts.candidates import (  # noqa: E402
    ArtifactCandidate,
    AttributeAssertionCandidate,
    Candidate,
    CandidateScores,
    EntityCandidate,
    RelationCandidate,
    SourceCoordinates,
)
from kg_contracts.evidence import (  # noqa: E402
    Evidence,
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
    present_evidence,
)
from kg_contracts.identity import EntityRef  # noqa: E402
from kg_contracts.ingestion import CompletionClient  # noqa: E402
from kg_contracts.stores import LedgerEntry, SubmissionStatus  # noqa: E402
from kgis.builders import (  # noqa: E402
    AttributeCandidateBuilder,
    BuildContext,
    CandidateBuilder,
    CompositeCandidateBuilder,
    EntityCandidateBuilder,
    SourceScoring,
    entity_semantic_key,
)
from kgis.clock import FixedClock  # noqa: E402
from kgis.evidence.store import SqliteEvidenceRegistry  # noqa: E402
from kgis.extraction.client import (  # noqa: E402
    RecordingCompletionClient,
    ReplayCompletionClient,
    ReplayMiss,
    is_deterministic,
    request_key,
)
from kgis.extraction.config import ExtractorConfig  # noqa: E402
from kgis.extraction.documents import Chunk, Document, IterableDocumentSource  # noqa: E402
from kgis.extraction.provenance import chunk_evidence_id  # noqa: E402
from kgis.extraction.runner import ExtractionPipeline  # noqa: E402
from kgis.ids import DeterministicIdStrategy, stable_suffix  # noqa: E402
from kgis.ledger.store import SqliteCandidateLedger  # noqa: E402
from kgis.ontology import CoverageCounter, Ontology  # noqa: E402
from kgis.records import NormalizedRecord  # noqa: E402
from kgis.report import IngestionReport  # noqa: E402
from kgis.validate import OntologyCandidateValidator  # noqa: E402

__all__ = [
    "ArtifactCandidate",
    "AttributeAssertionCandidate",
    "AttributeCandidateBuilder",
    "BuildContext",
    "Candidate",
    "CandidateBuilder",
    "CandidateScores",
    "Chunk",
    "CompletionClient",
    "CompositeCandidateBuilder",
    "CoverageCounter",
    "DeterministicIdStrategy",
    "Document",
    "EntityCandidate",
    "EntityCandidateBuilder",
    "EntityRef",
    "Evidence",
    "EvidenceRef",
    "EvidenceRelationship",
    "ExtractionPipeline",
    "ExtractorConfig",
    "FixedClock",
    "IngestionReport",
    "IterableDocumentSource",
    "LedgerEntry",
    "NormalizedRecord",
    "Ontology",
    "OntologyCandidateValidator",
    "Provenance",
    "RecordingCompletionClient",
    "RelationCandidate",
    "ReplayCompletionClient",
    "ReplayMiss",
    "SourceCoordinates",
    "SourceScoring",
    "SqliteCandidateLedger",
    "SqliteEvidenceRegistry",
    "SubmissionStatus",
    "chunk_evidence_id",
    "entity_semantic_key",
    "present_evidence",
    "is_deterministic",
    "request_key",
    "stable_suffix",
]
