"""The source-record shape the KGIS paper arm consumes, and the corpus's facts.

The structured paper arm (:mod:`agentic_kg.migration.ingestion.papers`) reads a
paper's identity, its PDF text and the provenance of its *record* from one
object. Two producers supply such an object:

* :class:`~agentic_kg.migration.ingestion.corpus.CorpusPaper` — a paper of the
  committed frozen corpus, whose record is the committed importer-output file.
* :class:`~agentic_kg.migration.live.AcquiredPaper` — a paper discovered at run
  time, whose record is the discovery API response it was normalized from.

This module states the interface once, as a `Protocol`, rather than restating it
in the two producers. ``test_source_record.py`` asserts that both satisfy it, so
adding a member here without implementing it on both fails loudly instead of
surfacing as an ``AttributeError`` deep inside candidate construction.

**Why the record is more than DOI/title/year.** §3.3 keys a `Paper` identity on
its DOI, but an *evidence row* is a statement about a source: which API returned
it, when it was retrieved, under what query, and with which cross-identifiers.
The frozen corpus answers those questions from a committed file; a live fetch
answers them from the response envelope. Both must reach
:func:`~agentic_kg.migration.ingestion.papers.build_paper_evidence`, so the
producer — not the builder — owns them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentic_kg.migration.ingestion.documents import PaperDocument


@runtime_checkable
class SourceRecord(Protocol):
    """One paper's text plus everything the paper arm needs to cite its source.

    Members are read, never written: both producers are frozen. The corpus's
    implementation reproduces the exact values the committed-importer path
    produced before this module existed, which is what keeps corpus mode
    byte-identical.
    """

    #: A stable, unique key for the paper within a run. Used as the KGIS
    #: `Document.doc_id`; it need not be human-meaningful.
    slug: str
    #: The paper's DOI, exactly as the source carried it. Normalized by callers.
    doi: str
    title: str
    year: int | None
    #: The full text the segmenter runs over.
    text: str
    #: The paper's cross-identifiers, keyed by namespace (`arxiv`,
    #: `semantic_scholar`, `openalex`, ...). Empty for the frozen corpus, which
    #: records only DOIs.
    external_ids: dict[str, str]

    def to_document(self) -> PaperDocument:
        """The KGIS source document for this paper."""
        ...

    @property
    def record_source_type(self) -> str:
        """`SourceCoordinates.source_type` for the record."""
        ...

    @property
    def record_locator(self) -> str:
        """`SourceCoordinates.locator` / provenance source for the record."""
        ...

    @property
    def record_fragment(self) -> str:
        """`SourceCoordinates.fragment` / provenance ref for the record."""
        ...

    @property
    def record_identity(self) -> str:
        """The record's own key, used to make its evidence id deterministic."""
        ...

    @property
    def evidence_content(self) -> str:
        """The human-readable evidence content for the record."""
        ...

    def evidence_hash_parts(self) -> tuple[str, ...]:
        """The stable parts hashed into the record's evidence `payload_hash`."""
        ...


__all__ = ["SourceRecord"]
