"""The scheduled-re-sync prerequisite, measured and then pinned.

``StructuredRecordReader`` stamps ``@snapshot=<version>`` into its locator by
default. The locator becomes ``Provenance.source_ref``, which is one of the
seven components ``kgcs.records.record_seed`` hashes into an ``assertion_id``.
So on a scheduled re-sync whose provider carries an explicit
``snapshot_version`` — a transaction id, an LSN, a watermark, which is what a
real re-sync uses — the token moves every run even when no row changed, and
each run mints a **new** record for the same unchanged fact.

This module does not assert that claim; it **measures** it, in both directions,
against the real reader and the real planner:

* with the upstream default: 30 daily re-syncs of one unchanged row → 30
  distinct ``assertion_id``s;
* through :func:`~agentic_kg.migration.ingestion.structured.structured_reader`:
  the same 30 re-syncs → 1.

Both halves matter. The first is the falsification — without it a green suite
would be consistent with the flag having no effect at all, and the guard would
be pinning nothing. The third test is the anti-vacuity control: switching the
flag off must not collapse *genuinely different* sources onto one record, which
is the obvious way to make the second number 1 for the wrong reason.

No network, no Docker, no model: a stdlib ``sqlite3`` in-memory table with one
row.
"""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from agentic_kg.migration.ingestion._contracts import (
    AttributeAssertionCandidate,
    CandidateScores,
    RowProvider,
    StructuredRecordReader,
)
from agentic_kg.migration.ingestion.structured import (
    INCLUDE_SNAPSHOT_IN_LOCATOR,
    SNAPSHOT_LOCATOR_MARKER,
    SnapshotLocatorRefused,
    locator_is_resync_safe,
    structured_config,
    structured_reader,
)
from kg_contracts.curation import CurationOperationType, ResolutionDecision
from kg_contracts.policy import AdjudicationRoute
from kgcs.planner import CurationPlanner, ResolvedCandidate
from kgis.structured import SqliteRowProvider

from .astscan import imported_symbols, package_modules, package_of, parse

#: A daily re-sync for a month. The number is the one the upstream measurement
#: used; nothing here depends on it being exactly 30 beyond "many".
RESYNCS = 30

SUBJECT = "kg://g1/identity/01J0000000000000000000000A"

#: ``packages/core/src/agentic_kg/migration/ingestion`` — the tree the AST scan
#: walks. Asserted to exist and to be non-empty by
#: :func:`test_the_guard_is_the_only_way_to_build_a_structured_reader`: a wrong
#: path here makes ``package_modules`` return ``[]`` and the scan pass
#: vacuously, which is the exact failure this repository keeps re-finding.
INGESTION_SRC = (
    Path(__file__).resolve().parents[3] / "src" / "agentic_kg" / "migration" / "ingestion"
)

#: The importable root the relative-import resolver measures against.
SRC_ROOT = INGESTION_SRC.parents[1]

#: The two upstream names that reintroduce the default if constructed directly.
GUARDED_SYMBOLS = frozenset({"StructuredRecordReader", "StructuredSyncConfig"})

#: The two modules allowed to name them: the single door onto the optional
#: packages, and the guard that pins the flag.
ALLOWED_MODULES = frozenset({"_contracts.py", "structured.py"})


# --- fixtures -----------------------------------------------------------------


def _table(height: int = 200) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE players (id TEXT PRIMARY KEY, height_cm INTEGER)")
    conn.execute("INSERT INTO players VALUES ('p1', ?)", (height,))
    conn.commit()
    return conn


@pytest.fixture
def unchanged_row() -> Iterator[sqlite3.Connection]:
    conn = _table()
    try:
        yield conn
    finally:
        conn.close()


def _provider(conn: sqlite3.Connection, **kwargs: object) -> RowProvider:
    return SqliteRowProvider(conn, table="players", key_fields=("id",), **kwargs)  # type: ignore[arg-type]


