"""Adapting agentic-kg's segmenter output into KGIS documents and chunks.

This is an *adapter*, not a reimplementation. The segmentation itself is
`agentic_kg.extraction.section_segmenter.SectionSegmenter` — the same
production segmenter the legacy ingestion path uses, with its frozen corpus
fixtures — and all this module does is carry its output across the seam with
coordinates the spec's fragment grammar can express.

Two things are worth stating plainly, because both are places where a
plausible shortcut would quietly produce wrong coordinates.

**`Section.start_char` is not where the content starts.** It is the offset of
the *heading line*, and `Section.content` has been `.strip()`ed, so
``text[section.start_char:section.end_char] != section.content`` for essentially
every real section. KGIS documents the opposite invariant for its own chunks
(``document.text[chunk.start:chunk.end] == chunk.text``, `documents.py:67-81`),
and that invariant is what makes a coordinate *resolvable* — an operator or a
re-collection can find the exact passage from the fragment alone. So
:class:`SectionChunker` locates the content inside the section's span and emits
true content offsets. :func:`agentic_kg.migration.ingestion.documents` ships a
test asserting the invariant holds over all eight committed papers; it fails if
this is ever weakened back to passing `start_char` through.

**`Chunk.fragment` is a KGIS grammar, and the spec mandates a different one.**
KGIS renders ``chunk:{index}@chars:{start}-{end}``; spec §3.4 mandates
``sec:<slug>#chars:<start>-<end>[#page:<n>]`` and states that agentic-kg owns
that grammar and its parser. `fragment` is a read-only property on a frozen
pydantic model, but it is a *property*, so a subclass may override it — and
because `LLMExtractor` and the runner both reach the fragment through
`chunk.coordinates` / `chunk.fragment` rather than reconstructing it, the
override reaches every candidate, every evidence row and every evidence id
without a single KGIS edit. :class:`SectionChunk` is that subclass.

**Page numbers are absent, honestly.** `ExtractedText.full_text` joins pages
with a blank line and keeps no page-to-offset table
(`extraction/pdf_extractor.py`), so once text reaches the segmenter the page a
span came from is gone. §3.4 makes `#page:` optional and present "only when
PyMuPDF supplied it"; here it never is, so it is omitted rather than guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agentic_kg.extraction.section_segmenter import (
    Section,
    SectionSegmenter,
    SectionType,
    get_section_segmenter,
)
from agentic_kg.migration.ingestion._contracts import Chunk, Document

#: Sections the legacy extractor path keeps (`ingestion.py`'s
#: `_EXTRACTOR_WANTED_SECTIONS`). Mirrored so the shadow path reads the same
#: text the legacy path reads — a comparison between the two arms is only
#: meaningful if they were shown the same input.
WANTED_SECTIONS: tuple[SectionType, ...] = (
    SectionType.ABSTRACT,
    SectionType.INTRODUCTION,
    SectionType.METHODS,
    SectionType.EXPERIMENTS,
)

#: `source_type` for a paper document. Matches the spec's §3.3 coordinate table
#: for extracted (as opposed to structured) candidates.
PAPER_SOURCE_TYPE = "pdf"


class SectionChunk(Chunk):
    """A KGIS `Chunk` whose fragment speaks the spec §3.4 grammar.

    Subclassing rather than post-processing is deliberate. The runner builds
    evidence ids from `chunk.fragment` (`chunk_evidence_id`), writes
    `Evidence.source_locator` as ``f"{locator}#{fragment}"`` and stamps
    `chunk.coordinates` onto every candidate. Rewriting the fragment after the
    fact would mean rewriting it in three places and keeping them in step; an
    overridden property is computed once, by definition consistently.
    """

    section_slug: str
    page: int | None = None

    @property
    def fragment(self) -> str:
        """`sec:<slug>#chars:<start>-<end>[#page:<n>]` — spec §3.4."""
        base = f"sec:{self.section_slug}#chars:{self.start}-{self.end}"
        return base if self.page is None else f"{base}#page:{self.page}"


@dataclass(frozen=True)
class PaperDocument:
    """One paper's text plus the identity the mapping keys candidates on.

    `doi` is carried separately from the KGIS `Document` because the
    `Document` contract has nowhere to put it that a builder can read, and
    because the DOI is what every paper-scoped semantic key is built from. It
    travels into the KGIS layer inside the locator (see :meth:`locator`), which
    is the one field that reaches a builder through `record.coordinates`.
    """

    slug: str
    doi: str
    title: str
    text: str

    @property
    def locator(self) -> str:
        """`paper://doi/<doi>` — the document's source locator.

        A DOI-bearing locator, not a file path. Two reasons. The path is a
        property of this checkout and would make every coordinate
        unreproducible elsewhere; and the locator is the only part of a chunk
        that reaches a `CandidateBuilder` (through `record.coordinates`), so a
        paper-scoped identity such as `Problem`'s `K` can be derived from it
        without threading a parallel channel through KGIS.
        """
        return f"paper://doi/{self.doi}"

    def to_kgis_document(self) -> Document:
        """The KGIS `Document` for this paper."""
        return Document(
            doc_id=self.slug,
            text=self.text,
            source_type=PAPER_SOURCE_TYPE,
            locator=self.locator,
            metadata={"doi": self.doi, "title": self.title},
        )


def content_span(text: str, section: Section) -> tuple[int, int] | None:
    """True `(start, end)` of `section.content` inside `text`.

    `Section` carries the heading's offset and a stripped body, so the body's
    own offsets have to be recovered. The search is bounded to the section's
    own `[start_char, end_char)` window rather than run over the whole
    document: a short section body (a one-line abstract, a run-in heading) can
    easily occur verbatim somewhere else, and an unbounded `str.find` would
    happily return that other occurrence and mint a coordinate pointing at the
    wrong passage — a defect no amount of downstream checking would catch,
    because the resulting fragment is perfectly well-formed.

    Returns `None` when the content cannot be located in its own window, which
    the caller treats as "no chunk", never as "offset 0".
    """
    window_start = max(0, section.start_char)
    window_end = min(len(text), section.end_char) if section.end_char else len(text)
    if window_end <= window_start or not section.content:
        return None
    offset = text.find(section.content, window_start, window_end)
    if offset == -1:
        return None
    return offset, offset + len(section.content)


class SectionChunker:
    """Segments a paper into one :class:`SectionChunk` per kept section.

    A `Chunker` must be a *pure function of the document* — no clock, no
    randomness, no state — because its coordinates are idempotency anchors
    (`kgis/extraction/documents.py:159-167`). `SectionSegmenter` satisfies that:
    it is regex-driven over the text and holds no per-call state.

    `keep` defaults to the four sections the legacy extractor path keeps, so the
    shadow arm and the legacy arm read the same text. Passing `keep=None` keeps
    every section the segmenter found.
    """

    def __init__(
        self,
        *,
        segmenter: SectionSegmenter | None = None,
        keep: Sequence[SectionType] | None = WANTED_SECTIONS,
    ) -> None:
        self._segmenter = segmenter if segmenter is not None else get_section_segmenter()
        self._keep = None if keep is None else frozenset(keep)

    def chunk(self, document: Document) -> list[Chunk]:
        segmented = self._segmenter.segment_with_abstract(document.text)
        chunks: list[Chunk] = []
        index = 0
        for section in segmented.sections:
            if self._keep is not None and section.section_type not in self._keep:
                continue
            span = content_span(document.text, section)
            if span is None:
                continue
            start, end = span
            chunks.append(
                SectionChunk(
                    doc_id=document.doc_id,
                    index=index,
                    start=start,
                    end=end,
                    text=section.content,
                    source_type=document.source_type,
                    locator=document.resolved_locator,
                    section_slug=section.section_type.value,
                )
            )
            index += 1
        return chunks


def doi_from_locator(locator: str) -> str:
    """Recover the DOI a `paper://doi/<doi>` locator carries.

    Raises rather than returning a sentinel: a builder that silently accepted a
    missing DOI would mint a `Problem` semantic key scoped to nothing, and every
    such key would collide with every other paper's.
    """
    prefix = "paper://doi/"
    if not locator.startswith(prefix):
        raise ValueError(
            f"{locator!r} is not a paper locator (expected {prefix!r} prefix); "
            f"paper-scoped candidate keys cannot be derived from it"
        )
    doi = locator[len(prefix) :]
    if not doi:
        raise ValueError(f"{locator!r} carries an empty DOI")
    return doi


__all__ = [
    "PAPER_SOURCE_TYPE",
    "WANTED_SECTIONS",
    "PaperDocument",
    "SectionChunk",
    "SectionChunker",
    "content_span",
    "doi_from_locator",
]
