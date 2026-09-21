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
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from ..conftest import (
    UnownedDatabaseError,
    database_is_declared,
    guard_is_installed,
)

# Evaluated when pytest *imports* this module -- that is, during collection,
# before a single fixture has run. Asserting `guard_is_installed()` from inside
# a test body proves nothing, because a session-scoped autouse fixture would
# have installed the guard by then; this constant is the only vantage point from
# which the two arrangements look different. See TestCollectionTimeIsGuarded.
GUARD_WAS_INSTALLED_AT_COLLECTION_TIME = guard_is_installed()

REPO_ROOT = Path(__file__).resolve().parents[4]
TESTS_ROOT = REPO_ROOT / "packages/core/tests"

# Entry points that reach a database. A call to any of these in a test module
# outside the allowlist is the shape that produced issue #78.
#
# ``initialize_schema`` and ``get_repository`` are here because of review
# finding H1: the original scanner matched only ``Neo4jRepository``, so a module
# body calling ``initialize_schema(force=True)`` -- which is literally half of
# the audited exploit -- was invisible to it. ``GraphDatabase.driver`` is here
# because of H2: a raw driver never touches ``Neo4jRepository``, so the
# constructor seam cannot see it and only this scanner can.
DATABASE_ENTRY_POINTS = {
    "Neo4jRepository",
    "initialize_schema",
    "get_repository",
}

