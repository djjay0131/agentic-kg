"""The runnable migration path: summary shape, honest nulls, and idempotency.

Fast tier only — the real KGIS shadow pipeline over the committed corpus against
the deterministic replay client, the real KGCS curation engine, and the
reference ``MemoryGraphStore``. No Docker, no Neo4j, no network. The Neo4j
idempotency proof lives in ``curation/test_neo4j_run_idempotency.py`` and runs
only where an owned container is available.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "kgcs",
    reason=(
        "the opt-in 'migration' extra is not installed; pip install './packages/core[migration]'"
    ),
)

from agentic_kg.migration.config import MigrationConfig  # noqa: E402
from agentic_kg.migration.ingestion import (  # noqa: E402
    ShadowStores,
    importer_replay_client,
    load_corpus,
)
from agentic_kg.migration.ingestion.corpus import CorpusError  # noqa: E402
from agentic_kg.migration.neo4j import SUPPORTED_OPERATIONS  # noqa: E402
from agentic_kg.migration.run import (  # noqa: E402
    MigrationRunDisabled,
    run_migration,
    select_papers,
)
from kg_contracts.testing.memory import MemoryGraphStore  # noqa: E402


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


@pytest.fixture(scope="module")
def two_runs(corpus):
    """One store, run twice. Everything the idempotency claims rest on."""
    config = MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True)
    stores = ShadowStores.in_memory()
    store = MemoryGraphStore()
    try:
        first = run_migration(
            config=config,
            papers=corpus,
            client=importer_replay_client(corpus),
            stores=stores,
            store=store,
            namespace="test-ns",
            supported_operations=SUPPORTED_OPERATIONS,
        )
        after_first = len(store.find_entities())
        epoch_first = store.current_epoch()
        second = run_migration(
            config=config,
            papers=corpus,
            client=importer_replay_client(corpus),
            stores=stores,
            store=store,
            namespace="test-ns",
            supported_operations=SUPPORTED_OPERATIONS,
        )
        yield SimpleNamespace(
            first=first,
            second=second,
            entities_after_first=after_first,
            entities_after_second=len(store.find_entities()),
            epoch_first=epoch_first,
            epoch_after_second=store.current_epoch(),
            store=store,
        )
    finally:
        stores.close()


def test_first_run_commits_one_identity_per_paper(two_runs, corpus):
    assert two_runs.first.committed is True
    assert two_runs.first.committed_candidates == len(corpus)
    assert set(two_runs.first.committed_operations) == {"CREATE_IDENTITY"}
    assert two_runs.entities_after_first == len(corpus)


def test_first_run_publishes_an_epoch(two_runs):
    assert two_runs.first.epoch >= 1
    assert two_runs.first.published_epoch_this_run == two_runs.first.epoch
    assert two_runs.first.execution_outcome == "COMMITTED"


def test_every_candidate_lands_in_exactly_one_bucket(two_runs):
    first = two_runs.first
    total = sum(first.candidates_by_kind.values())
    assert first.planned_candidates + first.deferred_candidates + first.rejected_candidates == total


def test_deferrals_are_classified_not_silent(two_runs):
    first = two_runs.first
    assert first.deferred_candidates > 0
    assert sum(first.deferral_reasons.values()) == (
        first.deferred_candidates + first.rejected_candidates
    )
    # The two dominant reasons are the unwired stages, named rather than hidden.
    assert first.deferral_reasons.get("unresolved_identity_no_entity_resolution", 0) > 0
    assert first.deferral_reasons.get("awaiting_adviser_or_review", 0) > 0


def test_honest_nulls_name_the_unwired_stages(two_runs):
    nulls = two_runs.first.honest_nulls
    for key in ("entity_resolution", "llm_adviser", "human_review", "live_provider"):
        assert key in nulls and nulls[key]
    assert "extraction_quality" in nulls  # deterministic replay client
    assert "ledger_persistence" in nulls


def test_evidence_is_referenced_and_counted(two_runs):
    assert two_runs.first.evidence_refs > 0


def test_summary_is_json_serialisable(two_runs):
    payload = json.dumps(two_runs.first.to_dict())
    assert "committed_operations" in payload


def test_second_run_does_not_duplicate_canonical_state(two_runs):
    assert two_runs.second.committed is False
    assert two_runs.entities_after_second == two_runs.entities_after_first
    assert two_runs.epoch_after_second == two_runs.epoch_first


def test_disabled_config_refuses_before_any_run(corpus):
    stores = ShadowStores.in_memory()
    try:
        with pytest.raises(MigrationRunDisabled):
            run_migration(
                config=MigrationConfig(use_kgis_ingestion=False, use_kgcs_resolution=True),
                papers=corpus,
                client=importer_replay_client(corpus),
                stores=stores,
                store=MemoryGraphStore(),
                namespace="test-ns",
            )
        with pytest.raises(MigrationRunDisabled):
            run_migration(
                config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
                papers=corpus,
                client=importer_replay_client(corpus),
                stores=stores,
                store=MemoryGraphStore(),
                namespace="test-ns",
            )
    finally:
        stores.close()


class TestSelectPapers:
    def test_by_slug_subset(self, corpus):
        selected = select_papers(slugs=["cskg"])
        assert [paper.slug for paper in selected] == ["cskg"]

    def test_by_doi_is_case_insensitive(self, corpus):
        doi = corpus[0].doi
        selected = select_papers(dois=[doi.upper()])
        assert [paper.slug for paper in selected] == [corpus[0].slug]

    def test_unknown_doi_refuses_rather_than_returning_nothing(self):
        with pytest.raises(CorpusError, match="not in the frozen corpus"):
            select_papers(dois=["10.9999/does-not-exist"])

    def test_unknown_slug_refuses(self):
        with pytest.raises(CorpusError, match="unknown corpus slug"):
            select_papers(slugs=["not-a-paper"])

    def test_both_slugs_and_dois_is_a_programming_error(self):
        with pytest.raises(ValueError, match="not both"):
            select_papers(slugs=["cskg"], dois=["10.1007/x"])

    def test_no_selector_is_the_whole_corpus(self):
        assert len(select_papers()) == 8
