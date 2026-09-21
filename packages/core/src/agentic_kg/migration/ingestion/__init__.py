"""KGIS shadow ingestion: source -> KGIS -> candidates + evidence -> ledger.

Shadow only. This subpackage proposes candidates into an isolated ledger and
evidence registry; it writes no canonical fact and mutates no production graph.

**Importing this package imports `kgis`.** Unlike `agentic_kg.migration`, which
is deliberately free to import on a default install, everything here is built
on the optional packages, so the guard in `_contracts` fires on import and
raises `MigrationDependencyError` with install instructions when the `migration`
extra is absent. That is the right failure: a shadow-ingestion module that
imported successfully and then did nothing would be worse.

Start at :func:`~agentic_kg.migration.ingestion.pipeline.run_shadow_ingestion`.
"""

from agentic_kg.migration.ingestion.arm_export import (
    UNAVAILABLE_REASON,
    ShadowArmEntity,
    ShadowArmPaper,
    as_arm_payload,
    to_arm_papers,
)
from agentic_kg.migration.ingestion.corpus import (
    GRADED_SLUGS,
    CorpusError,
    CorpusPaper,
    load_corpus,
    load_paper,
)
from agentic_kg.migration.ingestion.documents import (
    PaperDocument,
    SectionChunk,
    SectionChunker,
)
from agentic_kg.migration.ingestion.extractors import research_extractors
from agentic_kg.migration.ingestion.identity import (
    Fragment,
    JoinKey,
    join_key,
    norm,
    normalize_doi,
    parse_fragment,
    span_digest,
)
from agentic_kg.migration.ingestion.ontology import (
    GRAPH_ID,
    ONTOLOGY_VERSION,
    RESEARCH_ONTOLOGY,
    research_candidate_validator,
)
from agentic_kg.migration.ingestion.pipeline import (
    ShadowIngestionDisabled,
    ShadowRunResult,
    build_shadow_pipeline,
    run_shadow_ingestion,
)
from agentic_kg.migration.ingestion.replay import (
    assert_no_live_provider,
    importer_replay_client,
    recording_client,
    replay_client_from_file,
    save_recording,
)
from agentic_kg.migration.ingestion.stores import ShadowStores

__all__ = [
    "GRADED_SLUGS",
    "GRAPH_ID",
    "ONTOLOGY_VERSION",
    "RESEARCH_ONTOLOGY",
    "UNAVAILABLE_REASON",
    "CorpusError",
    "CorpusPaper",
    "Fragment",
    "JoinKey",
    "PaperDocument",
    "SectionChunk",
    "SectionChunker",
    "ShadowArmEntity",
    "ShadowArmPaper",
    "ShadowIngestionDisabled",
    "ShadowRunResult",
    "ShadowStores",
    "as_arm_payload",
    "assert_no_live_provider",
    "build_shadow_pipeline",
    "importer_replay_client",
    "join_key",
    "load_corpus",
    "load_paper",
    "norm",
    "normalize_doi",
    "parse_fragment",
    "recording_client",
    "replay_client_from_file",
    "research_candidate_validator",
    "research_extractors",
    "run_shadow_ingestion",
    "save_recording",
    "span_digest",
    "to_arm_papers",
]
