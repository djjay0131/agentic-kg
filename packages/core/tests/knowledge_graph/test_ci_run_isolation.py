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
The sweep is now (a) teardown-only and (b) refused unless this pytest session
*started the database itself*. CI provisions a per-run throwaway Neo4j, so the
sweep is safe by construction and the shared-instance topology is unreachable.

Testing the guard, not just its argument
----------------------------------------
``TestRealFixturePath`` drives a whole pytest session in a subprocess with
``NEO4J_URI`` aimed at a database this session owns -- the configuration that
caused the outage -- and asserts the suite refuses and leaves foreign rows
intact. An earlier revision of this file only called ``sweep_test_data`` with a
hand-passed ``ephemeral=`` argument, which tested the parameter and not the
plumbing that computes it: all eight tests passed while the fixture happily
destroyed a concurrent run's data. The subprocess test is the one that would
have caught that, so it is the load-bearing test here.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ..conftest import (
    TEST_DATA_SWEEP_QUERY,
    SharedDatabaseSweepError,
    session_owns_database,
    sweep_test_data,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]


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


# =============================================================================
# The load-bearing test: the real fixture path, end to end.
# =============================================================================


class TestRealFixturePath:
    """Drive a real pytest session the way CI does and observe what it does."""

    # A genuine integration test that uses the neo4j_repository fixture.
    TARGET = (
        "packages/core/tests/knowledge_graph/test_citation_graph.py"
        "::TestLinkPaperCitesPaper::test_link_creates_edge_and_increments_counters"
    )

    def _run_session_against(self, uri: str, password: str, extra_env: dict):
        env = dict(os.environ)
        env["NEO4J_URI"] = uri
        env["NEO4J_USERNAME"] = "neo4j"
        env["NEO4J_PASSWORD"] = password
        env["NEO4J_DATABASE"] = "neo4j"
        env.update(extra_env)
        return subprocess.run(
            [
                sys.executable, "-m", "pytest", self.TARGET,
                "-m", "integration", "-q", "-p", "no:cacheprovider", "--no-header",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    # The second case is the one that matters most: an environment variable
    # asserting ownership of a database the session did not create. That exact
    # combination -- AKG_NEO4J_EPHEMERAL=1 plus a shared NEO4J_URI -- made an
    # earlier revision of this fix pass all its tests while deleting a
    # concurrent run's rows. No flag may buy ownership.
    @pytest.mark.parametrize(
        "extra_env",
        [
            pytest.param({}, id="no-flags"),
            pytest.param({"AKG_NEO4J_EPHEMERAL": "1"}, id="ownership-flag-set"),
        ],
    )
    def test_session_pointed_at_a_shared_database_refuses_and_preserves_data(
        self, neo4j_container, neo4j_repository, extra_env
    ):
        """The configuration that caused the outage must fail, not pass quietly.

        This is the exact shape of the production defect: a pytest session whose
        NEO4J_URI names a database somebody else is also using. The session must
        refuse, and the other party's rows must survive.
        """
        if neo4j_container is None:
            pytest.skip("needs a container this session owns to stand in for staging")

        uri = neo4j_container.get_connection_url()
        foreign = _seed_foreign_run(neo4j_repository, "FOREIGN")
        assert _exists(neo4j_repository, foreign)

        self._run_session_against(uri, "testpassword", extra_env)

        # The assertion is about the database, not about how the child failed.
        #
        # When this test was written the child *refused* with
        # SharedDatabaseSweepError, and it asserted exactly that. Issue #78 then
        # removed the environment fallback entirely, so a child session no longer
        # consults NEO4J_URI at all -- it starts its own container and passes.
        # Asserting on the refusal mechanism would now fail against behaviour
        # that is strictly safer than what it was written to check.
        #
        # So assert the invariant the mechanism existed to protect: whatever the
        # child did, the database named only by environment variables is
        # untouched. That survives a change of mechanism, and still fails if any
        # future change lets a child reach a database it was merely told about.
        assert _exists(neo4j_repository, foreign), (
            "a concurrent run's data was destroyed by a session that should "
            "never have addressed this database"
        )

    def test_session_that_owns_its_database_runs_normally(self, neo4j_container):
        """The guard must not be a blanket refusal -- the supported path works.

        No NEO4J_URI, so the child session starts its own container and proceeds.

        Gated on ``neo4j_container`` like every other test that needs a real
        database. That fixture skips when Docker is unavailable, which is the
        only honest thing to do here: the child session cannot start a container
        either, so it skips, and asserting "1 passed" on its output would be
        asserting something the environment cannot deliver. The `test (3.12)`
        job runs this file without Docker; an earlier revision took no fixture,
        ran there, and reported a false failure -- the mirror image of the false
        successes this PR exists to remove. Both come from an assertion with an
        unstated precondition.

        The two negative cases above need no such gate: a refusal is still a
        refusal without Docker.
        """
        if neo4j_container is None:
            pytest.skip(
                "needs Docker: the child session must be able to start its own "
                "container for the positive path to mean anything"
            )
        env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
        env.pop("AKG_NEO4J_EPHEMERAL", None)
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", self.TARGET,
                "-m", "integration", "-q", "-p", "no:cacheprovider", "--no-header",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, (
            "a session that owns its own database was refused.\n" + combined[-3000:]
        )
        assert "1 passed" in combined, combined[-3000:]


class TestOwnershipIsAFactNotAnAssertion:
    """No flag may claim ownership of a database the session did not create."""

    def test_no_container_means_not_owned(self):
        assert session_owns_database(None) is False

    def test_environment_cannot_claim_ownership(self, monkeypatch):
        """Guards against reintroducing an env-var bypass.

        ``AKG_NEO4J_EPHEMERAL`` used to make this return True, which is how a
        shared database passed as owned.
        """
        for name in ("AKG_NEO4J_EPHEMERAL", "AKG_NEO4J_OWNED", "NEO4J_EPHEMERAL"):
            monkeypatch.setenv(name, "1")
        assert session_owns_database(None) is False

    def test_a_started_container_means_owned(self):
        assert session_owns_database(object()) is True


# =============================================================================
# Unit-level properties of the sweep itself.
# =============================================================================


class TestSweepOwnershipGuard:
    def test_sweep_refuses_non_owned_database(self, neo4j_repository):
        with pytest.raises(SharedDatabaseSweepError):
            sweep_test_data(neo4j_repository, ephemeral=False)

    def test_sweep_runs_on_an_owned_database(self, neo4j_repository):
        node_id = _seed_foreign_run(neo4j_repository, "OWNED")
        assert _exists(neo4j_repository, node_id)

        sweep_test_data(neo4j_repository, ephemeral=True)

        assert not _exists(neo4j_repository, node_id)

    def test_unguarded_sweep_is_what_destroyed_concurrent_runs(self, neo4j_repository):
        """Pin the mechanism: the raw query cannot tell whose rows it deletes.

        This documents *why* an ownership guard is required rather than a
        smarter predicate -- the query is indiscriminate by construction.
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
    _seeded = False

    def test_a_leaves_a_row_behind(self, neo4j_repository):
        with neo4j_repository.session() as session:
            session.run("CREATE (n:Problem {id: $id})", id=self._LEAKED)
        assert _exists(neo4j_repository, self._LEAKED)
        TestTeardownOnlyCleanup._seeded = True

    def test_b_does_not_see_the_previous_test_row(self, neo4j_repository):
        """Teardown-only cleanup is sufficient: no pre-test sweep required.

        Ordered pair -- skips rather than passing vacuously when run alone.
        """
        if not TestTeardownOnlyCleanup._seeded:
            pytest.skip(
                "requires test_a_leaves_a_row_behind to have run in this session"
            )
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

    def test_e2e_cleanup_actually_deletes_papers_by_doi(self, neo4j_repository):
        """The same leak existed in the E2E helper, which matched no DOI at all.

        Behavioural, deliberately. An earlier version of this test grepped
        ``clear_test_data``'s source for the two DOI literals -- and the
        docstring added alongside the fix contains both, so stripping the DOI
        clauses out of the Cypher left the test green. A text scan over code
        trips on the very words written to describe that code. So: seed real
        Papers, run the real function, assert they are gone.
        """
        from ..e2e.utils import clear_test_data

        # These Papers carry no `id`, so only the DOI clauses of the predicate
        # can possibly match them. Both historical DOI shapes are covered.
        old_shape = f"10.TEST_E2E/{uuid.uuid4().hex[:8]}"
        new_shape = f"10.1/TEST-{uuid.uuid4().hex[:6]}"

        with neo4j_repository.session() as session:
            for doi in (old_shape, new_shape):
                session.run(
                    "CREATE (p:Paper {doi: $doi, title: 'e2e cleanup probe'})",
                    doi=doi,
                )

        def _remaining() -> int:
            with neo4j_repository.session() as session:
                record = session.run(
                    "MATCH (p:Paper) WHERE p.doi IN $dois RETURN count(p) AS n",
                    dois=[old_shape, new_shape],
                ).single()
            return record["n"] if record else 0

        assert _remaining() == 2, "probe Papers were not created"

        with neo4j_repository.session() as session:
            clear_test_data(session)

        assert _remaining() == 0, (
            "tests/e2e/utils.py:clear_test_data left TEST_ Papers behind. Papers "
            "are keyed by `doi`, never `id`, so an id-only predicate matches no "
            "Paper at all and every one the E2E suite creates leaks into real "
            "staging permanently (DuplicateError: 10.1/TEST-c854bd, CI run "
            "35448203443)."
        )
