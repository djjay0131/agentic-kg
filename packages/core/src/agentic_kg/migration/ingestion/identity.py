"""`NORM`, the join key `K`, and the evidence-fragment grammar.

Three things the mapping spec says must exist exactly once, implemented here
exactly once. Nothing else in this subpackage restates them.

**`NORM` (spec §3.1).** NFKC -> rejoin a hyphen broken across a line ->
collapse whitespace -> casefold. **Punctuation is preserved.**

The spec is unusually emphatic that `NORM` has one definition, and the reason
is measured rather than stylistic: an earlier draft stated it twice, and the
two statements disagreed on 26 of 194 (13%) of the corpus's Concept/Model/
Method names — `fine-tuning`, `part-of-speech tagging`, `DeLong's test`,
`paraphrase-distilroberta-base-v2`. Because §3.3 mints a candidate's semantic
key with `NORM` and §6.4.1 computes the migration join with `NORM`, a 13%
disagreement silently loses 13% of the mapping inside the mechanism built to
prevent exactly that loss. "The join key and the identity key agree by
construction" is a fact only while there is one function.

Punctuation is preserved deliberately: hyphens and apostrophes are semantic in
this domain and stripping them merges distinct entities. The line-break rule is
narrow — it rejoins `paraphrase-\\ndistilroberta-base-v2`, the one corruption
the gold files actually document — and NFKC handles the ligature case
(`Classiﬁer`).

**`K` (spec §6.4.1).** `K = (entity_type, doi, surface, span)`, rendered as
`<doi>#<surface>#<span>` for the `paper_span` alias. `surface` is
`NORM(statement)` for a Problem and `NORM(canonical name)` otherwise; `span` is
a 16-byte SHA-256 prefix of `NORM(quoted_text)`. Both halves of "the join key
and the identity key agree by construction" call the same function here.

**The fragment grammar (spec §3.4).** `kg_contracts.Evidence` has no span,
offset or page field, and `SourceCoordinates.fragment` is a free-form string.
agentic-kg therefore owns the grammar *and its parser*; nothing in KGIS parses
it back.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

#: A hyphen at end-of-line followed by the continuation. Narrow on purpose: it
#: matches a hyphen immediately before a newline, never a hyphen inside a word
#: on one line, so `part-of-speech tagging` survives untouched.
_LINE_BREAK_HYPHEN = re.compile(r"-[ \t]*\r?\n[ \t]*")

_WHITESPACE = re.compile(r"\s+")

#: Length of the hex span digest. 16 hex chars = 64 bits; the spec calls it
#: "sha256-16" and the corpus measurement behind the 0-collision claim was run
#: at this width, so narrowing or widening it invalidates that measurement.
_SPAN_HEX_CHARS = 16


def norm(text: str) -> str:
    """`NORM(s)` of spec §3.1. The one normalization in the adoption.

    Order matters. NFKC first, so a ligature becomes its letters before any
    matching; the line-break rejoin next, while newlines still exist to mark
    where a break was; whitespace collapse after that, because the rejoin
    consumes the newline it acted on and the remaining runs are plain spacing;
    casefold last, because none of the earlier steps are case-sensitive and
    folding early would only obscure the input in a traceback.
    """
    folded = unicodedata.normalize("NFKC", text)
    rejoined = _LINE_BREAK_HYPHEN.sub("", folded)
    collapsed = _WHITESPACE.sub(" ", rejoined).strip()
    return collapsed.casefold()


def span_digest(quoted_text: str) -> str:
    """The `span` component of `K`: sha256-16 over `NORM(quoted_text)`.

    Hashing the *normalized* quote rather than the raw one is what makes the
    span survive the two corruptions the gold files document. A raw hash would
    treat `paraphrase-\\ndistilroberta-base-v2` and its rejoined form as two
    different spans, which is the silent mapping loss §6.4.1 exists to prevent.
    """
    return hashlib.sha256(norm(quoted_text).encode("utf-8")).hexdigest()[:_SPAN_HEX_CHARS]


def normalize_doi(doi: str) -> str:
    """Case-fold a DOI and strip the resolver prefixes people paste with it.

    DOIs are case-insensitive by specification, and the corpus carries them in
    at least two casings. Folding here rather than at each call site keeps the
    Paper alias and the `K` join agreeing for the same reason `NORM` is single.
    """
    text = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.casefold()


@dataclass(frozen=True)
class JoinKey:
    """`K = (entity_type, doi, surface, span)` — spec §6.4.1.

    All four parts are load-bearing and the spec gives the evidence for each:
    `entity_type` because one span in the corpus is cited by both a Model and a
    Method; `doi` because the same concept recurs across the citation chain by
    design; `surface` because 32 of 194 Concept/Model/Method entries share a
    span (one enumeration sentence evidencing many entities), which is the
    discriminator that takes the collision rate from 16% to 0; and `span`
    because it ties the key to evidence rather than to a string.
    """

    entity_type: str
    doi: str
    surface: str
    span: str

    @property
    def paper_span(self) -> str:
        """`K` rendered as the `paper_span` alias key: `<doi>#<surface>#<span>`."""
        return f"{self.doi}#{self.surface}#{self.span}"


def join_key(*, entity_type: str, doi: str, surface: str, quoted_text: str) -> JoinKey:
    """Build `K`. `surface` is normalized here, so callers pass it raw.

    Normalizing inside rather than asking callers to pre-normalize is the whole
    point: a caller who forgets is the 13%-disagreement failure, and there is
    no way for them to forget if they cannot pass a normalized value in.
    """
    return JoinKey(
        entity_type=entity_type,
        doi=normalize_doi(doi),
        surface=norm(surface),
        span=span_digest(quoted_text),
    )


# ---------------------------------------------------------------------------
# The evidence-fragment grammar (spec §3.4)
# ---------------------------------------------------------------------------
#
#   fragment := "sec:" <section-slug> "#chars:" <start> "-" <end> [ "#page:" <n> ]
#   example  := sec:introduction#chars:1024-1180#page:2

_FRAGMENT = re.compile(
    r"^sec:(?P<slug>[a-z0-9_]+)#chars:(?P<start>\d+)-(?P<end>\d+)(?:#page:(?P<page>\d+))?$"
)


@dataclass(frozen=True)
class Fragment:
    """A parsed §3.4 fragment.

    `start`/`end` are character offsets into the **extracted text** of the
    document, not into the PDF byte stream. `page` is optional and present only
    when PyMuPDF supplied it — for the committed text corpus it never is,
    because `ExtractedText.full_text` joins pages with a blank line and keeps
    no page-to-offset table (`extraction/pdf_extractor.py`). Absent rather
    than guessed: a fabricated page number is worse than a missing one.
    """

    section_slug: str
    start: int
    end: int
    page: int | None = None

    def render(self) -> str:
        base = f"sec:{self.section_slug}#chars:{self.start}-{self.end}"
        return base if self.page is None else f"{base}#page:{self.page}"


def parse_fragment(fragment: str) -> Fragment:
    """Parse a §3.4 fragment. Raises `ValueError` on anything else.

    Round-tripping is agentic-kg's job — KGIS writes the string into
    `SourceCoordinates.fragment` and `Evidence.source_locator` and never looks
    at it again. Raising rather than returning `None` keeps a malformed
    coordinate from being read as "no coordinate", which would let a projector
    silently drop the span it was supposed to carry.
    """
    match = _FRAGMENT.match(fragment)
    if match is None:
        raise ValueError(
            f"{fragment!r} is not a §3.4 evidence fragment "
            f"('sec:<slug>#chars:<start>-<end>[#page:<n>]')"
        )
    page = match.group("page")
    return Fragment(
        section_slug=match.group("slug"),
        start=int(match.group("start")),
        end=int(match.group("end")),
        page=None if page is None else int(page),
    )


__all__ = [
    "Fragment",
    "JoinKey",
    "join_key",
    "norm",
    "normalize_doi",
    "parse_fragment",
    "span_digest",
]
