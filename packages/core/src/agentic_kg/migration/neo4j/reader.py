"""The canonical **read** surface — structurally not a write surface.

ADR-0010 and governance-delta principle 3: applications never hold a
`GraphMutationStore`; the executor alone mutates canonical state. Mapping spec
§2 rule 1 and AC-1 make that an assertion rather than a convention.

Handing out :class:`~agentic_kg.migration.neo4j.store.Neo4jCanonicalGraphStore`
and asking callers not to call ``apply`` would be a convention. This class is
the structural version:

* it defines no ``apply`` and no writer primitive, so
  ``isinstance(reader, GraphMutationStore)`` is ``False`` — `GraphMutationStore`
  is ``@runtime_checkable``, so that check is real, not decorative;
* it holds a ``neo4j.Driver`` and two strings, and **no reference to a store**,
  so there is no attribute walk that reaches a write surface either;
* it is not a `LedgerReader` (AC-3): canonical reads and ledger reads are never
  the same access path (KGIS ADR-0011), and the candidate ledger is a different
  adapter entirely.

It *is* a `GraphReader` and a `TemporalGraphReader`, with exactly the same
visibility rules as the store — both go through the same module-level helpers in
``store.py``, so a read here can never disagree with a read there.

A driver can of course run arbitrary Cypher, and nothing in Python can prevent
that. What this class removes is the *contract-shaped* write path: no object
handed to application code satisfies the protocol the executor is defined
against, so "did an application write canonically?" is answerable by an
`isinstance` check in a test instead of by reviewing every call site.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from neo4j import Driver

from agentic_kg.migration.neo4j._contracts import (
    Assertion,
    CanonicalEntity,
    EntityRef,
    GraphReadOptions,
)
from agentic_kg.migration.neo4j.store import (
    DEFAULT_DATABASE,
    _get_assertion,
    _iter_assertions,
    _read_assertions,
    _read_epoch,
    _read_identities,
    _read_identity,
    _subject_revoked,
    _visible_assertion,
    _visible_entity,
)


class Neo4jCanonicalGraphReader:
    """Read-only `TemporalGraphReader` over the canonical Neo4j surface."""

    def __init__(
        self,
        driver: Driver,
        *,
        namespace: str,
        database: str = DEFAULT_DATABASE,
    ) -> None:
        if not namespace:
            raise ValueError("namespace must be a non-empty string")
        self._driver = driver
        self._namespace = namespace
        self._database = database

    @property
    def namespace(self) -> str:
        return self._namespace

    def current_epoch(self) -> int:
        with self._driver.session(database=self._database) as session:
            return int(session.execute_read(_read_epoch, self._namespace))

    def get_entity(
        self, identity_id: str, options: GraphReadOptions = GraphReadOptions()
    ) -> CanonicalEntity | None:
        with self._driver.session(database=self._database) as session:
            row = session.execute_read(_read_identity, self._namespace, identity_id)
        return None if row is None else _visible_entity(row, options)

    def find_entities(
        self,
        entity_type: str | None = None,
        alias: EntityRef | None = None,
        options: GraphReadOptions = GraphReadOptions(),
    ) -> list[CanonicalEntity]:
        with self._driver.session(database=self._database) as session:
            rows = session.execute_read(
                _read_identities,
                self._namespace,
                entity_type,
                alias.render() if alias is not None else None,
            )
        found = [_visible_entity(row, options) for row in rows]
        return [e for e in found if e is not None]

    def assertions_for(
        self, identity_id: str, options: GraphReadOptions = GraphReadOptions()
    ) -> list[Assertion]:
        """Mirrors the store, including the revoked-subject shield (ADR-0026).

        Both surfaces share the module-level helpers in ``store.py``, so a read
        here can never disagree with a read through the store.
        """
        with self._driver.session(database=self._database) as session:
            identity = session.execute_read(_read_identity, self._namespace, identity_id)
            if identity is not None and _subject_revoked(
                json.loads(identity["status_history"]), options
            ):
                return []
            rows = session.execute_read(_read_assertions, self._namespace, identity_id)
        found = [_visible_assertion(row, options) for row in rows]
        return [a for a in found if a is not None]

    def get_assertion(
        self, assertion_id: str, options: GraphReadOptions = GraphReadOptions()
    ) -> Assertion | None:
        """Resolve one assertion by id, scoped to this reader's namespace.

        The id-keyed counterpart to :meth:`assertions_for` (ADR-0029). It
        shares :func:`~agentic_kg.migration.neo4j.store._get_assertion` with the
        store, so ``curation_epoch``, ``include_superseded``,
        ``include_revoked``, the subject-revoke shield (ADR-0026) and the
        temporal filters behave identically on both surfaces. An id in another
        namespace, or no id at all, returns ``None``.
        """
        return _get_assertion(
            self._driver, self._database, self._namespace, assertion_id, options
        )

    def iter_assertions(
        self,
        options: GraphReadOptions = GraphReadOptions(),
        *,
        batch_size: int = 500,
    ) -> Iterator[Assertion]:
        """Stream every visible assertion in this namespace.

        Keyset-paginated on the namespace's ``seq`` (never one giant list), so
        memory stays bounded regardless of graph size. Visibility is applied
        per record exactly as :meth:`assertions_for` applies it, including the
        subject-revoke shield; assertions in any other namespace are never
        selected. :meth:`~agentic_kg.migration.neo4j.store._iter_assertions`
        owns the pagination.
        """
        return _iter_assertions(
            self._driver, self._database, self._namespace, options, batch_size
        )

    def neighborhood(
        self, identity_id: str, hops: int = 1, options: GraphReadOptions = GraphReadOptions()
    ) -> list[CanonicalEntity]:
        visited = {identity_id}
        frontier = {identity_id}
        result: list[CanonicalEntity] = []
        for _ in range(hops):
            next_frontier: set[str] = set()
            for subject in sorted(frontier):
                for assertion in self.assertions_for(subject, options):
                    target = assertion.object_identity
                    if target is None or target in visited:
                        continue
                    visited.add(target)
                    entity = self.get_entity(target, options)
                    if entity is not None:
                        next_frontier.add(target)
                        result.append(entity)
            frontier = next_frontier
            if not frontier:
                break
        return result


__all__ = ["Neo4jCanonicalGraphReader"]
