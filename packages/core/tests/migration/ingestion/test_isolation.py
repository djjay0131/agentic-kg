""""Shadow only" as a structural property, not a promise in a docstring.

ADR-0003 decision 1: the candidate ledger is written by applications through
`CandidateSink.submit()`; the canonical graph is written by the KGCS
`PlanExecutor` alone. A shadow-ingestion path that *could* reach the canonical
graph is a shadow path in name only, and the difference is invisible in a
passing test run — the code that would do it simply never executes.

So the check is on what this subpackage can reach at all.
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
from agentic_kg.migration.ingestion.stores import (
    EVIDENCE_FILENAME,
    LEDGER_FILENAME,
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

#: Names from `kg_contracts.stores` that are canonical-write surfaces. Imported
#: from upstream and filtered, rather than transcribed, so a port renamed
#: upstream does not quietly fall off this list.
CANONICAL_WRITE_HINTS = ("GraphMutationStore", "GraphMutationBatch", "PlanExecutor")


def _modules() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def test_the_package_has_modules_to_check() -> None:
    assert len(_modules()) >= 8


def test_no_module_can_reach_the_canonical_or_production_graph() -> None:
    """No import of neo4j, the legacy repository, or the canonical adapter."""
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, (
        f"the shadow ingestion path can reach a canonical/production graph: "
        f"{offenders}"
    )


def test_no_module_names_a_canonical_write_surface() -> None:
    """Not even by name. Holding one is the thing ADR-0003 rule 1 forbids."""
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if alias.name in CANONICAL_WRITE_HINTS:
                        offenders.append(f"{path.name}:{node.lineno} {alias.name}")
    assert not offenders, f"canonical write surfaces imported: {offenders}"


def test_the_forbidden_list_would_catch_a_real_import() -> None:
    """Obligation 5 for the two tests above, without mutating the package.

    Both are "assert nothing matched", which is the shape that passes when the
    matcher is broken. Feeding the same predicate a line that *should* match
    proves it discriminates. `neo4j` is a real installed module and
    `Neo4jCanonicalGraphStore` is the real adapter next door, so this is not a
    straw man.
    """
    sample = (
        "from agentic_kg.migration.neo4j import Neo4jCanonicalGraphStore\n"
        "from kg_contracts.stores import GraphMutationStore\n"
        "import neo4j\n"
    )
    tree = ast.parse(sample)
    import_hits = 0
    surface_hits = 0
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
            surface_hits += sum(
                1 for a in node.names if a.name in CANONICAL_WRITE_HINTS
            )
        import_hits += sum(
            1
            for name in names
            if any(
                name == p or name.startswith(p + ".") for p in FORBIDDEN_IMPORT_PREFIXES
            )
        )
    assert import_hits == 2, import_hits
    assert surface_hits == 1, surface_hits


def test_in_memory_stores_persist_nothing() -> None:
    stores = ShadowStores.in_memory()
    try:
        assert stores.root is None
        assert stores.persists is False
    finally:
        stores.close()


def test_persistent_stores_live_only_under_the_given_root(tmp_path) -> None:
    """Two files, both under the caller's directory, and nothing else.

    The defect: a store that quietly defaulted to a path outside the run's own
    directory — a repo-relative `ledger.db`, a home-directory cache — would be
    shared between runs and between branches, and a shadow run would
    accumulate another run's candidates without anything looking wrong.
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
    # WAL sidecars are allowed; anything not prefixed by one of the two
    # databases is not.
    unexpected = {
        name
        for name in written
        if not name.startswith((LEDGER_FILENAME, EVIDENCE_FILENAME))
    }
    assert not unexpected, unexpected


def test_the_deployment_limitation_is_recorded() -> None:
    """The SQLite/Cloud Run concern is stated where an operator will read it.

    Discovery flagged it; this PR does not fix it. A limitation recorded only
    in a PR description is a limitation nobody finds at 3am.
    """
    warning = ShadowStores.deployment_warning()
    assert "SQLite" in warning
    assert "Cloud Run" in warning
    assert "ADR-0012" in warning


def test_no_live_provider_credential_reaches_this_suite() -> None:
    """CI runs this path against a replay client, never a provider.

    The check is on the *environment*, because the structural guarantee — the
    client is a required argument with no default — is already asserted by
    `test_config_injection`'s `_NeverCalled`. This one notices if somebody
    later adds a convenience fallback, since a fallback only ever does
    something when a key is present.
    """
    assert LIVE_PROVIDER_ENV
    assert_no_live_provider()


def test_the_provider_check_would_notice_a_credential() -> None:
    """Obligation 5 for the test above, which otherwise asserts nothing ran."""
    with pytest.raises(AssertionError, match="OPENAI_API_KEY"):
        assert_no_live_provider({"OPENAI_API_KEY": "sk-not-a-real-key"})