def _mint(reader: StructuredRecordReader, run: int) -> tuple[str, str]:
    """Read one row through ``reader`` and plan it; return (locator, assertion_id).

    Goes through ``CurationPlanner`` rather than calling ``record_seed``
    directly, because the property under test is that the *planner* folds the
    reader's locator into the minted id. Re-deriving the seed here would be a
    local restatement of upstream's rule and would stay green if the planner
    stopped using it.

    The ``ResolutionDecision`` is built by hand, at ``AUTO``, rather than taken
    from ``ResolutionPolicy`` — see ``test_adjudication_routing.py`` for why
    that path cannot currently produce an ``AUTO`` decision. That limitation is
    about *routing*; it is orthogonal to what is measured here, and hand-supplying
    the decision keeps this test measuring the locator rather than the gate.
    """
    record = next(iter(reader.read()))
    candidate = AttributeAssertionCandidate(
        candidate_id=f"cand_{run}",
        graph_id="g1",
        producer="test.structured",
        producer_run_id="run_1",
        ontology_version="1",
        semantic_key="players|p1|height_cm",
        source_coordinates=record.coordinates,
        scores=CandidateScores(extraction_confidence=1.0, source_reliability=1.0),
        evidence_refs=(),
        trace_id=f"tr_{run}",
        subject=SUBJECT,
        attribute="height_cm",
        value=200,
    )
    decision = ResolutionDecision(
        candidate_id=candidate.candidate_id,
        resolved_identity=SUBJECT,
        create_new_identity=False,
        route=AdjudicationRoute.AUTO,
        score_vector={"extraction_confidence": 1.0},
        matcher_version=None,
        snapshot_version="0",
        trace_id=candidate.trace_id,
    )
    result = CurationPlanner().plan(
        [ResolvedCandidate(candidate=candidate, resolution=decision)]
    )
    attaches = [
        planned.operation
        for planned in result.planned_operations
        if planned.operation.type is CurationOperationType.ATTACH_ASSERTION
    ]
    assert len(attaches) == 1, f"expected one ATTACH_ASSERTION, got {attaches}"
    return record.coordinates.locator, str(attaches[0].payload["assertion_id"])


def _resync(
    conn: sqlite3.Connection, build: Callable[[RowProvider], StructuredRecordReader]
) -> tuple[set[str], set[str]]:
    """``RESYNCS`` reads of the same unchanged row; returns (locators, assertion_ids).

    A fresh provider per run with an advancing ``snapshot_version``, because
    that is what a scheduled re-sync against a real database looks like: the
    watermark moves whether or not the data did. (With the *derived* default
    version — a digest of the rows — unchanged data yields an unchanged token
    and the defect does not appear, which is precisely why it is invisible in a
    fixture-only test and shows up in production.)
    """
    locators: set[str] = set()
    ids: set[str] = set()
    for run in range(RESYNCS):
        reader = build(_provider(conn, snapshot_version=f"txn_{run}"))
        locator, assertion_id = _mint(reader, run)
        locators.add(locator)
        ids.add(assertion_id)
    return locators, ids


# --- the measurement ----------------------------------------------------------


def test_the_upstream_default_mints_a_new_record_on_every_resync(unchanged_row) -> None:
    """The falsification: the defect is real, and this file can see it.

    Without this half, the guarded test below would pass just as well against a
    flag that changed nothing.
    """
    locators, ids = _resync(unchanged_row, StructuredRecordReader)
    assert len(locators) == RESYNCS
    assert len(ids) == RESYNCS, (
        f"expected the snapshot-stamped locator to mint {RESYNCS} distinct "
        f"records for one unchanged row; got {len(ids)}"
    )
    assert all(SNAPSHOT_LOCATOR_MARKER in locator for locator in locators)


def test_the_guarded_reader_mints_one_record_across_every_resync(unchanged_row) -> None:
    """The prerequisite, as a number: 30 re-syncs of one unchanged row → 1 record."""
    locators, ids = _resync(unchanged_row, structured_reader)
    assert len(locators) == 1, f"the locator must not move between re-syncs: {locators}"
    assert len(ids) == 1, (
        f"a re-sync that read nothing new must replay onto the record it already "
        f"minted; got {len(ids)} distinct assertion_ids"
    )
    assert all(locator_is_resync_safe(locator) for locator in locators)


def test_two_genuine_sources_still_mint_different_records_when_guarded() -> None:
    """Anti-vacuity: the fix must not collapse real differences onto one record.

    Dropping the snapshot token is only correct if identity still discriminates
    on everything that genuinely differs. Two different sources holding
    different values for the same subject must stay two records.
    """
    conn_a, conn_b = _table(200), _table(195)
    try:
        _, id_a = _mint(
            structured_reader(_provider(conn_a, locator="sqlite://source_a")), 0
        )
        _, id_b = _mint(
            structured_reader(_provider(conn_b, locator="sqlite://source_b")), 0
        )
    finally:
        conn_a.close()
        conn_b.close()
    assert id_a != id_b, (
        "two genuinely different sources collapsed onto one assertion_id - the "
        "guard has removed more than the snapshot token"
    )


