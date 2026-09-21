"""The committed corpus the shadow path runs over.

Two committed directories, joined by one table:

* ``packages/core/tests/extraction/fixtures/ground_truth_chain/paper_<slug>.txt``
  — the paper text. Eight files, hand-verified, produced from the (gitignored)
  PDFs by ``scripts/segment_ground_truth.py``.
* ``docs/ground-truth/importer-output/<n>-<name>.yml`` — the recorded output of
  the existing importer, whose ``paper:`` block carries each paper's DOI, title
  and year.

:data:`SLUG_TO_IMPORTER_FILE` is the join, and it is deliberately the *only*
transcribed thing here: a filename-to-filename mapping, carrying no semantic
content. Every fact about a paper — its DOI, its title, its year — is **read
from the committed file at load time**, never copied into this module.

That distinction is the point. A transcribed DOI table is a second definition
of a fact that already exists in the repo, and a second definition is a thing
that can drift from the first without either being wrong on its own — the
failure §9.0 catalogues as "assert an identity between two things defined
separately, which have drifted". Reading through is not tidiness; it is the
only version of this module that cannot silently disagree with the corpus.

``test_corpus.py`` asserts the join is total in both directions — eight text
files, eight importer files, every mapping resolving — so deleting a row or
adding a ninth paper without a row turns it red rather than shrinking the
corpus quietly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from agentic_kg.migration.ingestion.documents import PaperDocument

#: Repo root. `parents[0]` is this file's directory (`ingestion/`), so the root
#: — six levels up past `migration`, `agentic_kg`, `src`, `core`, `packages` —
#: is `parents[6]`. Derived rather than configured so the corpus is findable
#: from a test or a script without an env var.
#:
#: This resolves correctly only for a source or editable install, which is what
#: the migration CI job uses (`pip install -e`). On a non-editable install the
#: corpus is simply absent and :func:`load_paper` raises `CorpusError` naming
#: the missing path — a loud failure, not a silently empty corpus.
_REPO_ROOT = Path(__file__).resolve().parents[6]

CORPUS_TEXT_DIR = _REPO_ROOT / "packages/core/tests/extraction/fixtures/ground_truth_chain"
IMPORTER_OUTPUT_DIR = _REPO_ROOT / "docs/ground-truth/importer-output"

#: slug -> the importer-output file carrying that paper's identity block.
#:
#: Pure plumbing: the two committed directories name the same eight papers with
#: two different conventions (`paper_cskg.txt` vs `1-cs-kg.yml`) and nothing in
#: either file relates them. This table is that relation and nothing else — no
#: DOI, no title, no year appears here.
SLUG_TO_IMPORTER_FILE: dict[str, str] = {
    "cskg": "1-cs-kg.yml",
    "cskg2": "2-cs-kg-2-0.yml",
    "kg_construction_survey": "3-construction-of-kgs.yml",
    "llm_ontology_gen": "4-llms-for-scholarly-ontology-generation.yml",
    "fact_completion": "5-completing-scientific-facts.yml",
    "kg_validation_hitl": "6-kg-validation-hitl.yml",
    "hypothesis_generation": "7-research-hypothesis-generation.yml",
    "empire": "8-divide-and-conquer-the-empire.yml",
}

#: The two papers with a reconciled answer key. Metrics are graded over these
#: and only these — grading all eight against a two-paper gold set would make
#: every entity from the other six a false positive.
GRADED_SLUGS = frozenset({"cskg", "cskg2"})


class CorpusError(RuntimeError):
    """The committed corpus is not in the shape this module requires."""


@dataclass(frozen=True)
class CorpusPaper:
    """One paper of the committed corpus, with its identity read from disk."""

    slug: str
    doi: str
    title: str
    year: int | None
    text_path: Path
    importer_path: Path
    text: str

    def to_document(self) -> PaperDocument:
        return PaperDocument(
            slug=self.slug, doi=self.doi, title=self.title, text=self.text
        )


def text_path(slug: str) -> Path:
    return CORPUS_TEXT_DIR / f"paper_{slug}.txt"


def importer_path(slug: str) -> Path:
    try:
        filename = SLUG_TO_IMPORTER_FILE[slug]
    except KeyError:
        raise CorpusError(
            f"{slug!r} has no importer-output mapping; known slugs: "
            f"{', '.join(sorted(SLUG_TO_IMPORTER_FILE))}"
        ) from None
    return IMPORTER_OUTPUT_DIR / filename


def _read_identity(path: Path) -> tuple[str, str, int | None]:
    """`(doi, title, year)` from an importer-output file's `paper:` block."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CorpusError(f"{path} is not a YAML mapping")
    paper = payload.get("paper")
    if not isinstance(paper, dict):
        raise CorpusError(f"{path} has no 'paper:' block")
    doi = paper.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        raise CorpusError(f"{path} has no usable 'paper.doi'")
    title = paper.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CorpusError(f"{path} has no usable 'paper.title'")
    year = paper.get("year")
    return doi.strip(), title.strip(), year if isinstance(year, int) else None


def load_paper(slug: str) -> CorpusPaper:
    """Load one corpus paper. Raises `CorpusError` on anything unexpected."""
    tpath = text_path(slug)
    ipath = importer_path(slug)
    if not tpath.is_file():
        raise CorpusError(f"corpus text missing for {slug!r}: {tpath}")
    if not ipath.is_file():
        raise CorpusError(f"importer output missing for {slug!r}: {ipath}")
    doi, title, year = _read_identity(ipath)
    return CorpusPaper(
        slug=slug,
        doi=doi,
        title=title,
        year=year,
        text_path=tpath,
        importer_path=ipath,
        text=tpath.read_text(encoding="utf-8"),
    )


def load_corpus(slugs: tuple[str, ...] | None = None) -> tuple[CorpusPaper, ...]:
    """Load the corpus in a stable order.

    Sorted by slug rather than by the importer files' numeric prefix: the order
    a run sees decides candidate ordering and intra-run duplicate suppression
    (first key wins), so it must not depend on a filename convention that could
    be renumbered.
    """
    wanted = tuple(sorted(SLUG_TO_IMPORTER_FILE)) if slugs is None else tuple(slugs)
    return tuple(load_paper(slug) for slug in wanted)


def discovered_text_slugs() -> frozenset[str]:
    """Slugs discovered on disk, from the text files themselves.

    Used by the totality test, which compares this against
    :data:`SLUG_TO_IMPORTER_FILE`'s keys. Discovering one side and declaring the
    other is what makes that test able to fail: comparing the table to itself
    would pass whatever the corpus contained.
    """
    return frozenset(
        path.name[len("paper_") : -len(".txt")]
        for path in CORPUS_TEXT_DIR.glob("paper_*.txt")
    )


def discovered_importer_files() -> frozenset[str]:
    """Importer-output filenames discovered on disk."""
    return frozenset(path.name for path in IMPORTER_OUTPUT_DIR.glob("*.yml"))


__all__ = [
    "CORPUS_TEXT_DIR",
    "GRADED_SLUGS",
    "IMPORTER_OUTPUT_DIR",
    "SLUG_TO_IMPORTER_FILE",
    "CorpusError",
    "CorpusPaper",
    "discovered_importer_files",
    "discovered_text_slugs",
    "importer_path",
    "load_corpus",
    "load_paper",
    "text_path",
]
