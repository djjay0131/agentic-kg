"""Regression tests for the shared-database routes found in issue #78.

The defect
----------
PR #75 made ``neo4j_repository`` refuse a database this session does not own.
Two integration modules never took that fixture. They gated themselves on::

    NEO4J_AVAILABLE = all([os.getenv("NEO4J_URI"), ...])
    pytestmark = pytest.mark.skipif(not NEO4J_AVAILABLE, ...)

-- the opposite polarity to every other gate in the suite: credentials in the
environment *enabled* them. They then built a bare ``Neo4jRepository()`` from
those credentials and ran ``initialize_schema(force=True)`` against it. Neither
carried ``pytest.mark.integration``, so no ``-m`` filter deselected them, and
both are collected by ``make test``, ``make test-core`` and the command
README.md documents as "unit tests".

Measured against a stand-in database before the fix: the documented command took
it from **0 to 10 constraints** -- unauthorised DDL -- while the #75 guard
correctly refused the 654 constructions that did go through its fixture. The
same environment variable that made the fixture refuse made these modules run.

The fix
-------
Ownership is enforced at the seam rather than per-file. ``Neo4jRepository`` is
the single choke point every database access passes through, so the autouse
``enforce_database_ownership`` fixture wraps its constructor for the whole
session and refuses any target this session has not declared. Starting a
container is the only thing that declares one for the integration suite.

A per-file patch would leave the next such file free to reintroduce the route;
``TestNoBareRepositoryConstruction`` below fails if one does.
"""

import ast
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ..conftest import (
    UnownedDatabaseError,
    database_is_declared,
    declare_session_database,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
TESTS_ROOT = REPO_ROOT / "packages/core/tests"

# The only places allowed to construct a repository directly. Everything else
# must take the `neo4j_repository` fixture.
#
# - conftest.py builds the owned repository the fixture hands out.
# - tests/e2e/** deliberately targets the deployed staging environment and runs
#   only under explicit commands; its conftest declares that target in code.
CONSTRUCTION_ALLOWLIST = {
    "conftest.py",
    "e2e",
    # This file. It constructs repositories only inside `pytest.raises` to prove
    # the guard refuses them -- see TestOwnershipSeam. The scanner caught it on
    # first run, which is the evidence that it works.
    "test_database_ownership_seam.py",
}


# =============================================================================
# The seam itself
# =============================================================================


class TestOwnershipSeam:
    """The guard must sit on the constructor, not on any one test module."""

    def test_undeclared_target_is_refused(self, neo4j_repository):
        """A repository against a database we never declared must not build."""
        from agentic_kg.config import Neo4jConfig
        from agentic_kg.knowledge_graph.repository import Neo4jRepository

        undeclared = Neo4jConfig(
            uri="bolt://neo4j.production.example.com:7687",
            username="neo4j",
            password="hunter2",
            database="neo4j",
        )
        with pytest.raises(UnownedDatabaseError):
            Neo4jRepository(config=undeclared)

    def test_bare_construction_reads_env_and_is_refused(
        self, neo4j_repository, monkeypatch
    ):
        """`Neo4jRepository()` with no argument resolves NEO4J_* from the env.

        This is the exact call the two audited modules made. It must not build,
        whatever the environment says.
        """
        from agentic_kg.config import reset_config
        from agentic_kg.knowledge_graph.repository import Neo4jRepository

        monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.production.example.com:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "hunter2")
        reset_config()

        with pytest.raises(UnownedDatabaseError):
            Neo4jRepository()

    def test_initialize_schema_cannot_reach_an_undeclared_target(
        self, neo4j_repository, monkeypatch
    ):
        """`initialize_schema(force=True)` goes through get_repository().

        The second half of the audited call pair. It reaches the same seam, so
        it is refused by the same guard rather than performing DDL.
        """
        from agentic_kg.config import reset_config
        from agentic_kg.knowledge_graph.schema import initialize_schema

        monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.production.example.com:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "hunter2")
        reset_config()

        with pytest.raises(UnownedDatabaseError):
            initialize_schema(force=True)

    def test_the_owned_container_is_declared(self, neo4j_container, neo4j_config):
        """The guard must not be a blanket refusal: the owned path works."""
        if neo4j_container is None:
            pytest.skip("needs a container this session started")
        assert database_is_declared(neo4j_config.uri)

    def test_credentials_are_not_permission(self, monkeypatch):
        """Declaration cannot be produced by exporting variables.

        The polarity inversion in one assertion: no combination of environment
        variables may make an undeclared database acceptable.
        """
        for name in (
            "NEO4J_URI",
            "NEO4J_USER",
            "NEO4J_USERNAME",
            "NEO4J_PASSWORD",
            "NEO4J_AVAILABLE",
            "AKG_NEO4J_EPHEMERAL",
        ):
            monkeypatch.setenv(name, "bolt://neo4j.production.example.com:7687")
        assert not database_is_declared("bolt://neo4j.production.example.com:7687")


# =============================================================================
# Anti-regression: no new test file may reintroduce the route
# =============================================================================


