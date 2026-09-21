"""Read-only compatibility harness for the KGIS/KGCS cutover (Phase 7).

Before the canonical projection can replace the legacy graph, the Ranking,
Continuation, Evaluation and Synthesis agents, the API routers and the
retrieval/search paths have to be shown to still work against it. This package
holds the two read-only pieces that make that showable:

* :mod:`~agentic_kg.migration.compat.read_paths` — the application read-path
  inventory as machine-checkable data, each entry anchored to a literal
  fragment of the file it describes.
* :mod:`~agentic_kg.migration.compat.probes` — the deterministic pre-LLM probe
  set and the anti-vacuity guard.

Nothing here writes. Nothing here imports the canonical store, the projector,
``kgis`` or ``kgcs``, so importing this package is free on a default install.

What this package does **not** cover, and why
---------------------------------------------
Four application surfaces are excluded by
:data:`~agentic_kg.migration.compat.read_paths.SCOPED_OUT_SURFACES` because they
do not reach the graph at all today:

1. ``PUT /api/problems/{id}`` — positional-argument mismatch, uncaught, 500.
2. all of ``/api/reviews/*`` — ``ReviewQueueService`` calls repository methods
   that do not exist.
3. all four ``SynthesisAgent`` writes — wrong keyword arguments, swallowed by
   ``logger.warning``.
4. ``ContinuationAgent``'s related-problem context — ``limit=`` passed to a
   method that has no such parameter, swallowed by ``logger.warning``. Found by
   this phase; see the note on that entry.

A compatibility test built on any of these would be quantifying over a set that
is empty because the code raises before it queries — exactly the vacuous shape
mapping spec §9.0 names. Repairing any of them should widen this harness, and
``test_scoped_out_write_surfaces.py`` turns red when one is repaired so the
decision is forced rather than forgotten.
"""

from agentic_kg.migration.compat.probes import (
    CYPHER_PROBES,
    CypherProbe,
    GraphSurface,
    ProbeInputs,
    ProbeResult,
    VacuousProbe,
    assert_parity,
    canonicalise,
    require_non_vacuous,
    run_all_probes,
    run_probe,
)
from agentic_kg.migration.compat.read_paths import (
    READ_PATHS,
    SCOPED_OUT_SURFACES,
    CompatClass,
    ReadPath,
    ScopedOutSurface,
    labels_read,
    ordering_keys_read,
    paths_for_class,
    relationships_read,
    vector_indexes_read,
)

__all__ = [
    "CYPHER_PROBES",
    "READ_PATHS",
    "SCOPED_OUT_SURFACES",
    "CompatClass",
    "CypherProbe",
    "GraphSurface",
    "ProbeInputs",
    "ProbeResult",
    "ReadPath",
    "ScopedOutSurface",
    "VacuousProbe",
    "assert_parity",
    "canonicalise",
    "labels_read",
    "ordering_keys_read",
    "paths_for_class",
    "relationships_read",
    "require_non_vacuous",
    "run_all_probes",
    "run_probe",
    "vector_indexes_read",
]
