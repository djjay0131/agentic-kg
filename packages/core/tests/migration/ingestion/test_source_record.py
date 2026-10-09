"""The two source-record producers satisfy one interface.

The frozen corpus and the live acquirer feed the *same* paper arm, so they must
present the same surface. This suite asserts that structurally (``isinstance``
against a runtime-checkable Protocol) and — for the corpus — numerically: its
record properties reproduce, exactly, the values the paper arm derived inline
before ``SourceRecord`` existed. If a member is added to the protocol and only
one producer implements it, the ``isinstance`` check fails here rather than as
an ``AttributeError`` deep inside candidate construction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip(
    "kgis",
    reason=(
        "the opt-in 'migration' extra is not installed; "
        "pip install './packages/core[migration]'"
    ),
)

from agentic_kg.migration.ingestion.corpus import load_paper  # noqa: E402
from agentic_kg.migration.ingestion.papers import (  # noqa: E402
    aliases_for,
    paper_evidence_id,
    record_coordinates,
)
from agentic_kg.migration.ingestion.source_record import SourceRecord  # noqa: E402
from agentic_kg.migration.live import AcquiredPaper  # noqa: E402


def _acquired() -> AcquiredPaper:
    return AcquiredPaper(
        slug="semantic_scholar-abc",
        doi="10.1234/Live",
        title="A Live Paper",
        year=2025,
        text="Abstract\nWe study live things. " * 20,
        source_api="semantic_scholar",
        retrieved_at=datetime(2026, 9, 18, tzinfo=UTC),
        query="live things",
        external_ids={"semantic_scholar": "abc", "arxiv": "2501.00001"},
    )


def test_both_producers_are_source_records() -> None:
    assert isinstance(load_paper("cskg"), SourceRecord)
    assert isinstance(_acquired(), SourceRecord)


def test_corpus_record_values_are_the_legacy_values() -> None:
    """The corpus's record coordinates and evidence id, unchanged.

    These are the exact strings the paper arm produced before this refactor;
    pinning them is what makes "corpus mode is byte-identical" checkable without
    a full candidate dump.
    """
    paper = load_paper("cskg")
    coords = record_coordinates(paper)
    assert coords.source_type == "importer_record"
    assert coords.locator == (
        f"docs/ground-truth/importer-output/{paper.importer_path.name}"
    )
    assert coords.fragment == "paper"
    assert paper.record_identity == paper.importer_path.name
    assert paper.evidence_content == f"{paper.title} ({paper.year}) doi:{paper.doi}"
    assert paper.evidence_hash_parts() == (paper.title, str(paper.year), paper.doi)
    assert paper_evidence_id(paper).startswith("ev_")


def test_corpus_aliases_are_doi_only() -> None:
    paper = load_paper("cskg")
    assert paper.external_ids == {}
    aliases = aliases_for(paper.doi, paper.external_ids)
    assert [alias.namespace for alias in aliases] == ["doi"]


def test_live_record_carries_full_provenance() -> None:
    paper = _acquired()
    coords = record_coordinates(paper)
    assert coords.source_type == "semantic_scholar"
    assert coords.locator == "semantic_scholar:doi:10.1234/live"
    # The fragment carries the query the paper was discovered under.
    assert paper.query in (coords.fragment or "")
    # Content names the source API, the retrieval instant, the query and ids.
    content = paper.evidence_content
    assert "source:semantic_scholar" in content
    assert paper.retrieved_at.isoformat() in content
    assert "query:live things" in content
    assert "arxiv=2501.00001" in content
    assert "semantic_scholar=abc" in content


def test_live_aliases_include_cross_identifiers() -> None:
    paper = _acquired()
    namespaces = {alias.namespace for alias in aliases_for(paper.doi, paper.external_ids)}
    assert namespaces == {"doi", "semantic_scholar", "arxiv"}


def test_acquired_paper_document_uses_the_doi_locator() -> None:
    """Every paper-scoped key is built from `paper://doi/<doi>`."""
    doc = _acquired().to_document()
    assert doc.locator == "paper://doi/10.1234/Live"
