"""The committed corpus, and the one transcribed table in this subpackage.

`SLUG_TO_IMPORTER_FILE` is a filename-to-filename join. Everything a paper
*means* — its DOI, its title, its year — is read from the committed file at load
time, so there is no second copy to drift. The assertions here are about the
join being total in both directions, which is the only way it can go wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.ingestion.corpus import (
    GRADED_SLUGS,
    KGIS_CORPUS_DIR,
    SLUG_TO_IMPORTER_FILE,
    CorpusError,
    corpus_root,
    corpus_text_dir,
    discovered_importer_files,
    discovered_text_slugs,
    importer_output_dir,
    importer_path,
    load_corpus,
    load_paper,
    text_path,
)


def test_the_join_is_total_over_the_committed_text_files() -> None:
    """Every `paper_<slug>.txt` on disk has a mapping, and vice versa.

    Discovered on one side, declared on the other. Comparing the table against
    itself would pass whatever the corpus held; comparing it against a glob
    fails the moment a ninth paper lands without a row, or a row outlives the
    file it names.
    """
    discovered = discovered_text_slugs()
    assert discovered, "no paper_*.txt files found; the corpus path is wrong"
    assert discovered == set(SLUG_TO_IMPORTER_FILE), (
        f"text files and mapping disagree: "
        f"only on disk {sorted(discovered - set(SLUG_TO_IMPORTER_FILE))}, "
        f"only in table {sorted(set(SLUG_TO_IMPORTER_FILE) - discovered)}"
    )


def test_the_join_is_total_over_the_committed_importer_output() -> None:
    discovered = discovered_importer_files()
    assert discovered, "no importer-output *.yml files found"
    mapped = set(SLUG_TO_IMPORTER_FILE.values())
    assert discovered == mapped, (
        f"importer files and mapping disagree: "
        f"only on disk {sorted(discovered - mapped)}, "
        f"only in table {sorted(mapped - discovered)}"
    )


def test_every_paper_loads_with_an_identity_read_from_disk() -> None:
    papers = load_corpus()
    assert len(papers) == 8
    for paper in papers:
        assert paper.doi.startswith("10."), f"{paper.slug}: {paper.doi!r} is not a DOI"
        assert paper.title
        assert paper.text.strip(), f"{paper.slug} has empty text"


def test_dois_are_distinct() -> None:
    """Eight papers, eight DOIs.

    The defect: a copy-paste in the join table pointing two slugs at one
    importer file. Both papers would then carry the same DOI, every
    paper-scoped key would collide between them, and the candidate counts would
    still look plausible.
    """
    papers = load_corpus()
    dois = [paper.doi.casefold() for paper in papers]
    assert len(set(dois)) == len(dois), f"duplicate DOIs: {dois}"


def test_graded_slugs_are_in_the_corpus() -> None:
    """The two papers with reconciled gold exist and are a strict subset.

    Grading all eight against a two-paper gold set would make every entity from
    the other six a false positive.
    """
    assert GRADED_SLUGS
    assert GRADED_SLUGS < set(SLUG_TO_IMPORTER_FILE)


def test_an_unknown_slug_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(CorpusError):
        load_paper("not_a_paper")


# --------------------------------------------------------------------------
# Root resolution: the repo-relative default, and the KGIS_CORPUS_DIR override
# the Cloud Run Job image uses because it carries the corpus outside a checkout.
# --------------------------------------------------------------------------


def _write_fake_corpus(root: Path, slug: str = "cskg") -> None:
    """A minimal corpus at the repo-relative layout under ``root``.

    Only the one paper named, so a test that passes proves the override is read
    rather than the real checkout being found by accident.
    """
    text_dir = root / "packages/core/tests/extraction/fixtures/ground_truth_chain"
    importer_dir = root / "docs/ground-truth/importer-output"
    text_dir.mkdir(parents=True)
    importer_dir.mkdir(parents=True)
    (text_dir / f"paper_{slug}.txt").write_text("overridden body text", encoding="utf-8")
    (importer_dir / SLUG_TO_IMPORTER_FILE[slug]).write_text(
        "paper:\n"
        "  doi: 10.1234/overridden\n"
        "  title: Overridden Paper\n"
        "  year: 2024\n",
        encoding="utf-8",
    )


def test_corpus_root_defaults_to_the_repo_root() -> None:
    """Unset, the root is derived from this checkout, not from a configured path."""
    root = corpus_root({})
    assert (root / "packages/core/src/agentic_kg").is_dir()
    assert (root / "packages/core/tests/extraction/fixtures/ground_truth_chain").is_dir()


def test_corpus_root_reads_kgis_corpus_dir() -> None:
    assert corpus_root({KGIS_CORPUS_DIR: "/srv/corpus"}) == Path("/srv/corpus")


def test_paths_follow_the_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The resolution functions re-read the env; the module constants do not.

    Asserting against the functions (not the import-time constants) is the point:
    the Job sets ``KGIS_CORPUS_DIR`` before Python starts, but a test can only
    change it afterwards, so the lookup must be per-call.
    """
    monkeypatch.setenv(KGIS_CORPUS_DIR, str(tmp_path))
    assert corpus_text_dir() == (
        tmp_path / "packages/core/tests/extraction/fixtures/ground_truth_chain"
    )
    assert importer_output_dir() == tmp_path / "docs/ground-truth/importer-output"
    assert text_path("cskg") == (
        tmp_path / "packages/core/tests/extraction/fixtures/ground_truth_chain/paper_cskg.txt"
    )
    assert importer_path("cskg") == (
        tmp_path / "docs/ground-truth/importer-output" / SLUG_TO_IMPORTER_FILE["cskg"]
    )


def test_load_paper_reads_the_overridden_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point: a paper outside the checkout loads, identity and all."""
    _write_fake_corpus(tmp_path)
    monkeypatch.setenv(KGIS_CORPUS_DIR, str(tmp_path))
    paper = load_paper("cskg")
    assert paper.text == "overridden body text"
    assert paper.doi == "10.1234/overridden"
    assert paper.title == "Overridden Paper"
    assert paper.text_path == tmp_path / (
        "packages/core/tests/extraction/fixtures/ground_truth_chain/paper_cskg.txt"
    )


def test_a_missing_overridden_root_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An override with no corpus raises, naming the path under the override.

    Not a silently empty corpus: the same loud ``CorpusError`` the module has
    always raised, now pointing at the configured root so a mis-set
    ``KGIS_CORPUS_DIR`` is visible in the message.
    """
    monkeypatch.setenv(KGIS_CORPUS_DIR, str(tmp_path / "does-not-exist"))
    with pytest.raises(CorpusError) as excinfo:
        load_paper("cskg")
    assert str(tmp_path / "does-not-exist") in str(excinfo.value)

