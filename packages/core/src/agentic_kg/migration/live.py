"""Live, query-driven acquisition for the KGIS -> KGCS path.

The migration run's default source is the committed eight-paper *corpus*
(:mod:`agentic_kg.migration.ingestion.corpus`). This module adds the other
source: a free-text ``query`` and a ``limit``, resolved against the repo's
existing discovery clients into the *same source-record shape* the corpus uses,
so the KGIS -> KGCS pipeline downstream cannot tell the two apart.

Two seams, both injectable, because CI must never reach the network:

* a **discovery** callable ``(query, limit, sources) -> (papers, errors)`` which
  defaults to the real multi-source aggregator
  (:func:`agentic_kg.data_acquisition.aggregator.get_paper_aggregator`). This is
  the *same* acquisition layer the legacy ``ingest_papers`` path uses —
  Semantic Scholar / arXiv / OpenAlex clients, their rate limiting, their
  bounded retries and their API-key handling — reused rather than reimplemented.
* a **text fetcher** ``(paper) -> str | None`` which defaults to the repo's
  ``PDFExtractor`` (PyMuPDF). It reuses the production PDF acquisition layer and
  keeps no copy of it.

Dedupe happens *before* full-text fetch — the expensive, network-bound step — so
a paper already in the durable KGIS ledger or already canonical is never
re-downloaded. Duplicates are detected on every alias the record carries (DOI,
arXiv, Semantic Scholar, OpenAlex), which is what lets a paper discovered under
a different identifier be recognized as the same paper.

**Why there is no default live model client here.** The extraction arm needs a
``CompletionClient``; tests inject the deterministic replay client, and the
staging Job injects a live adapter. This module deliberately does *not* build a
live provider on its own — the same rule
:mod:`agentic_kg.migration.ingestion.pipeline` states: a default that reaches a
paid API is how a test suite ends up spending someone else's money.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentic_kg.migration.ingestion.documents import PaperDocument
from agentic_kg.migration.ingestion.identity import normalize_doi

logger = logging.getLogger(__name__)

#: Value of ``MIGRATION_SOURCE`` that replays the committed corpus (the
#: default: nothing changes until an operator opts in).
SOURCE_CORPUS = "corpus"
#: Value of ``MIGRATION_SOURCE`` that discovers papers live.
SOURCE_LIVE = "live"

#: The environment variable that selects the source.
ENV_MIGRATION_SOURCE = "MIGRATION_SOURCE"

#: Mirrors ``agentic_kg.ingestion.MIN_USABLE_CHARS``. Duplicated as a *default*
#: only, and overridable, so this module does not import the whole legacy
#: ingestion stack at import time just for one integer.
DEFAULT_MIN_TEXT_CHARS = 250

#: Namespaces whose identifiers are used, in order, to name a record and its
#: slug. The DOI is handled separately because it is mandatory on this path.
_ID_NAMESPACE_ORDER: tuple[str, ...] = ("arxiv", "semantic_scholar", "openalex")

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class LiveAcquisitionError(RuntimeError):
    """Live acquisition was requested without the configuration it needs."""


def _sanitize_slug(value: str) -> str:
    return _SLUG_UNSAFE.sub("_", value).strip("_") or "unknown"


def _canonical_ids(external_ids: Mapping[str, str] | None) -> str:
    ids = external_ids or {}
    return ";".join(f"{key}={ids[key]}" for key in sorted(ids))


def _slug_for(doi: str, external_ids: Mapping[str, str] | None) -> str:
    ids = external_ids or {}
    for namespace in _ID_NAMESPACE_ORDER:
        key = ids.get(namespace)
        if key and key.strip():
            return f"{namespace}-{_sanitize_slug(key.strip())}"
    return f"doi-{_sanitize_slug(normalize_doi(doi))}"


@dataclass(frozen=True)
class AcquiredPaper:
    """One paper discovered at run time, in the corpus source-record shape.

    Satisfies :class:`~agentic_kg.migration.ingestion.source_record.SourceRecord`
    structurally, so it flows through the identical KGIS pipeline a
    `CorpusPaper` does. The difference is provenance: where a corpus paper cites
    a committed file, this cites the discovery response — the API, the instant,
    the query and the cross-identifiers (spec §3.3; ADR-0004).
    """

    slug: str
    doi: str
    title: str
    year: int | None
    text: str
    source_api: str
    retrieved_at: datetime
    query: str
    external_ids: dict[str, str] = field(default_factory=dict)
    pdf_url: str | None = None

    def to_document(self) -> PaperDocument:
        return PaperDocument(
            slug=self.slug, doi=self.doi, title=self.title, text=self.text
        )

    # --- SourceRecord -------------------------------------------------------

    @property
    def record_source_type(self) -> str:
        return self.source_api

    @property
    def record_locator(self) -> str:
        return f"{self.source_api}:doi:{normalize_doi(self.doi)}"

    @property
    def record_fragment(self) -> str:
        return f"record@query={self.query}"

    @property
    def record_identity(self) -> str:
        return f"{self.source_api}:doi:{normalize_doi(self.doi)}"

    @property
    def evidence_content(self) -> str:
        ids = _canonical_ids(self.external_ids)
        return (
            f"{self.title} ({self.year}) doi:{self.doi} "
            f"source:{self.source_api} retrieved:{self.retrieved_at.isoformat()} "
            f"query:{self.query} ids:[{ids}]"
        )

    def evidence_hash_parts(self) -> tuple[str, ...]:
        return (
            self.title,
            str(self.year),
            self.doi,
            self.source_api,
            _canonical_ids(self.external_ids),
        )


# --- Injectable seams -------------------------------------------------------

#: ``(query, limit, sources) -> (papers, errors)``. ``papers`` is a sequence of
#: ``NormalizedPaper`` (duck-typed here to avoid importing the acquisition stack
#: at module load); ``errors`` maps a source name to a message.
Discovery = Callable[
    [str, int, "Sequence[str] | None"], "tuple[Sequence[object], Mapping[str, str]]"
]
#: ``(paper) -> extracted full text | None``.
TextFetcher = Callable[[object], "str | None"]


def default_discovery(
    query: str, limit: int, sources: Sequence[str] | None
) -> tuple[Sequence[object], Mapping[str, str]]:
    """The real discovery layer: the same aggregator the legacy path uses."""
    from agentic_kg.data_acquisition.aggregator import get_paper_aggregator

    aggregator = get_paper_aggregator()
    result = asyncio.run(
        aggregator.search_papers(
            query, sources=list(sources) if sources else None, limit=limit
        )
    )
    return result.papers, dict(result.errors or {})


def default_text_fetcher() -> TextFetcher:
    """The real full-text layer: the repo's production ``PDFExtractor``."""
    from agentic_kg.extraction.pdf_extractor import get_pdf_extractor

    extractor = get_pdf_extractor()

    def fetch(paper: object) -> str | None:
        urls = getattr(paper, "candidate_pdf_urls", None)
        candidates = urls() if callable(urls) else []
        for url in candidates:
            try:
                extracted = asyncio.run(extractor.extract_from_url(url))
            except Exception as exc:  # noqa: BLE001 - one bad host must not kill the run
                logger.warning("full-text fetch failed for %s: %s", url, exc)
                continue
            text = getattr(extracted, "full_text", "") or ""
            if len(text.strip()) >= DEFAULT_MIN_TEXT_CHARS:
                return text
            logger.warning("full text too thin for %s: %d chars", url, len(text))
        return None

    return fetch


