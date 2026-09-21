"""Coordinates: the resolvability invariant and the §3.4 grammar.

The assertions here run over the **eight committed papers**, not over a
hand-built string, because the defect they exist to catch is one only real
segmenter output produces: `Section.start_char` points at the heading line and
`Section.content` has been stripped, so passing the segmenter's offsets through
yields fragments that are perfectly well-formed and point at the wrong text.
A synthetic one-section document would not exhibit it.
"""

from __future__ import annotations

import pytest
from agentic_kg.extraction.section_segmenter import Section, SectionType
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.documents import (
    SectionChunk,
    SectionChunker,
    content_span,
    doi_from_locator,
)
from agentic_kg.migration.ingestion.identity import parse_fragment


def _chunks(corpus: tuple[CorpusPaper, ...]) -> list[tuple[str, str, SectionChunk]]:
    """`(slug, document_text, chunk)` for every chunk of every paper."""
    chunker = SectionChunker()
    out: list[tuple[str, str, SectionChunk]] = []
    for paper in corpus:
        document = paper.to_document().to_kgis_document()
        for chunk in chunker.chunk(document):
            assert isinstance(chunk, SectionChunk)
            out.append((paper.slug, document.text, chunk))
    return out


def test_the_corpus_produces_chunks_for_every_paper(
    corpus: tuple[CorpusPaper, ...],
) -> None:
    """Non-emptiness, per paper, before anything else quantifies over chunks.

    §9.0 obligation 1. Every other test in this module quantifies over the
    chunk set; if a chunker regression emptied it they would all pass while
    checking nothing.
    """
    assert len(corpus) == 8
    chunker = SectionChunker()
    for paper in corpus:
        chunks = chunker.chunk(paper.to_document().to_kgis_document())
        assert chunks, f"{paper.slug} produced no chunks"


def test_chunk_offsets_resolve_to_the_chunk_text(
    corpus: tuple[CorpusPaper, ...],
) -> None:
    """``document.text[chunk.start:chunk.end] == chunk.text`` — KGIS's invariant.

    The defect: passing `Section.start_char` straight through. That offset is
    the *heading's*, and `Section.content` is stripped, so the slice would
    include the heading and lose the trailing whitespace — a coordinate that
    parses cleanly and resolves to different text than the model actually read.
    Every downstream check on the fragment would still pass.
    """
    rows = _chunks(corpus)
    assert rows, "no chunks to check"
    for slug, text, chunk in rows:
        assert text[chunk.start : chunk.end] == chunk.text, (
            f"{slug} {chunk.fragment}: the coordinate does not resolve to the "
            f"chunk text"
        )


def test_every_fragment_speaks_the_spec_grammar(
    corpus: tuple[CorpusPaper, ...],
) -> None:
    """Spec §3.4, and specifically *not* KGIS's `chunk:N@chars:` grammar.

    The defect: a plain `kgis.Chunk` slipping into the chunker. Its fragment is
    well-formed, resolvable and completely different, and nothing downstream
    would notice — `parse_fragment` is the only thing in the system that reads
    the grammar back, and it is ours.
    """
    rows = _chunks(corpus)
    assert rows
    for slug, _text, chunk in rows:
        parsed = parse_fragment(chunk.fragment)
        assert parsed.start == chunk.start and parsed.end == chunk.end, slug
        assert parsed.section_slug == chunk.section_slug, slug


def test_fragments_are_unique_within_a_paper(corpus: tuple[CorpusPaper, ...]) -> None:
    """Two chunks of one paper never share a coordinate.

    They can share a *slug*: `kg_validation_hitl` segments into three separate
    `methods` sections. The char span is what keeps them distinct, and without
    it their evidence ids would collide and two passages would become one
    evidence row.
    """
    by_paper: dict[str, list[str]] = {}
    for slug, _text, chunk in _chunks(corpus):
        by_paper.setdefault(slug, []).append(chunk.fragment)
    assert by_paper
    for slug, fragments in by_paper.items():
        assert len(fragments) == len(set(fragments)), f"{slug} has duplicate fragments"


def test_page_is_absent_rather_than_invented(corpus: tuple[CorpusPaper, ...]) -> None:
    """`#page:` never appears, because the page number is genuinely unknown.

    `ExtractedText.full_text` joins pages with a blank line and keeps no
    page-to-offset table, so by the time text reaches the segmenter the page is
    gone. §3.4 makes `#page:` optional for exactly this case. A fabricated page
    number would be worse than a missing one and no consumer could tell.
    """
    rows = _chunks(corpus)
    assert rows
    assert all(parse_fragment(c.fragment).page is None for _s, _t, c in rows)


def test_content_span_searches_only_the_section_window() -> None:
    """A repeated body must not resolve to another section's copy of itself.

    The defect: an unbounded `text.find(section.content)`. A short body — a
    one-line abstract, a run-in heading's text — can occur verbatim elsewhere,
    and an unbounded search returns the *first* occurrence. The resulting
    fragment is well-formed and points at the wrong passage, which is the worst
    available failure: silent and invisible downstream.
    """
    body = "Short repeated body."
    text = f"{body}\n\n1. Introduction\n{body}\n"
    section = Section(
        section_type=SectionType.INTRODUCTION,
        title="1. Introduction",
        content=body,
        start_char=text.index("1. Introduction"),
        end_char=len(text),
    )
    span = content_span(text, section)
    assert span is not None
    start, end = span
    assert text[start:end] == body
    assert start > section.start_char, (
        "the span resolved to the copy *before* the section, which an "
        "unbounded search would return"
    )


def test_content_span_returns_none_rather_than_zero_when_unlocatable() -> None:
    """Absent, not `(0, 0)`.

    A `(0, 0)` would be a coordinate pointing at the top of the document for
    every unlocatable section — indistinguishable, downstream, from a real one.
    """
    section = Section(
        section_type=SectionType.METHODS,
        title="Methods",
        content="text that is not in the document",
        start_char=0,
        end_char=10,
    )
    assert content_span("something else entirely", section) is None


def test_locator_carries_the_doi(corpus: tuple[CorpusPaper, ...]) -> None:
    """The locator is the only channel from document to builder.

    `PaperScopedKeyBuilder` derives `K`'s `doi` component from it, so a locator
    that lost the DOI would produce `Problem` keys scoped to nothing — and they
    would collide across every paper in the corpus.
    """
    assert corpus
    for paper in corpus:
        document = paper.to_document()
        assert doi_from_locator(document.locator) == paper.doi


@pytest.mark.parametrize("bad", ["file:///tmp/x.pdf", "paper://doi/", "", "10.1/a"])
def test_doi_from_locator_refuses_a_non_paper_locator(bad: str) -> None:
    with pytest.raises(ValueError):
        doi_from_locator(bad)
