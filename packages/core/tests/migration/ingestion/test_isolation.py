""""Shadow only" as a structural property, not a promise in a docstring.

ADR-0003 decision 1: the candidate ledger is written by applications through
`CandidateSink.submit()`; the canonical graph is written by the KGCS
`PlanExecutor` alone. A shadow-ingestion path that *could* reach the canonical
graph is a shadow path in name only, and the difference is invisible in a
passing test run — the code that would do it simply never executes.

So the check is on what this subpackage can reach at all. It is run through
`astscan`, and — this is the part that was missing — the matcher is itself
tested against every evasion a reviewer found, because "assert nothing matched"
passes just as convincingly when the matcher is broken.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agentic_kg.migration.ingestion as ingestion_package
import pytest
from agentic_kg.migration.ingestion import ShadowStores
from agentic_kg.migration.ingestion.replay import (
    LIVE_PROVIDER_ENV,
    assert_no_live_provider,
)
from agentic_kg.migration.ingestion.stores import EVIDENCE_FILENAME, LEDGER_FILENAME

from .astscan import (
    imported_names,
    imported_symbols,
    matches_prefix,
    opaque_dynamic_imports,
    package_modules,
    parse,
)

PACKAGE_DIR = Path(ingestion_package.__file__).resolve().parent

#: Import roots that would give this subpackage a route to the production or
#: canonical graph. `agentic_kg.extraction` is deliberately absent: the
#: segmenter lives there and reusing it is the point.
FORBIDDEN_IMPORT_PREFIXES = (
    "neo4j",
    "agentic_kg.knowledge_graph",
    "agentic_kg.migration.neo4j",
    "agentic_kg.repository",
)

#: Canonical-write surfaces. Importing one by name is what ADR-0003 rule 1
#: forbids, independently of which module it came from.
CANONICAL_WRITE_SURFACES = frozenset(
    {
        "GraphMutationStore",
        "GraphMutationBatch",
        "PlanExecutor",
        "Neo4jCanonicalGraphStore",
    }
)

#: Every evasion an independent review found against the first version of this
#: check, plus the two forms it did catch. Fed to the real matcher below.
#:
#: The fourth was the dangerous one and the least clever: a file in a
#: subdirectory, missed because the scan used `glob` rather than `rglob`. It is
#: covered by `test_the_scan_reaches_subdirectories` rather than by a source
#: sample, since it is a property of file discovery, not of parsing.
EVASION_SAMPLES: tuple[tuple[str, str], ...] = (
    ("plain import", "import neo4j\n"),
    ("aliased import", "import neo4j as _n\n"),
    (
        "from-import of the adapter module",
        "from agentic_kg.migration.neo4j import Neo4jCanonicalGraphStore\n",
    ),
    (
        "package-relative from-import (a live store handle went through this)",
        "from agentic_kg.migration import neo4j\n",
    ),
    (
        "aliased package-relative from-import",
        "from agentic_kg.migration import neo4j as _adapter\n",
    ),
    ("importlib", "import importlib\nx = importlib.import_module('neo4j')\n"),
    ("dunder import", "x = __import__('neo4j')\n"),
    ("from-import of a driver symbol", "from neo4j import GraphDatabase\n"),
)


def _forbidden_hits(tree: ast.AST) -> list[str]:
    return [
        f"line {lineno}: {name}"
        for lineno, name in imported_names(tree)
        if matches_prefix(name, FORBIDDEN_IMPORT_PREFIXES)
    ]


def test_the_package_has_modules_to_check() -> None:
    """§9.0 obligation 1 for everything below."""
    modules = package_modules(PACKAGE_DIR)
    assert len(modules) >= 9, [p.name for p in modules]


def test_the_scan_reaches_subdirectories(tmp_path: Path) -> None:
    """`rglob`, not `glob`.

    The defect: a non-recursive scan. It needs no cleverness at all to evade —
    a contributor adding a subpackage silently loses isolation checking and is
    told nothing. Proven on a real directory tree rather than by reading the
    call, because `glob` and `rglob` differ only in a character.
    """
    (tmp_path / "top.py").write_text("x = 1\n", encoding="utf-8")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "hidden.py").write_text("import neo4j\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "stale.py").write_text("import neo4j\n", encoding="utf-8")

    found = {p.name for p in package_modules(tmp_path)}
    assert found == {"top.py", "hidden.py"}, found


@pytest.mark.parametrize("label,source", EVASION_SAMPLES, ids=[s[0] for s in EVASION_SAMPLES])
def test_the_matcher_catches_every_known_evasion(label: str, source: str) -> None:
    """Obligation 5 for the two "assert nothing matched" tests below.

    Four of these six forms passed the original matcher with all nine tests
    green, and one of them yielded a live `Neo4jCanonicalGraphStore` handle. A
    negative assertion is only worth what its matcher discriminates, so the
    matcher is exercised on each form here.
    """
    assert _forbidden_hits(parse_source(source)), f"{label} evaded the matcher"


def test_the_matcher_does_not_flag_what_this_package_legitimately_uses() -> None:
    """...and the companion, without which a matcher returning True always passes.

    `agentic_kg.extraction` is the segmenter — reusing it is the design — and
    `kgis` is the whole point. Flagging either would make the tests above
    unfalsifiable in the other direction.
    """
    benign = (
        "from agentic_kg.extraction.section_segmenter import SectionSegmenter\n"
        "from kgis.extraction.runner import ExtractionPipeline\n"
        "import yaml\n"
    )
    assert not _forbidden_hits(parse_source(benign))


def test_no_module_can_reach_the_canonical_or_production_graph() -> None:
    offenders: list[str] = []
    for path in package_modules(PACKAGE_DIR):
        for hit in _forbidden_hits(parse(path)):
            offenders.append(f"{path.name} {hit}")
    assert not offenders, (
        f"the shadow ingestion path can reach a canonical/production graph: {offenders}"
    )


def test_no_module_names_a_canonical_write_surface() -> None:
    """Not even by name. Holding one is what ADR-0003 rule 1 forbids."""
    offenders: list[str] = []
    for path in package_modules(PACKAGE_DIR):
        for lineno, module, symbol in imported_symbols(parse(path)):
            if symbol in CANONICAL_WRITE_SURFACES:
                offenders.append(f"{path.name}:{lineno} {module}.{symbol}")
    assert not offenders, f"canonical write surfaces imported: {offenders}"


def test_the_write_surface_matcher_catches_a_real_import() -> None:
    sample = (
        "from kg_contracts.stores import GraphMutationStore, LedgerEntry\n"
        "from agentic_kg.migration.neo4j.store import Neo4jCanonicalGraphStore\n"
    )
    hits = {
        symbol
        for _l, _m, symbol in imported_symbols(parse_source(sample))
        if symbol in CANONICAL_WRITE_SURFACES
    }
    assert hits == {"GraphMutationStore", "Neo4jCanonicalGraphStore"}


def test_no_module_hides_an_import_behind_a_computed_name() -> None:
    """A dynamic import the scan cannot resolve is a hole, not an all-clear.

    `import_module(some_variable)` is invisible to every check above. There is
    none today; failing on the first one means it has to be argued for rather
    than quietly exempting itself.
    """
    offenders: list[str] = []
    for path in package_modules(PACKAGE_DIR):
        for lineno in opaque_dynamic_imports(parse(path)):
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        f"dynamic import with a non-literal target — the scan cannot see where "
        f"these go: {offenders}"
    )


def test_the_opaque_import_check_would_notice_one() -> None:
    sample = "import importlib\nname = 'neo' + '4j'\nx = importlib.import_module(name)\n"
    assert opaque_dynamic_imports(parse_source(sample)) == [3]
    assert opaque_dynamic_imports(parse_source("import os\n")) == []


def parse_source(source: str) -> ast.Module:
    return ast.parse(source)


def test_in_memory_stores_persist_nothing() -> None:
    stores = ShadowStores.in_memory()
    try:
        assert stores.root is None
        assert stores.persists is False
    finally:
        stores.close()


def test_persistent_stores_live_only_under_the_given_root(tmp_path: Path) -> None:
    """Two files, both under the caller's directory, and nothing else.

    The defect: a store quietly defaulting to a path outside the run's own
    directory — a repo-relative `ledger.db`, a home-directory cache — would be
    shared between runs and between branches, and a shadow run would accumulate
    another run's candidates without anything looking wrong.
    """
    root = tmp_path / "shadow"
    stores = ShadowStores.at(root)
    try:
        assert stores.root == root
        assert stores.persists
    finally:
        stores.close()
    written = {p.name for p in root.iterdir()}
    assert LEDGER_FILENAME in written and EVIDENCE_FILENAME in written
    unexpected = {
        name
        for name in written
        if not name.startswith((LEDGER_FILENAME, EVIDENCE_FILENAME))
    }
    assert not unexpected, unexpected


def test_the_deployment_limitation_is_recorded() -> None:
    """The SQLite/Cloud Run concern, stated where an operator will read it."""
    warning = ShadowStores.deployment_warning()
    assert "SQLite" in warning
    assert "Cloud Run" in warning
    assert "ADR-0012" in warning


def test_no_live_provider_credential_reaches_this_suite() -> None:
    assert LIVE_PROVIDER_ENV
    assert_no_live_provider()


def test_the_provider_check_would_notice_a_credential() -> None:
    with pytest.raises(AssertionError, match="OPENAI_API_KEY"):
        assert_no_live_provider({"OPENAI_API_KEY": "sk-not-a-real-key"})