@dataclass(frozen=True)
class AcquisitionResult:
    """What one live acquisition did, in a form the run summary can carry."""

    source: str
    query: str | None
    limit: int | None
    papers: tuple[AcquiredPaper, ...]
    papers_seen: int
    papers_new: int
    skipped_duplicate: int
    skipped_no_text: int
    skipped_no_doi: int
    errors: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "query": self.query,
            "limit": self.limit,
            "papers_seen": self.papers_seen,
            "papers_new": self.papers_new,
            "papers_skipped_duplicate": self.skipped_duplicate,
            "papers_skipped_no_text": self.skipped_no_text,
            "papers_skipped_no_doi": self.skipped_no_doi,
            "acquisition_errors": self.errors,
        }


def acquire_live_papers(
    *,
    query: str,
    limit: int,
    discovery: Discovery,
    text_fetcher: TextFetcher,
    ledger_aliases: "frozenset[str] | set[str]",
    canonical_lookup: Callable[[tuple[object, ...]], bool],
    retrieved_at: datetime,
    sources: Sequence[str] | None = None,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> AcquisitionResult:
    """Discover, dedupe and fetch full text for up to ``limit`` new papers.

    The per-run cap is ``limit``: discovery is asked for ``limit`` and the
    normalized result is truncated to ``limit`` after the sources are merged and
    deduplicated, so the cap is on papers this run will consider, not per
    source.

    A paper is skipped as a duplicate when *any* of its aliases is already in
    the ledger or already canonical. Dedupe precedes full-text fetch because the
    fetch is the network-bound step.
    """
    from agentic_kg.migration.ingestion.papers import aliases_for

    if limit < 1:
        raise LiveAcquisitionError(f"limit must be >= 1, got {limit}")

    discovered, errors = discovery(query, limit, sources)
    errors_by_source = {name: 1 for name in errors}

    papers: list[AcquiredPaper] = []
    seen = 0
    skipped_duplicate = 0
    skipped_no_text = 0
    skipped_no_doi = 0

    for raw in list(discovered)[:limit]:
        doi_raw = getattr(raw, "doi", None)
        doi = normalize_doi(doi_raw) if isinstance(doi_raw, str) and doi_raw.strip() else ""
        if not doi:
            skipped_no_doi += 1
            continue
        seen += 1
        external_ids = dict(getattr(raw, "external_ids", None) or {})
        refs = aliases_for(doi, external_ids)
        keys = {f"{ref.namespace}:{ref.key}" for ref in refs}
        if keys & set(ledger_aliases) or canonical_lookup(refs):
            skipped_duplicate += 1
            continue

        text = text_fetcher(raw)
        if not text or len(text.strip()) < min_text_chars:
            skipped_no_text += 1
            continue

        title = (getattr(raw, "title", "") or "").strip() or doi
        papers.append(
            AcquiredPaper(
                slug=_slug_for(doi, external_ids),
                doi=doi,
                title=title,
                year=getattr(raw, "year", None),
                text=text,
                source_api=getattr(raw, "source", None) or "unknown",
                retrieved_at=retrieved_at,
                query=query,
                external_ids=external_ids,
                pdf_url=getattr(raw, "pdf_url", None),
            )
        )

    return AcquisitionResult(
        source=SOURCE_LIVE,
        query=query,
        limit=limit,
        papers=tuple(papers),
        papers_seen=seen,
        papers_new=len(papers),
        skipped_duplicate=skipped_duplicate,
        skipped_no_text=skipped_no_text,
        skipped_no_doi=skipped_no_doi,
        errors=errors_by_source,
    )


class LiveOpenAICompletionClient:
    """A raw-text ``CompletionClient`` over this repo's OpenAI configuration.

    The KGIS extractors ask for a JSON document and parse it themselves, so this
    adapter returns the model's raw text rather than a structured object (the
    repo's ``OpenAIClient.extract`` is instructor-based and would double-parse).
    The model resolves through ``OPENAI_EXTRACTION_MODEL`` — the same lever the
    rest of the repo uses — and the key through ``OPENAI_API_KEY``. Marked
    non-deterministic, so the run's honest nulls drop the replay warning and the
    pipeline's dry-run refuses to claim its plan is a measurement.
    """

    deterministic = False

    def __init__(self, *, model: str | None = None, temperature: float = 0.0) -> None:
        self._model = model
        self._temperature = temperature

    def complete(self, prompt: str, *, system: str | None = None) -> str:  # pragma: no cover
        import os

        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001 - actionable, not a crash
            raise LiveAcquisitionError(
                f"the OpenAI SDK is unavailable: {exc}"
            ) from exc

        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise LiveAcquisitionError(
                "OPENAI_API_KEY is not set; the live KGIS extraction client "
                "needs it, or inject a deterministic client for a replay run"
            )
        model = self._model or os.getenv("OPENAI_EXTRACTION_MODEL") or "gpt-4o"
        client = AsyncOpenAI(api_key=key)
        # The OpenAI SDK's message param type is a union of TypedDicts; this
        # adapter is a staging-only path and the SDK is imported lazily, so the
        # list is deliberately opaque here.
        messages: list[Any] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async def _call() -> str:
            # One client per call, closed in the same event loop: the async
            # transport binds to the loop, and `complete` drives a fresh
            # `asyncio.run` each time, so a shared client would cross loops.
            try:
                response = await client.chat.completions.create(
                    model=model, messages=messages, temperature=self._temperature
                )
                return response.choices[0].message.content or ""
            finally:
                await client.close()

        try:
            return asyncio.run(_call())
        except Exception as exc:  # noqa: BLE001 - surface as a client failure
            raise LiveAcquisitionError(f"OpenAI completion failed: {exc}") from exc


__all__ = [
    "DEFAULT_MIN_TEXT_CHARS",
    "ENV_MIGRATION_SOURCE",
    "SOURCE_CORPUS",
    "SOURCE_LIVE",
    "AcquiredPaper",
    "AcquisitionResult",
    "LiveAcquisitionError",
    "LiveOpenAICompletionClient",
    "acquire_live_papers",
    "default_discovery",
    "default_text_fetcher",
]
