"""The revoke read rules, as pure functions — no Neo4j and no container.

The four new ADR-0026 conformance tests run against a real Neo4j in the
canonical-adapter CI job, which is the only place Docker exists. This module
covers the *decision logic* those tests turn on (``_subject_revoked``) without
a database, so a regression in the shield is caught on every local run and in
the fast CI selection too — not only in the job that has a container.

It is deliberately a whitebox of two module-level helpers rather than a
restatement of the contract tests: the contract tests remain the authority on
the store's behaviour, and these pin the one function that implements the
read-layer rule so its cross-term with ``_reported_status`` is legible.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentic_kg.migration.neo4j._contracts import CurationStatus, GraphReadOptions
from agentic_kg.migration.neo4j.store import _reported_status, _subject_revoked

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def _history(*entries: tuple[int, CurationStatus]) -> list[dict[str, object]]:
    return [
        {"epoch": epoch, "status": status.value, "superseded_at": None} for epoch, status in entries
    ]


# --- the shield itself --------------------------------------------------------


def test_an_active_identity_does_not_shield_its_assertions() -> None:
    assert _subject_revoked(_history((1, CurationStatus.ACTIVE)), GraphReadOptions()) is False


def test_a_revoked_identity_shields_by_default() -> None:
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.REVOKED))
    assert _subject_revoked(history, GraphReadOptions()) is True


def test_include_revoked_lifts_the_shield() -> None:
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.REVOKED))
    assert _subject_revoked(history, GraphReadOptions(include_revoked=True)) is False


def test_include_superseded_alone_does_not_lift_the_shield() -> None:
    """The cross-term: two switches, and neither reveals the other's records."""
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.REVOKED))
    assert _subject_revoked(history, GraphReadOptions(include_superseded=True)) is True


def test_the_shield_is_terminal_across_epochs() -> None:
    """REVOKED is global: a snapshot read of an earlier epoch does not lift it."""
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.REVOKED))
    assert _subject_revoked(history, GraphReadOptions(curation_epoch=1)) is True


def test_a_superseded_identity_does_not_shield() -> None:
    """Only REVOKED shields; a merged (SUPERSEDED) identity still serves reads."""
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.SUPERSEDED))
    assert _subject_revoked(history, GraphReadOptions()) is False


# --- the assertion's own gate, independently ----------------------------------


def test_a_superseded_assertion_still_needs_its_own_flag() -> None:
    """The shield and the assertion's status gate are independent.

    A superseded assertion on a *live* identity is hidden by default and needs
    ``include_superseded`` — the behaviour ``_reported_status`` owns and that
    the subject shield must not absorb.
    """
    history = _history((1, CurationStatus.ACTIVE), (2, CurationStatus.SUPERSEDED))
    assert _reported_status(history, GraphReadOptions()) is None
    assert _reported_status(history, GraphReadOptions(include_superseded=True)) is not None
