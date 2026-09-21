"""KGCS curation: candidates -> plan -> executor -> canonical graph -> epoch.

The producer the `new` arm of every comparison was missing. It takes the
``Sequence[Candidate]`` the KGIS shadow path already emits, runs it through the
deterministic KGCS core, and — when the emitted plan auto-applies anything —
hands that plan to ``kgcs.executor.PlanExecutor``, the only component in this
architecture that may mutate canonical state.

**Importing this package imports ``kgcs``.** Unlike ``agentic_kg.migration``,
which is deliberately free to import on a default install, everything here is
built on the optional packages, so the guard in ``_contracts`` fires on import
and raises ``MigrationDependencyError`` with install instructions when the
``migration`` extra is absent. A curation module that imported successfully and
then did nothing would be the worse failure.

**Everything is off by default.** :func:`run_curation` raises
:class:`CurationDisabled` unless the *injected* ``MigrationConfig`` has
``use_kgcs_resolution=True``.

Start at :func:`~agentic_kg.migration.curation.pipeline.run_curation`; read
:mod:`~agentic_kg.migration.curation.policy` first for what this path will and
will not auto-apply, and why.
"""

from agentic_kg.migration.curation.arm_export import (
    GRADED_ENTITY_TYPES,
    CuratedArm,
    curated_arm,
)
from agentic_kg.migration.curation.pipeline import (
    ARTIFACT_REASON,
    UNRESOLVED_SUBJECT_REASON,
    CurationDisabled,
    CurationRunResult,
    Deferral,
    UnsafeIdentityRelaxation,
    classify,
    run_curation,
    unkeyed_new_identities,
)
from agentic_kg.migration.curation.policy import (
    CONTRACT_DEFAULT_POLICY,
    CURATION_GRAPH_ID,
    REGISTERED_IDENTIFIER_NAMESPACES,
    RUN_INSTANT,
    STRUCTURED_IDENTITY_POLICY,
    curation_engine,
)
from agentic_kg.migration.curation.rollback import (
    NotRollbackable,
    RollbackResult,
    roll_back,
)

__all__ = [
    "ARTIFACT_REASON",
    "CONTRACT_DEFAULT_POLICY",
    "CURATION_GRAPH_ID",
    "GRADED_ENTITY_TYPES",
    "REGISTERED_IDENTIFIER_NAMESPACES",
    "RUN_INSTANT",
    "STRUCTURED_IDENTITY_POLICY",
    "UNRESOLVED_SUBJECT_REASON",
    "CuratedArm",
    "CurationDisabled",
    "CurationRunResult",
    "Deferral",
    "NotRollbackable",
    "RollbackResult",
    "UnsafeIdentityRelaxation",
    "classify",
    "curated_arm",
    "curation_engine",
    "roll_back",
    "run_curation",
    "unkeyed_new_identities",
]
