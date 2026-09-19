"""Deterministic legacy-vs-new-vs-gold evaluation over the ground-truth chain.

This subpackage is a **narrow adapter onto ``kg_eval``**, which ships inside the
pinned ``agentic-kgis``. It does not reimplement precision, recall, bootstrap
intervals, ablation verdicts, or report rendering — ``kg_eval`` already has all
of those, with the honest-null policy of ADR-0009 built into the types
(``MetricValue.insufficient`` returns ``None``, never ``0.0``;
``AblationVerdict.INSUFFICIENT_EVIDENCE`` is a real outcome). What this package
adds is the translation between our fixtures and those types, and the metrics
this corpus needs that ``kg_eval`` does not own.

``kg_eval`` lives behind the optional ``migration`` extra, so **importing this
subpackage requires that extra**. The import is left unguarded on purpose:
:mod:`agentic_kg.migration.imports` exists to keep *optional* code paths from
crashing on a default install, and this package is not an optional path inside a
running pipeline — it is an evaluation tool whose entire reason to exist is
``kg_eval``. A guard here would only convert an actionable ``ImportError`` at the
top of a deliberate import into a confusing ``None`` further down. Callers that
must branch should use
:func:`agentic_kg.migration.imports.check_migration_module` *before* importing
this package.

Layout:

* :mod:`.corpus` — reads the fixtures into framework-neutral records.
* :mod:`.adapter` — builds ``GoldSet`` / ``Candidate`` objects from them.
* :mod:`.arms` — the legacy, new and gold (control) arms.
* :mod:`.metrics` — the named-resource gate and the curation metric provider.
* :mod:`.runner` — assembles and renders the whole comparison.
"""

from agentic_kg.migration.evaluation.adapter import (
    build_candidates,
    build_goldset,
    build_surface_index,
    filter_candidates,
)
from agentic_kg.migration.evaluation.arms import (
    ArmUnavailable,
    BuiltArm,
    build_gold_arm,
    build_legacy_arm,
    build_new_arm,
)
from agentic_kg.migration.evaluation.corpus import (
    RECONCILED_SLUGS,
    ReconciledPaper,
    load_importer_output,
    load_reconciled_papers,
)
from agentic_kg.migration.evaluation.metrics import (
    CurationMetricProvider,
    NamedResourceDisposition,
)
from agentic_kg.migration.evaluation.runner import (
    DEFAULT_BOOTSTRAP,
    EvaluationReport,
    render_report,
    run_evaluation,
)

__all__ = [
    "DEFAULT_BOOTSTRAP",
    "RECONCILED_SLUGS",
    "ArmUnavailable",
    "BuiltArm",
    "BuiltArm",
    "CurationMetricProvider",
    "EvaluationReport",
    "NamedResourceDisposition",
    "ReconciledPaper",
    "build_candidates",
    "build_gold_arm",
    "build_goldset",
    "build_legacy_arm",
    "build_new_arm",
    "build_surface_index",
    "filter_candidates",
    "load_importer_output",
    "load_reconciled_papers",
    "render_report",
    "run_evaluation",
]
