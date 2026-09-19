"""Regression tests for the concurrent-CI-run data race in the integration suite.

The defect
----------
``neo4j_repository`` used to run a global ``DETACH DELETE`` over every
``TEST_``-prefixed node, before *and* after every test, against shared staging
Neo4j. The prefix is run-global, and the workflow's ``concurrency:`` group is
keyed per-ref, so two CI runs on different refs executed that sweep against one
another's in-flight rows. The observable symptom was a node vanishing between
its own creation and the next line of the same test::

    NotFoundError: Method not found: a1b3b505-d9e9-420b-9f77-587d9ca3f673
    NotFoundError: Cannot link CITES: source Paper '10.TEST_A/7d7802d3' ... not found

That is a data race, not flakiness.

The fix
-------
The sweep is now (a) teardown-only and (b) refused outright unless this pytest
session exclusively owns the database. CI provisions a per-run throwaway Neo4j,
so the sweep is safe by construction and the shared-instance configuration that
permitted cross-run destruction is unreachable.

These tests fail if either half of that guard is reverted.
"""

import uuid

import pytest

from ..conftest import (
    TEST_DATA_SWEEP_QUERY,
    SharedDatabaseSweepError,
    neo4j_is_ephemeral,
    sweep_test_data,
)

pytestmark = pytest.mark.integration


def _seed_foreign_run(repo, run_label: str) -> str:
    """Create a TEST_-prefixed node standing in for another CI run's data."""
    node_id = f"TEST_{run_label}_{uuid.uuid4().hex[:12]}"
    with repo.session() as session:
        session.run(
            "CREATE (n:Problem {id: $id, statement: $stmt, domain: $domain})",
            id=node_id,
            stmt=f"TEST_ statement for {run_label}",
            domain=f"TEST_{run_label}",
        )
    return node_id


def _exists(repo, node_id: str) -> bool:
    with repo.session() as session:
        record = session.run(
            "MATCH (n {id: $id}) RETURN count(n) AS n", id=node_id
        ).single()
    return bool(record and record["n"])


class TestSweepOwnershipGuard:
    """The sweep must refuse to run against a database we do not own."""

    def test_sweep_refuses_non_exclusive_database(self, neo4j_repository):
        with pytest.raises(SharedDatabaseSweepError):
            sweep_test_data(neo4j_repository, ephemeral=False)

    def test_sweep_runs_on_an_exclusively_owned_database(self, neo4j_repository):
        node_id = _seed_foreign_run(neo4j_repository, "OWNED")
        assert _exists(neo4j_repository, node_id)

        sweep_test_data(neo4j_repository, ephemeral=True)

        assert not _exists(neo4j_repository, node_id)

    def test_shared_staging_uri_is_not_considered_exclusive(self, monkeypatch):
        """An operator pointing at staging must not be treated as exclusive."""
        monkeypatch.delenv("AKG_NEO4J_EPHEMERAL", raising=False)
        assert neo4j_is_ephemeral(container=None) is False


class TestConcurrentRunsDoNotInterfere:
    """Model two CI runs sharing one Neo4j; neither may destroy the other's data."""

    def test_foreign_run_data_survives_our_teardown(self, neo4j_repository):
        # Run A (some other PR's CI job) has live rows in the shared database.
        run_a_node = _seed_foreign_run(neo4j_repository, "RUNA")

        # Run B (us) reaches fixture teardown while Run A is still executing.
        # Pre-fix this issued the global sweep and deleted Run A's row. It must
        # now refuse, because Run B does not exclusively own this database.
        with pytest.raises(SharedDatabaseSweepError):
            sweep_test_data(neo4j_repository, ephemeral=False)

        # Run A's data is intact: no cross-run destruction occurred.
        assert _exists(neo4j_repository, run_a_node), (
            "a concurrent run's data was destroyed by our teardown sweep"
        )

        # Clean up as an owner would.
        sweep_test_data(neo4j_repository, ephemeral=True)

    def test_unguarded_sweep_is_what_destroyed_concurrent_runs(self, neo4j_repository):
        """Pin the mechanism: the raw sweep query is indiscriminate by prefix.

        This documents *why* the guard is required rather than asserting the
        guard again -- the query itself cannot distinguish our rows from a
        concurrent run's, which is exactly why it may only run on a database we
        own outright.
        """
        run_a_node = _seed_foreign_run(neo4j_repository, "RUNA")
        run_b_node = _seed_foreign_run(neo4j_repository, "RUNB")

        with neo4j_repository.session() as session:
            session.run(TEST_DATA_SWEEP_QUERY)

        assert not _exists(neo4j_repository, run_a_node)
        assert not _exists(neo4j_repository, run_b_node)


class TestTeardownOnlyCleanup:
    """The pre-test sweep is gone; each test must still start clean."""

    _LEAKED = "TEST_LEAK_probe_marker"

    def test_a_leaves_a_row_behind(self, neo4j_repository):
        with neo4j_repository.session() as session:
            session.run("CREATE (n:Problem {id: $id})", id=self._LEAKED)
        assert _exists(neo4j_repository, self._LEAKED)

    def test_b_does_not_see_the_previous_test_row(self, neo4j_repository):
        """Teardown-only cleanup is sufficient: no pre-test sweep required."""
        assert not _exists(neo4j_repository, self._LEAKED), (
            "teardown sweep did not remove the previous test's data, so the "
            "pre-test sweep cannot be dropped"
        )


class TestDoiCleanupCoverage:
    """DOIs minted as 10.1/TEST-* were unreachable by the old predicate."""

    def test_sweep_matches_dot_one_slash_test_dois(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        with neo4j_repository.session() as session:
            session.run("CREATE (p:Paper {doi: $doi, title: 'x'})", doi=doi)

        with neo4j_repository.session() as session:
            session.run(TEST_DATA_SWEEP_QUERY)

        with neo4j_repository.session() as session:
            record = session.run(
                "MATCH (p:Paper {doi: $doi}) RETURN count(p) AS n", doi=doi
            ).single()
        assert record["n"] == 0, (
            "10.1/TEST- DOIs leak into the database forever; with 6 hex chars of "
            "entropy they eventually collide (DuplicateError in CI run 35448203443)"
        )