# The only places allowed to reach a database directly. Everything else must
# take the `neo4j_repository` fixture.
#
# Anchored to literal paths relative to ``packages/core/tests``. It used to be a
# set matched against path *parts*, which meant every file named ``conftest.py``
# anywhere in the tree was exempt, as was every file under any directory named
# ``e2e`` -- so a new ``tests/integration/conftest.py`` could reintroduce the
# route invisibly (review finding M4a). Each entry below is a specific file with
# a specific reason.
CONSTRUCTION_ALLOWLIST = {
    # Builds the owned repository the `neo4j_repository` fixture hands out, and
    # installs the ownership guard itself.
    "conftest.py",
    # The e2e suite deliberately targets the deployed staging environment and
    # runs only under explicit commands; its conftest declares that target in
    # code before opening a driver against it.
    "e2e/conftest.py",
    "e2e/test_kg_population.py",
    # Starts its own throwaway container for the canonical-adapter suite and
    # declares it before opening a raw driver against it (issue #78 / H2).
    "migration/neo4j/conftest.py",
    # `empty_graph_driver` (#84) starts a *second* throwaway container -- Neo4j
    # Community has one user database, so "empty" cannot be faked inside the
    # populated one -- and opens a raw driver against the URL that container
    # just handed back. It reads no environment variable and its password is a
    # literal, so no target it addresses can be named from outside the process.
    # The scanner found this file when the base branch merged; the entry is a
    # judgement about that fixture, not a blanket exemption for the directory.
    "migration/compat/conftest.py",
    # This file. It reaches these entry points only inside `pytest.raises` to
    # prove the guard refuses them -- see TestOwnershipSeam. The scanner caught
    # it on first run, which is the evidence that it works.
    "knowledge_graph/test_database_ownership_seam.py",
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
# Collection time is inside the blast radius (review finding H1)
# =============================================================================


COLLECTION_TIME_OFFENDER = '\n'.join([
    '"""A module whose *body* runs DDL -- the shape that evaded the fixture guard.',
    '',
    'Written by test_collection_time_ddl_cannot_reach_an_undeclared_database and',
    'deleted again in its finally block. pytest imports this during collection,',
    'before any session-scoped fixture runs.',
    '"""',
    '',
    'from agentic_kg.config import reset_config',
    'from agentic_kg.knowledge_graph.schema import initialize_schema',
    '',
    'reset_config()',
    'initialize_schema(force=True)',
    '',
    '',
    'def test_placeholder():',
    '    assert True',
    '',
])


class TestCollectionTimeIsGuarded:
    """A module body must not be able to reach an undeclared database.

    pytest imports test modules during *collection*, which happens before
    session-scoped autouse fixtures run. While the guard was installed from a
    fixture, a module-level ``initialize_schema(force=True)`` performed the full
    constraint DDL against whatever ``NEO4J_URI`` named and pytest printed
    "1 passed" over the top of it (review finding H1). The guard is now
    installed at conftest import time; these are the proofs.
    """

    def test_the_guard_was_installed_before_this_module_was_imported(self):
        """The seam is a conftest-import side effect, not a fixture.

        ``GUARD_WAS_INSTALLED_AT_COLLECTION_TIME`` is read at module scope, so
        it records the state of the world at the moment pytest imported this
        file -- during collection. Asserting ``guard_is_installed()`` from
        inside a test body instead would pass under either arrangement, since a
        session-scoped autouse fixture runs before the first test; that version
        of this test was written, found vacuous under mutation, and replaced.
        """
        assert GUARD_WAS_INSTALLED_AT_COLLECTION_TIME, (
            "the ownership guard was not installed when pytest imported this "
            "module. If install_ownership_guard() moved back behind a fixture, "
            "every module body in this tree is unguarded during collection "
            "again -- see review finding H1."
        )

    def test_collection_time_ddl_cannot_reach_an_undeclared_database(
        self, neo4j_container, neo4j_repository
    ):
        """Import-time DDL against an env-named database must be refused.

        Measures constraints and indexes rather than nodes: the exploit route is
        ``initialize_schema(force=True)``, and schema is not made of nodes. On
        the unguarded build this took the stand-in database from 0 to 11
        constraints while pytest reported "1 passed".
        """
        if neo4j_container is None:
            pytest.skip("needs a container to stand in for the developer's database")

        def schema_counts():
            with neo4j_repository.session() as session:
                constraints = session.run(
                    "SHOW CONSTRAINTS YIELD name RETURN count(name) AS n"
                ).single()["n"]
                indexes = session.run(
                    "SHOW INDEXES YIELD name RETURN count(name) AS n"
                ).single()["n"]
            return {"constraints": constraints, "indexes": indexes}

        # Start from zero constraints so the measurement can actually move --
        # otherwise ``initialize_schema(force=True)`` would recreate what is
        # already there and the count would not change. Subsequent tests
        # re-initialise through the fixture.
        with neo4j_repository.session() as session:
            for record in list(session.run("SHOW CONSTRAINTS YIELD name")):
                session.run(f"DROP CONSTRAINT {record['name']} IF EXISTS")
            for record in list(session.run("SHOW INDEXES YIELD name, type")):
                if record["type"] == "LOOKUP":
                    continue
                session.run(f"DROP INDEX {record['name']} IF EXISTS")

        before = schema_counts()

        scratch = (
            TESTS_ROOT
            / "knowledge_graph"
            / f"test_scratch_collection_time_{uuid.uuid4().hex[:10]}.py"
        )
        scratch.write_text(COLLECTION_TIME_OFFENDER, encoding="utf-8")
        try:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "NEO4J_URI": neo4j_container.get_connection_url(),
                "NEO4J_USER": "neo4j",
                "NEO4J_USERNAME": "neo4j",
                "NEO4J_PASSWORD": "testpassword",
            }
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest",
                    str(scratch.relative_to(REPO_ROOT)),
                    "-q", "-p", "no:cacheprovider", "--no-header",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
        finally:
            scratch.unlink(missing_ok=True)

        combined = result.stdout + result.stderr
        after = schema_counts()

        assert after == before, (
            "a module body ran DDL against a database named only by environment "
            f"variables, during collection: {before} -> {after}"
        )
        assert result.returncode != 0, (
            "the offending module was collected without complaint:\n"
            + combined[-3000:]
        )
        assert "UnownedDatabaseError" in combined, (
            "collection failed, but not because the ownership guard refused it "
            "-- that would make this test pass for the wrong reason.\n"
            + combined[-3000:]
        )


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
    def _entry_points_in(source: str, filename: str = "<scan>"):
        """Every database entry point called in ``source``, as (lineno, name).

        One function, used by the tree scan and by the self-tests below, so the
        thing proven falsifiable is the thing that runs over the tree.
        """
        found = []
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in DATABASE_ENTRY_POINTS:
                    found.append((node.lineno, func.id))
            elif isinstance(func, ast.Attribute):
                if func.attr in DATABASE_ENTRY_POINTS:
                    found.append((node.lineno, func.attr))
                elif (
                    func.attr == "driver"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "GraphDatabase"
                ):
                    found.append((node.lineno, "GraphDatabase.driver"))
        return sorted(found)

    @classmethod
    def _offenders(cls):
        offenders = []
        for path in sorted(TESTS_ROOT.rglob("*.py")):
            relative = path.relative_to(TESTS_ROOT)
            if relative.as_posix() in CONSTRUCTION_ALLOWLIST:
                continue
            for lineno, name in cls._entry_points_in(
                path.read_text(encoding="utf-8"), filename=str(path)
            ):
                offenders.append(f"{relative}:{lineno} ({name})")
        return offenders

    def test_no_test_file_constructs_a_repository_directly(self):
        offenders = self._offenders()
        assert offenders == [], (
            "These test files reach a database directly instead of "
            "taking the ownership-guarded `neo4j_repository` fixture, which is "
            "how issue #78 happened:\n  "
            + "\n  ".join(offenders)
            + "\n\nTake the fixture. If you genuinely need a repository against "
            "a deployed environment, that belongs in tests/e2e/, whose conftest "
            "declares its target deliberately."
        )

    @pytest.mark.parametrize(
        "source, expected",
        [
            pytest.param(
                "from agentic_kg.knowledge_graph.repository import Neo4jRepository\n"
                "def test_something():\n"
                "    repo = Neo4jRepository()\n",
                [(3, "Neo4jRepository")],
                id="bare-repository",
            ),
            pytest.param(
                "from agentic_kg.knowledge_graph.schema import initialize_schema\n"
                "initialize_schema(force=True)\n",
                [(2, "initialize_schema")],
                id="module-level-initialize_schema",
            ),
            pytest.param(
                "from agentic_kg.knowledge_graph.repository import get_repository\n"
                "def test_something():\n"
                "    repo = get_repository()\n",
                [(3, "get_repository")],
                id="get_repository",
            ),
            pytest.param(
                "from neo4j import GraphDatabase\n"
                "def test_something():\n"
                "    d = GraphDatabase.driver('bolt://prod:7687', auth=('a', 'b'))\n",
                [(3, "GraphDatabase.driver")],
                id="raw-driver",
            ),
            pytest.param(
                "import agentic_kg.knowledge_graph.schema as schema\n"
                "schema.initialize_schema(force=True)\n",
                [(2, "initialize_schema")],
                id="attribute-call",
            ),
        ],
    )
    def test_the_scanner_can_actually_see_a_violation(self, source, expected):
        """The scanner must be able to fail -- proven on synthetic offenders.

        Without this, `test_no_test_file_constructs_a_repository_directly`
        passing would be indistinguishable from the scanner being broken. Each
        case is a shape that actually evaded an earlier revision: the original
        scanner matched ``Neo4jRepository`` only, so `initialize_schema` -- half
        of the audited exploit, and the half that runs DDL -- and a raw
        ``GraphDatabase.driver`` both walked straight past it (review H1, H2).
        """
        assert self._entry_points_in(source) == expected

    def test_a_docstring_mentioning_the_call_is_not_a_violation(self):
        """The AST walk must not trip on prose, which is why it is not a grep."""
        innocent = (
            '"""Never call Neo4jRepository() or initialize_schema() directly,\n'
            'and never GraphDatabase.driver(...) either."""\n'
            "def test_something():\n"
            "    assert True\n"
        )
        assert self._entry_points_in(innocent) == []

    def test_the_allowlist_only_exempts_files_that_exist(self):
        """A stale allowlist entry silently widens the exemption it names.

        Also pins the anchoring fix (review M4a): entries are paths relative to
        ``packages/core/tests``, so `conftest.py` exempts exactly one file
        rather than every conftest in the tree.
        """
        missing = [
            entry for entry in CONSTRUCTION_ALLOWLIST if not (TESTS_ROOT / entry).is_file()
        ]
        assert missing == [], f"allowlist names files that do not exist: {missing}"

    def test_the_allowlist_does_not_exempt_a_sibling_of_the_same_name(self, tmp_path):
        """`conftest.py` in the allowlist must not exempt `agents/conftest.py`.

        The part-matching version did exactly that, and would have exempted a
        new `tests/integration/conftest.py` too.
        """
        assert "conftest.py" in CONSTRUCTION_ALLOWLIST
        for sibling in ("agents/conftest.py", "integration/conftest.py", "e2e/test_evil.py"):
            assert sibling not in CONSTRUCTION_ALLOWLIST


