""""Shadow only", checked on the source rather than taken on trust.

ADR-0003 decision 1: the candidate ledger is written by applications through
`CandidateSink.submit()`; the canonical graph is written by the KGCS
`PlanExecutor` alone. A shadow-ingestion path that *names* a canonical store is
one edit away from using it, and the difference is invisible in a passing test
run — the code that would do it simply never executes.

**What these tests establish, stated exactly.** No module in this subpackage
names a canonical or production-graph module, or a canonical write surface, in
its own source. That is a real and useful property. It is **not** the same as
the canonical store being unreachable, and an earlier version of this docstring
blurred the two. The scan is one-hop and syntactic: importing this package
already loads `neo4j` and `agentic_kg.knowledge_graph.*` into `sys.modules`
transitively through the `agentic_kg.extraction` segmenter import the design
deliberately allows. This catches accident and drift; it is not a sandbox.

The matcher itself is tested against every evasion two rounds of review found —
five so far — because "assert nothing matched" passes just as convincingly when
the matcher is broken as when the property holds.
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
    package_of,
    parse,
    unresolvable_relative_imports,
)

PACKAGE_DIR = Path(ingestion_package.__file__).resolve().parent
PACKAGE_NAME = ingestion_package.__name__

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
    # Found by a second review, in the fix for the four above. Every outward
    # relative form escaped, because `node.level` was never read.
    (
        "outward relative import (yielded a live store handle)",
        "from .. import neo4j as _n\n",
    ),
    ("outward relative submodule", "from ..neo4j import store\n"),
    (
        "outward relative symbol import",
        "from ..neo4j.store import Neo4jCanonicalGraphStore\n",
    ),
    ("two levels out", "from ... import knowledge_graph\n"),
    ("two levels out, with a module", "from ...knowledge_graph import auto_linker\n"),
)


#: The package the evasion samples are resolved against — this subpackage,
#: which is where a relative import in one of its modules would start from.
SAMPLE_PACKAGE = "agentic_kg.migration.ingestion"


def _forbidden_hits(tree: ast.AST, package: str = SAMPLE_PACKAGE) -> list[str]:
    return [
        f"line {lineno}: {name}"
        for lineno, name in imported_names(tree, package=package)
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
        package = package_of(path, PACKAGE_DIR, PACKAGE_NAME)
        for hit in _forbidden_hits(parse(path), package=package):
            offenders.append(f"{path.name} {hit}")
    assert not offenders, (
        f"the shadow ingestion path can reach a canonical/production graph: {offenders}"
    )


def test_no_module_names_a_canonical_write_surface() -> None:
    """Not even by name. Holding one is what ADR-0003 rule 1 forbids."""
    offenders: list[str] = []
    for path in package_modules(PACKAGE_DIR):
        package = package_of(path, PACKAGE_DIR, PACKAGE_NAME)
        for lineno, module, symbol in imported_symbols(parse(path), package=package):
            if symbol in CANONICAL_WRITE_SURFACES:
                offenders.append(f"{path.name}:{lineno} {module}.{symbol}")
    assert not offenders, f"canonical write surfaces imported: {offenders}"


def test_the_write_surface_matcher_catches_a_real_import() -> None:
    sample = (
        "from kg_contracts.stores import GraphMutationStore, LedgerEntry\n"
        "from agentic_kg.migration.neo4j.store import Neo4jCanonicalGraphStore\n"
        "from ..neo4j.store import PlanExecutor\n"  # the relative form too
    )
    hits = {
        symbol
        for _l, _m, symbol in imported_symbols(parse_source(sample), package=SAMPLE_PACKAGE)
        if symbol in CANONICAL_WRITE_SURFACES
    }
    assert hits == {"GraphMutationStore", "Neo4jCanonicalGraphStore", "PlanExecutor"}


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


def test_relative_imports_resolve_against_the_importing_package() -> None:
    """`node.level` arithmetic, checked directly rather than only through hits.

    The defect it catches is off-by-one in the dot count: resolving `..` as if
    it were `.` would map `from .. import neo4j` to
    `agentic_kg.migration.ingestion.neo4j`, which does not exist and does not
    match the forbidden prefix — a miss that looks exactly like a pass.
    """
    from .astscan import resolve_relative

    pkg = "agentic_kg.migration.ingestion"
    assert resolve_relative("documents", 1, pkg) == f"{pkg}.documents"
    assert resolve_relative(None, 2, pkg) == "agentic_kg.migration"
    assert resolve_relative("neo4j", 2, pkg) == "agentic_kg.migration.neo4j"
    assert resolve_relative("knowledge_graph", 3, pkg) == "agentic_kg.knowledge_graph"
    # Climbing past the root is unresolvable, not silently empty.
    assert resolve_relative("x", 9, pkg) is None


def test_package_of_matches_python_s_own_package_resolution() -> None:
    """`__init__.py` and a submodule share a package; a subdirectory does not."""
    root = PACKAGE_DIR
    assert package_of(root / "documents.py", root, PACKAGE_NAME) == PACKAGE_NAME
    assert package_of(root / "__init__.py", root, PACKAGE_NAME) == PACKAGE_NAME
    assert (
        package_of(root / "sub" / "mod.py", root, PACKAGE_NAME) == f"{PACKAGE_NAME}.sub"
    )


def test_no_module_hides_an_import_behind_an_unresolvable_relative_form() -> None:
    """A relative import that climbs past the root is a hole, not an all-clear."""
    offenders: list[str] = []
    for path in package_modules(PACKAGE_DIR):
        package = package_of(path, PACKAGE_DIR, PACKAGE_NAME)
        for lineno in unresolvable_relative_imports(parse(path), package):
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"unresolvable relative imports: {offenders}"


def test_the_unresolvable_relative_check_would_notice_one() -> None:
    assert unresolvable_relative_imports(
        parse_source("from ........ import something\n"), "a.b"
    ) == [1]
    assert unresolvable_relative_imports(
        parse_source("from .. import neo4j\n"), "agentic_kg.migration.ingestion"
    ) == []


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
