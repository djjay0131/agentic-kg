"""The only place this repository may build a KGIS structured-source reader.

A deployment prerequisite, not a style preference
------------------------------------------------
``StructuredRecordReader`` stamps ``@snapshot=<version>`` into its ``locator``
by **default** (``include_snapshot_in_locator=True``). That locator travels into
every candidate's ``SourceCoordinates``, and from there into the ``Provenance``
the KGCS planner feeds to ``kgcs.records.record_seed`` — which is what
``assertion_id`` is minted from. So the snapshot token is part of a record's
identity.

For a **one-shot import** that is exactly right: the candidate records which
snapshot it was read from.

For a source read **on a schedule** it is a defect, and a quiet one. A provider
given an explicit ``snapshot_version`` — a database transaction id, an LSN, a
watermark, which is what a real re-sync uses — hands back a *new* version on
every run whether or not a single row changed. New version → new locator → new
``record_seed`` → new ``assertion_id`` → a brand-new ``ACTIVE`` record for a
row that did not move. Thirty daily re-syncs of one unchanged row produce
thirty indistinguishable ACTIVE records rather than one. Nothing errors and
nothing looks wrong; the graph just silently accumulates duplicate present-tense
facts, and every downstream count is inflated.

``test_structured_locator.py`` measures exactly that — 30 against 1 — and
asserts both halves, including that two *genuinely different* sources still mint
different records with the flag off, so the fix is not "collapse everything".

Operator check
--------------
**If a source is read on a schedule and its locator ends in ``@snapshot=``, it
is misconfigured.** See ``docs/operations/structured-resync-runbook.md``.

Why a module and not a comment
------------------------------
The prerequisite is a default that has to be actively turned *off*, so the
failure mode is omission, and a comment does not catch omission. Every
structured reader this repository builds goes through :func:`structured_reader`
or :func:`structured_config`, both of which set the flag off and **refuse** to
be asked for the unsafe value. ``test_structured_locator.py`` walks the AST of
``migration/ingestion/`` and fails if any other module constructs a
``StructuredRecordReader``/``StructuredSyncConfig`` directly, which is the only
way to reintroduce the default without editing this file.

Nothing in the shadow path uses this today
------------------------------------------
Stated plainly so the measurement is not over-read. The shadow pipeline reads
committed importer-output files and builds ``SourceCoordinates`` by hand (see
``papers.py``), so **no locator this repository currently emits carries
``@snapshot=``** — ``test_no_shadow_candidate_carries_a_snapshot_locator``
measures that over the whole 252-candidate corpus rather than asserting it. This
module is the enforced landing site for the moment the structured arm reads a
real source, which is when the prerequisite starts to bite.
"""

from __future__ import annotations

from typing import Any

from agentic_kg.migration.ingestion._contracts import (
    RowProvider,
    StructuredRecordReader,
    StructuredSyncConfig,
)

#: The locator suffix a scheduled re-sync must never carry. Used by the runbook
#: check and by the tests, so the string is stated once.
SNAPSHOT_LOCATOR_MARKER = "@snapshot="

#: The value :func:`structured_reader` and :func:`structured_config` pin.
#: ``False`` = the locator is snapshot-independent, so a re-read of unchanged
#: data mints the record it already minted instead of a new one.
INCLUDE_SNAPSHOT_IN_LOCATOR = False


class SnapshotLocatorRefused(ValueError):
    """A caller asked for the snapshot-stamped locator this repository forbids."""


def _check(include_snapshot_in_locator: bool) -> None:
    if include_snapshot_in_locator:
        raise SnapshotLocatorRefused(
            "include_snapshot_in_locator=True stamps '@snapshot=<version>' into "
            "the locator, which becomes part of every assertion_id minted from "
            "the resulting candidates. On a scheduled re-sync whose provider "
            "carries an explicit snapshot_version (a txn id, LSN or watermark) "
            "the version changes on every run even when no row changed, so each "
            "run mints a fresh ACTIVE record for the same unchanged fact. If you "
            "genuinely want snapshot-scoped coordinates for a ONE-SHOT import, "
            "construct kgis.structured.StructuredRecordReader directly and say "
            "in the call site why the source is never re-read."
        )


def structured_reader(provider: RowProvider, **kwargs: Any) -> StructuredRecordReader:
    """A structured reader with the snapshot locator switched off.

    Accepts the same keyword arguments as ``StructuredRecordReader`` (e.g.
    ``batch_size``). Passing ``include_snapshot_in_locator=True`` raises
    :class:`SnapshotLocatorRefused` — the point is that the safe value cannot
    be undone by a keyword argument at a call site, only by editing this module,
    which is a reviewable change.
    """
    _check(bool(kwargs.pop("include_snapshot_in_locator", False)))
    return StructuredRecordReader(
        provider, include_snapshot_in_locator=INCLUDE_SNAPSHOT_IN_LOCATOR, **kwargs
    )


def structured_config(**kwargs: Any) -> StructuredSyncConfig:
    """A ``StructuredSyncConfig`` with the same flag pinned off.

    ``StructuredSyncConfig.reader()`` passes its own
    ``include_snapshot_in_locator`` through to the reader, so a config built
    with the upstream default reintroduces the defect one layer up where
    :func:`structured_reader` would never be called at all.
    """
    _check(bool(kwargs.pop("include_snapshot_in_locator", False)))
    return StructuredSyncConfig(
        include_snapshot_in_locator=INCLUDE_SNAPSHOT_IN_LOCATOR, **kwargs
    )


def locator_is_resync_safe(locator: str) -> bool:
    """The operator check, as a function: no ``@snapshot=`` in the locator.

    Exposed so a health check or an ingest report can apply the same rule the
    runbook states, rather than each caller re-spelling the substring.
    """
    return SNAPSHOT_LOCATOR_MARKER not in locator


__all__ = [
    "INCLUDE_SNAPSHOT_IN_LOCATOR",
    "SNAPSHOT_LOCATOR_MARKER",
    "SnapshotLocatorRefused",
    "locator_is_resync_safe",
    "structured_config",
    "structured_reader",
]