# =============================================================================
# No conftest may select a database from the environment (review finding H2)
# =============================================================================


# conftest.py files permitted to read a NEO4J-named environment variable.
# Exactly one, and it is the suite whose entire purpose is a deployed target.
ENVIRONMENT_SELECTION_ALLOWLIST = {
    # Reads STAGING_NEO4J_URI / STAGING_NEO4J_PASSWORD and declares that target
    # in code. It runs only under explicit commands and skips without the
    # password, so `make test` never reaches it.
    "e2e/conftest.py",
}


class TestNoConftestSelectsADatabaseFromTheEnvironment:
    """Close the route review finding H2 found still open.

    ``packages/core/tests/migration/neo4j/conftest.py`` honoured
    ``NEO4J_CANONICAL_URI``: setting it short-circuited all three skip
    conditions, opened a raw ``GraphDatabase.driver`` -- which never passes the
    constructor seam -- and ``make_canonical_store()`` then ran
    ``ensure_schema()`` against it. Verified with the #78 guard live: that route
    wrote a node *and* created a constraint on a stand-in database. Same defect
    class as #78, same test tree, collected by ``make test-core``.

    The route is removed. This is what keeps it removed, and what keeps the
    claim in ``tests/conftest.py`` -- that no environment variable under
    ``packages/core/tests`` declares a database -- honest.
    """

    @staticmethod
    def _environment_keys_read(source: str, filename: str = "<scan>"):
        """Every environment variable *read* in ``source``, as (lineno, key).

        Reads only: ``monkeypatch.setenv("NEO4J_URI", ...)`` is a test setting
        up its own process, not a conftest choosing a database, and must not be
        reported.
        """
        keys = []
        tree = ast.parse(source, filename=filename)

        def _is_environ(node) -> bool:
            return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
                isinstance(node, ast.Name) and node.id == "environ"
            )

        def _record(node, arg):
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.append((node.lineno, arg.value))

        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_environ(node.value):
                _record(node, node.slice)
            elif isinstance(node, ast.Call):
                func = node.func
                is_read = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and _is_environ(func.value)
                ) or (
                    isinstance(func, ast.Attribute) and func.attr == "getenv"
                ) or (
                    isinstance(func, ast.Name) and func.id == "getenv"
                )
                if is_read and node.args:
                    _record(node, node.args[0])
        return sorted(keys)

    @classmethod
    def _offenders(cls):
        offenders = []
        for path in sorted(TESTS_ROOT.rglob("conftest.py")):
            relative = path.relative_to(TESTS_ROOT).as_posix()
            if relative in ENVIRONMENT_SELECTION_ALLOWLIST:
                continue
            for lineno, key in cls._environment_keys_read(
                path.read_text(encoding="utf-8"), filename=str(path)
            ):
                if "NEO4J" in key.upper():
                    offenders.append(f"{relative}:{lineno} reads {key}")
        return offenders

    def test_no_conftest_reads_a_neo4j_variable(self):
        assert self._offenders() == [], (
            "These conftest files choose a database from the environment, which "
            "is the polarity inversion issue #78 was filed for -- credentials "
            "in the environment are not permission to write:\n  "
            + "\n  ".join(self._offenders())
            + "\n\nStart a container and declare it instead. If a suite really "
            "must address a deployed environment, it goes in tests/e2e/."
        )

    @pytest.mark.parametrize(
        "source, expected",
        [
            pytest.param(
                'import os\nuri = os.environ.get("NEO4J_CANONICAL_URI")\n',
                [(2, "NEO4J_CANONICAL_URI")],
                id="environ-get",
            ),
            pytest.param(
                'import os\nuri = os.getenv("NEO4J_URI")\n',
                [(2, "NEO4J_URI")],
                id="os-getenv",
            ),
            pytest.param(
                'import os\nuri = os.environ["NEO4J_URI"]\n',
                [(2, "NEO4J_URI")],
                id="environ-subscript",
            ),
            pytest.param(
                'from os import environ\nuri = environ.get("NEO4J_URI")\n',
                [(2, "NEO4J_URI")],
                id="bare-environ",
            ),
        ],
    )
    def test_the_scan_can_actually_see_a_violation(self, source, expected):
        """Each shape is one the removed route used or could have used."""
        assert self._environment_keys_read(source) == expected

    def test_setting_a_variable_is_not_reading_one(self):
        """A test configuring its own subprocess is not a conftest choosing.

        Without this, ``tests/conftest.py``'s own ``monkeypatch.setenv`` calls
        would be reported and the check would have to be weakened to pass --
        which is how a guard becomes decorative.
        """
        source = (
            'def fixture(monkeypatch):\n'
            '    monkeypatch.setenv("NEO4J_URI", "bolt://owned:7687")\n'
            '    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)\n'
        )
        assert self._environment_keys_read(source) == []

    def test_prose_naming_the_variable_is_not_a_violation(self):
        """AST, not grep -- this module's own docstrings name the variable."""
        source = '"""Never read NEO4J_CANONICAL_URI or os.environ[\'NEO4J_URI\']."""\n'
        assert self._environment_keys_read(source) == []


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

    def test_clear_requires_the_operator_to_name_the_database(self):
        """``--clear`` must not be able to derive its own expected value.

        ``expect_database`` existed to make a misconfigured ``NEO4J_DATABASE``
        abort instead of wiping the database it named -- but the script passed
        ``repo._config.database``, so both sides of the comparison came from
        the connection being cleared and the check could never fail (review
        finding M2). The name now has to be typed. Asserted through the real
        CLI rather than by reading the source, and it exits before any
        connection is opened.
        """
        script = REPO_ROOT / "scripts/load_sample_problems.py"
        assert script.is_file(), "the script this test is about has moved"
        result = subprocess.run(
            [sys.executable, str(script), "--clear"],
            cwd=REPO_ROOT,
            env={k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")},
            capture_output=True,
            text=True,
            timeout=120,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 2, combined[-2000:]
        assert "--clear requires --database" in combined, combined[-2000:]

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