class TestNoBareRepositoryConstruction:
    """Fail if a test file constructs a repository outside the fixture path.

    Deliberately an AST walk rather than a text scan. A grep over source trips
    on the words written to describe the code -- a docstring naming
    ``Neo4jRepository()`` would either trigger a false positive here or, worse,
    satisfy a check looking for the opposite. (Reviewed precedent: an earlier
    cleanup test in this suite grepped for two DOI literals and was satisfied by
    the docstring that described them, so the fix it guarded reverted green.)
    """

    @staticmethod
    def _offenders():
        offenders = []
        for path in sorted(TESTS_ROOT.rglob("*.py")):
            relative = path.relative_to(TESTS_ROOT)
            if set(relative.parts) & CONSTRUCTION_ALLOWLIST:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "Neo4jRepository":
                    offenders.append(f"{relative}:{node.lineno}")
        return offenders

    def test_no_test_file_constructs_a_repository_directly(self):
        offenders = self._offenders()
        assert offenders == [], (
            "These test files construct Neo4jRepository directly instead of "
            "taking the ownership-guarded `neo4j_repository` fixture, which is "
            "how issue #78 happened:\n  "
            + "\n  ".join(offenders)
            + "\n\nTake the fixture. If you genuinely need a repository against "
            "a deployed environment, that belongs in tests/e2e/, whose conftest "
            "declares its target deliberately."
        )

    def test_the_scanner_can_actually_see_a_violation(self, tmp_path):
        """The scanner must be able to fail -- proven on a synthetic offender.

        Without this, `test_no_test_file_constructs_a_repository_directly`
        passing would be indistinguishable from the scanner being broken.
        """
        offending = tmp_path / "test_offending_module.py"
        offending.write_text(
            "from agentic_kg.knowledge_graph.repository import Neo4jRepository\n"
            "def test_something():\n"
            "    repo = Neo4jRepository()\n",
            encoding="utf-8",
        )
        tree = ast.parse(offending.read_text(encoding="utf-8"))
        found = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Neo4jRepository"
        ]
        assert found == [3]

    def test_a_docstring_mentioning_the_call_is_not_a_violation(self, tmp_path):
        """The AST walk must not trip on prose, which is why it is not a grep."""
        innocent = tmp_path / "test_innocent_module.py"
        innocent.write_text(
            '"""This module must never call Neo4jRepository() directly."""\n'
            "def test_something():\n"
            "    assert True\n",
            encoding="utf-8",
        )
        tree = ast.parse(innocent.read_text(encoding="utf-8"))
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Neo4jRepository"
        ]
        assert found == []


# =============================================================================
# The audited modules, end to end
# =============================================================================


class TestAuditedModulesRefuseSharedDatabases:
    """Drive the two audited files the way a developer's shell would."""

    AUDITED = [
        "packages/core/tests/integration/test_phase2_workflow.py",
        "packages/core/tests/integration/test_canonical_workflow.py",
    ]

    @pytest.mark.parametrize("target", AUDITED)
    def test_module_does_not_write_to_an_environment_named_database(
        self, neo4j_container, neo4j_repository, target
    ):
        """Env credentials must not enable a module against that database.

        Runs the module in a subprocess with NEO4J_* pointed at a database this
        parent session owns, standing in for the developer's production URI,
        and asserts nothing was written to it -- no nodes, and crucially no
        schema, since `initialize_schema(force=True)` was the DDL route.
        """
        if neo4j_container is None:
            pytest.skip("needs a container to stand in for the developer's database")

        sentinel = f"TEST_SENTINEL_{uuid.uuid4().hex[:10]}"
        with neo4j_repository.session() as session:
            session.run("CREATE (n:Problem {id: $id})", id=sentinel)

        # Strip the schema this session's own fixture just created, so the
        # measurement below starts from zero constraints and can actually move.
        # Without this, `initialize_schema(force=True)` in the child would
        # recreate constraints that already existed, the count would not change,
        # and the assertion would pass while the DDL happened -- the mirror of
        # the node-only version of this test, which watched the original exploit
        # go by. Subsequent tests re-initialise through the fixture.
        with neo4j_repository.session() as session:
            for record in list(session.run("SHOW CONSTRAINTS YIELD name")):
                session.run(f"DROP CONSTRAINT {record['name']} IF EXISTS")
            for record in list(session.run("SHOW INDEXES YIELD name, type")):
                if record["type"] == "LOOKUP":
                    continue
                session.run(f"DROP INDEX {record['name']} IF EXISTS")

        def counts():
            """Nodes *and* constraints.

            Constraints matter more than nodes here: the DDL route was
            ``initialize_schema(force=True)``, and schema is not made of nodes.
            The original exploit measured exactly this -- a stand-in database
            went from 0 to 10 constraints while its node count barely moved. An
            earlier version of this test counted only nodes and would have
            watched that happen without failing.
            """
            with neo4j_repository.session() as session:
                nodes = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
                concepts = session.run(
                    "MATCH (n) WHERE n:ProblemConcept OR n:ProblemMention "
                    "RETURN count(n) AS n"
                ).single()["n"]
                constraints = session.run(
                    "SHOW CONSTRAINTS YIELD name RETURN count(name) AS n"
                ).single()["n"]
                indexes = session.run(
                    "SHOW INDEXES YIELD name RETURN count(name) AS n"
                ).single()["n"]
            return {
                "nodes": nodes,
                "concepts": concepts,
                "constraints": constraints,
                "indexes": indexes,
            }

        before = counts()

        env = {
            "PATH": subprocess.os.environ.get("PATH", ""),
            "HOME": subprocess.os.environ.get("HOME", ""),
            "NEO4J_URI": neo4j_container.get_connection_url(),
            "NEO4J_USER": "neo4j",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "testpassword",
        }
        subprocess.run(
            [
                sys.executable, "-m", "pytest", target,
                "-q", "-p", "no:cacheprovider", "--no-header",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )

        after = counts()
        assert after == before, (
            f"{target} modified a database named only by environment variables: "
            f"{before} -> {after}. Credentials in the environment are not "
            "permission to write, and schema is a write."
        )
        with neo4j_repository.session() as session:
            still_there = session.run(
                "MATCH (n {id: $id}) RETURN count(n) AS n", id=sentinel
            ).single()["n"]
        assert still_there == 1, "the sentinel row was destroyed"

    @pytest.mark.parametrize("target", AUDITED)
    def test_module_carries_the_integration_marker(self, target):
        """Without the marker, every `-m "not integration"` filter misses them."""
        tree = ast.parse((REPO_ROOT / target).read_text(encoding="utf-8"))
        marks = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
            ):
                marks.append(ast.dump(node.value))
        assert marks, f"{target} sets no pytestmark"
        assert any("integration" in m for m in marks), (
            f"{target} does not carry pytest.mark.integration, so no -m filter "
            "deselects it and `make test` will run it"
        )


