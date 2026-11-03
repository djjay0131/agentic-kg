"""RQ1 extraction-reliability harness.

Measures precision, recall and F1 of extracted problems, assumptions,
constraints, datasets, metrics and baselines against versioned human
annotations, stratified by section type, with inter-annotator agreement.

This package is intentionally independent of ``kg_eval`` (which ships in the
optional ``migration`` extra): the RQ1 harness must run on a default install and
in CI with no external service or model call. The provenance-scoring bug the
brief names lives in ``kg_eval`` and was already fixed at the pinned
``agentic-kgis`` v0.6.0; see ``docs/evaluation/rq1-annotation-guide.md``.

Entry point: ``python -m agentic_kg.evaluation.rq1``.
"""

from agentic_kg.evaluation.schema import (
    SCHEMA_VERSION,
    AnnotationRecord,
    CoverageRecord,
    FieldType,
    SectionType,
)

__all__ = [
    "SCHEMA_VERSION",
    "AnnotationRecord",
    "CoverageRecord",
    "FieldType",
    "SectionType",
]
