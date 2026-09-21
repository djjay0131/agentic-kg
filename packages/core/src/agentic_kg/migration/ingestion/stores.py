"""Where a shadow run persists — and where it is forbidden from persisting.

A shadow run writes to exactly two places, both SQLite, both under a directory
the caller names: `ledger.db` (the candidate ledger) and `evidence.db` (the
evidence registry). It writes to no Neo4j, touches no canonical graph, and
holds no `GraphMutationStore`. That is not a convention this module hopes
callers follow — :class:`ShadowStores` is the only way `pipeline.py` can build a
run, and it has no Neo4j-shaped constructor to misuse.

**The isolation is asserted, not assumed.** `test_isolation.py` walks this
subpackage's AST for any import of `neo4j`, `agentic_kg.knowledge_graph` or
`agentic_kg.migration.neo4j`, and for any name from `kg_contracts.stores` that
is a mutation surface. A shadow path that could reach the canonical graph is a
shadow path in name only, and "we checked at review time" is what the eleven
vacuous checks this programme has found all had in common.

**Deployment concern, recorded rather than solved.** `SqliteCandidateLedger` is
single-writer: SQLite serialises writers, and a WAL database is three files
(`-wal`, `-shm`) that must live on a filesystem with working POSIX advisory
locks. Cloud Run gives each revision a private, ephemeral filesystem and scales
to N instances, so N concurrent ingest jobs would each write a *different*
ledger and all of them would vanish on scale-in; pointing them at a shared GCS
FUSE mount is worse, because that mount does not honour the locks SQLite needs
and the failure is corruption rather than an error. ADR-0012 permits a backend
swap behind the same ports (`CandidateSink` / `LedgerReader`), which is the fix
— this PR does not make it. Local and dev persistence is what
:class:`ShadowStores` is for, and :meth:`ShadowStores.deployment_warning` states
the limitation in the one place an operator will actually read it.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from agentic_kg.migration.ingestion._contracts import (
    SqliteCandidateLedger,
    SqliteEvidenceRegistry,
)

LEDGER_FILENAME = "ledger.db"
EVIDENCE_FILENAME = "evidence.db"

#: In-memory sentinel. Passed straight through to SQLite, so a run under it
#: persists nothing at all — the default for tests that only care about the
#: candidates a run produced.
IN_MEMORY = ":memory:"

_DEPLOYMENT_WARNING = (
    "The shadow ledger is SQLite: single-writer, and a WAL database is three "
    "files needing working POSIX advisory locks. On Cloud Run each revision has "
    "a private ephemeral filesystem and scales to N instances, so N ingest jobs "
    "would write N separate ledgers and lose all of them on scale-in; a shared "
    "GCS FUSE mount does not honour SQLite's locks and fails as corruption "
    "rather than as an error. Isolated local/dev persistence only. ADR-0012 "
    "permits swapping the backend behind CandidateSink/LedgerReader; that swap "
    "is not part of this PR."
)


@dataclass(frozen=True)
class ShadowStores(AbstractContextManager["ShadowStores"]):
    """An isolated ledger + evidence registry pair.

    Constructed through :meth:`at` or :meth:`in_memory`, never directly, so
    that every instance has been through the directory check.
    """

    ledger: SqliteCandidateLedger
    evidence: SqliteEvidenceRegistry
    root: Path | None

    @classmethod
    def in_memory(cls) -> ShadowStores:
        """Stores that persist nothing. `root` is `None`, and that is checkable.

        Two separate `:memory:` databases, not one shared connection: the ledger
        and the registry have different schemas and KGIS opens each with its own
        `open_*_db`. Sharing one would make a schema collision the first
        symptom.
        """
        return cls(
            ledger=SqliteCandidateLedger(IN_MEMORY),
            evidence=SqliteEvidenceRegistry(IN_MEMORY),
            root=None,
        )

    @classmethod
    def at(cls, root: Path | str) -> ShadowStores:
        """Stores under `root`, which is created if absent.

        `root` must be a directory this run owns. It is not validated against a
        blocklist of "production" paths, because such a list is exactly the kind
        of check that reads as rigorous and enforces nothing — a deployment can
        mount anything anywhere, so the list would be a guess about someone
        else's filesystem. The real guarantee is structural and is asserted
        elsewhere: this subpackage cannot reach a canonical store at all.
        """
        directory = Path(root)
        directory.mkdir(parents=True, exist_ok=True)
        return cls(
            ledger=SqliteCandidateLedger(directory / LEDGER_FILENAME),
            evidence=SqliteEvidenceRegistry(directory / EVIDENCE_FILENAME),
            root=directory,
        )

    @staticmethod
    def deployment_warning() -> str:
        """The SQLite/Cloud Run limitation, in operator-facing words."""
        return _DEPLOYMENT_WARNING

    @property
    def persists(self) -> bool:
        return self.root is not None

    def close(self) -> None:
        # Close both even if the first raises: leaking a SQLite handle from the
        # registry because the ledger failed to close would turn one error into
        # a locked file on the next run.
        try:
            self.ledger.close()
        finally:
            self.evidence.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self.close()
        return None


__all__ = [
    "EVIDENCE_FILENAME",
    "IN_MEMORY",
    "LEDGER_FILENAME",
    "ShadowStores",
]