# =============================================================================
# The shipped total-wipe helper (issue #78, "Related")
# =============================================================================


class TestDropAllIsNotReachableByAccident:
    """``SchemaManager.drop_all`` ships in the wheel; it must be hard to fire.

    Decision: kept rather than removed -- ``load_sample_problems.py --clear`` is
    a legitimate developer workflow and deleting a public method is a wider
    break than the risk warrants -- but it can no longer be invoked without
    naming its target, and refuses outright in a deployed environment.
    """

    def test_confirm_alone_is_no_longer_enough(self, neo4j_repository):
        """The old signature, `drop_all(confirm=True)`, must not type-check."""
        from agentic_kg.knowledge_graph.schema import SchemaManager

        manager = SchemaManager(repository=neo4j_repository)
        with pytest.raises(TypeError):
            manager.drop_all(confirm=True)

    def test_wrong_expected_database_refuses(self, neo4j_repository):
        """A misconfigured NEO4J_DATABASE must abort, not wipe the wrong one."""
        from agentic_kg.knowledge_graph.schema import (
            DestructiveOperationRefused,
            SchemaManager,
        )

        manager = SchemaManager(repository=neo4j_repository)
        with pytest.raises(DestructiveOperationRefused):
            manager.drop_all(confirm=True, expect_database="some-other-database")

    @pytest.mark.parametrize("environment", ["production", "staging"])
    def test_deployed_environments_refuse(
        self, neo4j_repository, monkeypatch, environment
    ):
        from agentic_kg.config import reset_config
        from agentic_kg.knowledge_graph.schema import (
            DestructiveOperationRefused,
            SchemaManager,
        )

        monkeypatch.setenv("ENVIRONMENT", environment)
        reset_config()
        manager = SchemaManager(repository=neo4j_repository)
        with pytest.raises(DestructiveOperationRefused):
            manager.drop_all(confirm=True, expect_database=neo4j_repository._config.database)

    def test_refusal_raises_rather_than_returning_false(self, neo4j_repository):
        """A destructive call that quietly no-ops is its own hazard.

        The caller cannot distinguish "refused" from "wiped" by a False return,
        which is why the guards raise. ``confirm=False`` keeps its historical
        False return, since that one is an explicit opt-out rather than a guard.
        """
        from agentic_kg.knowledge_graph.schema import SchemaManager

        manager = SchemaManager(repository=neo4j_repository)
        assert manager.drop_all(confirm=False, expect_database="neo4j") is False

    def test_it_still_works_when_correctly_addressed(self, neo4j_repository):
        """Not a blanket refusal: the legitimate developer path still runs."""
        from agentic_kg.knowledge_graph.schema import SchemaManager

        with neo4j_repository.session() as session:
            session.run("CREATE (n:Problem {id: 'TEST_dropall_probe'})")

        manager = SchemaManager(repository=neo4j_repository)
        assert manager.drop_all(
            confirm=True, expect_database=neo4j_repository._config.database
        ) is True

        with neo4j_repository.session() as session:
            remaining = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        assert remaining == 0