# --- the guard ----------------------------------------------------------------


def test_the_flag_is_pinned_off() -> None:
    assert INCLUDE_SNAPSHOT_IN_LOCATOR is False


def test_structured_reader_refuses_to_be_asked_for_the_snapshot_locator(
    unchanged_row,
) -> None:
    """Turning it back on at a call site must fail loudly, not silently work."""
    with pytest.raises(SnapshotLocatorRefused, match="include_snapshot_in_locator=True"):
        structured_reader(_provider(unchanged_row), include_snapshot_in_locator=True)


def test_structured_config_refuses_it_too() -> None:
    """The config layer reintroduces it one level above the reader.

    ``StructuredSyncConfig.reader()`` passes its own flag through, so a config
    built with the upstream default defeats the reader guard without ever
    calling it.
    """
    with pytest.raises(SnapshotLocatorRefused):
        structured_config(provider=None, include_snapshot_in_locator=True)


def test_the_guard_is_the_only_way_to_build_a_structured_reader() -> None:
    """An AST scan: no other module under ``ingestion/`` may construct one.

    The call-site guard above is bypassed simply by importing
    ``StructuredRecordReader`` and calling it, which is not an attack but the
    thing a contributor does by default. This is the check that notices.
    """
    modules = package_modules(INGESTION_SRC)
    assert len(modules) > 5, (
        f"{INGESTION_SRC} yielded {len(modules)} modules - the scan would pass "
        f"vacuously; the path is wrong"
    )
    assert any(path.name == "pipeline.py" for path in modules)

    offenders: list[str] = []
    for path in modules:
        if path.name in ALLOWED_MODULES:
            continue
        package = package_of(path, SRC_ROOT, "agentic_kg")
        for lineno, _module, symbol in imported_symbols(parse(path), package):
            if symbol in GUARDED_SYMBOLS:
                offenders.append(f"{path.name}:{lineno} imports {symbol}")
    assert offenders == [], (
        "these modules can construct a structured reader with the default "
        "snapshot locator, bypassing structured.py: " + "; ".join(offenders)
    )


def test_the_ast_check_would_notice_a_direct_import(tmp_path: Path) -> None:
    """Obligation 5: the scan above can fail for the reason it names."""
    module = tmp_path / "sneaky.py"
    module.write_text(
        "from kgis.structured import StructuredRecordReader\n", encoding="utf-8"
    )
    found = [
        symbol
        for _lineno, _module, symbol in imported_symbols(parse(module), "pkg")
        if symbol in GUARDED_SYMBOLS
    ]
    assert found == ["StructuredRecordReader"]


def test_the_guard_module_itself_pins_the_flag_in_its_call() -> None:
    """``structured.py`` must pass the constant, not a literal that can drift.

    A keyword spelled ``include_snapshot_in_locator=False`` inline would pass
    every test above and silently diverge from
    :data:`INCLUDE_SNAPSHOT_IN_LOCATOR` if the constant were ever changed.
    """
    tree = parse(INGESTION_SRC / "structured.py")
    passes = [
        keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "include_snapshot_in_locator"
    ]
    assert passes, "structured.py never passes include_snapshot_in_locator at all"
    assert all(
        isinstance(keyword.value, ast.Name)
        and keyword.value.id == "INCLUDE_SNAPSHOT_IN_LOCATOR"
        for keyword in passes
    ), "structured.py passes a literal rather than INCLUDE_SNAPSHOT_IN_LOCATOR"


# --- what the shadow path actually emits today --------------------------------


def test_no_shadow_candidate_carries_a_snapshot_locator(shadow_run) -> None:
    """Measured over the whole corpus, not assumed.

    The shadow path builds ``SourceCoordinates`` by hand from committed
    importer output (``papers.py``) and reaches ``kgis.structured`` nowhere, so
    the prerequisite does not currently bite. That is a fact about today's
    wiring rather than a guarantee, and it is worth a number: if a future
    change routes the structured arm through a real ``RowProvider`` without the
    guard, this goes red with the offending locators named.
    """
    candidates = (*shadow_run.paper_candidates, *shadow_run.candidates)
    assert candidates, "an empty corpus would make this vacuous"
    stamped = [
        c.source_coordinates.locator
        for c in candidates
        if not locator_is_resync_safe(c.source_coordinates.locator)
    ]
    assert stamped == [], f"{len(stamped)} of {len(candidates)} locators carry a snapshot token"
