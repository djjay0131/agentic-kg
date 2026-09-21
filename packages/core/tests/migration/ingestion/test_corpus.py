"""The committed corpus, and the one transcribed table in this subpackage.

`SLUG_TO_IMPORTER_FILE` is a filename-to-filename join. Everything a paper
*means* — its DOI, its title, its year — is read from the committed file at load
time, so there is no second copy to drift. The assertions here are about the
join being total in both directions, which is the only way it can go wrong.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.ingestion.corpus import (
    GRADED_SLUGS,
    SLUG_TO_IMPORTER_FILE,
    CorpusError,
    discovered_importer_files,
    discovered_text_slugs,
    load_corpus,
    load_paper,
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
