"""The shared session's schema must be present, not merely "version current".

``neo4j_repository`` is a function-scoped fixture over one session-scoped
container, so every integration test shares one database. ``SchemaManager
.initialize(force=False)`` returns early once the ``SchemaVersion`` marker
reaches ``SCHEMA_VERSION`` -- even if the constraints and indexes that marker
stands for have been dropped. Two isolation tests in
``test_database_ownership_seam.py`` drop the whole schema on purpose to measure
that a refused collection-time DDL call changes nothing, and used to restore
nothing. The suite still passed only because a later test (``drop_all``) wiped
every node, including ``SchemaVersion``, which made the next
``initialize(force=False)`` rebuild from scratch. That is an accident of
collection order; under random ordering it produces the order-dependent
failures in issue #94, and it is the "tests mutate shared session state" defect
in issue #91.

These tests pin the correction without a database: ``ensure_schema`` must decide
on the schema *objects* (checked through ``SHOW``), and must force a rebuild
whenever any of them is missing -- regardless of what the version marker says.
"""

from __future__ import annotations

from ..conftest import (
    ensure_schema,
    expected_schema_names,
    missing_schema_names,
    present_schema_names,
)


class _FakeResult:
    def __init__(self, names: set[str]) -> None:
        self._names = names

    def __iter__(self):
        return ({"name": name} for name in self._names)


class _FakeSession:
    def __init__(self, names: set[str]) -> None:
        self._names = names

    def run(self, query: str) -> _FakeResult:
        assert "SHOW CONSTRAINTS" in query or "SHOW INDEXES" in query, query
        return _FakeResult(self._names)

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeRepo:
    """Minimal stand-in that answers the two ``SHOW`` queries with one set."""

    def __init__(self, names: set[str]) -> None:
        self._names = names

    def session(self) -> _FakeSession:
        return _FakeSession(self._names)


def test_expected_schema_names_covers_the_objects_the_guard_cares_about() -> None:
    names = expected_schema_names()
    # A constraint (dedup/uniqueness), a plain index and a vector index: the
    # three kinds the drop-schema tests remove and later tests depend on.
    assert "topic_id_unique" in names
    assert "topic_name_idx" in names
    assert "topic_embedding_idx" in names


def test_present_schema_names_unions_constraints_and_indexes() -> None:
    repo = _FakeRepo({"a", "b", "c"})
    assert present_schema_names(repo) == {"a", "b", "c"}


def test_missing_schema_names_is_empty_when_everything_is_present() -> None:
    assert missing_schema_names(set(expected_schema_names())) == set()


def test_missing_schema_names_names_the_dropped_objects() -> None:
    present = set(expected_schema_names()) - {"topic_id_unique", "model_embedding_idx"}
    assert missing_schema_names(present) == {"topic_id_unique", "model_embedding_idx"}


def test_ensure_schema_forces_a_rebuild_when_an_object_is_missing(monkeypatch) -> None:
    """The trap: version-current is not schema-present.

    If ``ensure_schema`` trusted the version marker it would call
    ``initialize(force=False)`` here and the dropped constraint would stay
    dropped. It must force.
    """
    import agentic_kg.knowledge_graph.schema as schema_module

    calls: list[bool] = []

    class _RecordingSchemaManager:
        def __init__(self, repository: object = None) -> None:
            pass

        def initialize(self, force: bool = False) -> bool:
            calls.append(force)
            return force

    monkeypatch.setattr(schema_module, "SchemaManager", _RecordingSchemaManager)

    dropped = set(expected_schema_names()) - {"topic_id_unique"}
    missing = ensure_schema(_FakeRepo(dropped))

    assert missing == {"topic_id_unique"}
    assert calls == [True]


def test_ensure_schema_takes_the_cheap_path_when_nothing_is_missing(
    monkeypatch,
) -> None:
    import agentic_kg.knowledge_graph.schema as schema_module

    calls: list[bool] = []

    class _RecordingSchemaManager:
        def __init__(self, repository: object = None) -> None:
            pass

        def initialize(self, force: bool = False) -> bool:
            calls.append(force)
            return force

    monkeypatch.setattr(schema_module, "SchemaManager", _RecordingSchemaManager)

    missing = ensure_schema(_FakeRepo(set(expected_schema_names())))

    assert missing == set()
    # force=False still re-sets the SchemaVersion marker if a node wipe removed
    # it, without paying for a full DDL pass on every healthy test.
    assert calls == [False]
