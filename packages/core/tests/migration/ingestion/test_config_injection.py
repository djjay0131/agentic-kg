"""ADR-0004 decisions 3-5 and AC-15c, enforced structurally.

Three properties, each checked by walking this subpackage's AST rather than by
reading it:

1. no module calls `get_migration_config()` — the config is **injected**;
2. only `_contracts.py` imports the optional packages — one door;
3. in `_contracts.py`, the guard call precedes the first optional import —
   a guard that runs after the thing it guards is decoration.

An AST walk rather than a `grep` because a grep over source text cannot tell an
import from the word appearing in a docstring, and this module's own prose names
every symbol it forbids.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agentic_kg.migration.ingestion as ingestion_package
import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.ingestion import ShadowStores
from agentic_kg.migration.ingestion.pipeline import (
    ShadowIngestionDisabled,
    build_shadow_pipeline,
)

PACKAGE_DIR = Path(ingestion_package.__file__).resolve().parent

#: The module allowed to import the optional packages. One door, and it is
#: guarded; a guard in `__init__.py` alone is bypassed by importing a submodule.
GUARD_MODULE = "_contracts.py"

OPTIONAL_ROOTS = frozenset({"kg_contracts", "kgis", "kg_eval", "kgcs"})


def _modules() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_package_has_modules_to_check() -> None:
    """§9.0 obligation 1 — the set every other test here quantifies over.

    A wrong `PACKAGE_DIR` would empty it and turn all three AST tests green.
    """
    modules = _modules()
    assert len(modules) >= 8, f"only found {[p.name for p in modules]}"
    assert any(p.name == GUARD_MODULE for p in modules)


def test_no_module_calls_the_config_singleton() -> None:
    """AC-15c: downstream code takes an injected `MigrationConfig`.

    The defect: `get_migration_config()` read inline. It works, and it makes
    the flag a process-global that a test can only set through the environment
    — so a later test asserting "the injected config wins" would be unable to
    tell the two apart.
    """
    offenders: list[str] = []
    for path in _modules():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "get_migration_config":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"get_migration_config() called inline at {offenders}; ADR-0004 "
        f"decision 4 requires an injected MigrationConfig"
    )


def test_only_the_guard_module_imports_the_optional_packages() -> None:
    offenders: list[str] = []
    for path in _modules():
        if path.name == GUARD_MODULE:
            continue
        for node in ast.walk(_tree(path)):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in OPTIONAL_ROOTS:
                    offenders.append(f"{path.name}:{node.lineno} imports {root}")
    assert not offenders, (
        f"optional packages imported outside {GUARD_MODULE}: {offenders}. "
        f"Every access must go through the guard, or an operator without the "
        f"extra gets a bare ModuleNotFoundError from deep in the pipeline."
    )


def test_the_guard_module_actually_guards() -> None:
    """The `require_*` calls precede the first optional import in `_contracts`.

    The defect this catches is subtle and would never show up in a passing
    install: move the guard below the imports and it still *runs*, but the
    `ModuleNotFoundError` fires first and the actionable message is never
    reached. Only an install missing the extra would reveal it — which is
    exactly the install that has no CI job.
    """
    path = PACKAGE_DIR / GUARD_MODULE
    tree = _tree(path)
    guard_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("require_")
    ]
    import_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[0] in OPTIONAL_ROOTS
    ]
    assert guard_lines, f"{GUARD_MODULE} makes no require_* call"
    assert import_lines, f"{GUARD_MODULE} imports none of {sorted(OPTIONAL_ROOTS)}"
    assert max(guard_lines) < min(import_lines), (
        f"the guard (lines {guard_lines}) does not precede the first optional "
        f"import (line {min(import_lines)})"
    )


def test_the_default_config_refuses_to_build_a_run(
    corpus: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default-off means no run happens, and says so.

    Raising rather than returning an empty result: zero candidates would be
    read as "the pipeline ran and found nothing", which is the opposite of
    "the pipeline did not run".
    """
    monkeypatch.delenv("KGIS_INGESTION_ENABLED", raising=False)
    stores = ShadowStores.in_memory()
    try:
        with pytest.raises(ShadowIngestionDisabled) as excinfo:
            build_shadow_pipeline(
                list(corpus)[:1],  # type: ignore[arg-type]
                config=MigrationConfig(),
                client=_never_called(),
                stores=stores,
            )
        assert "KGIS_INGESTION_ENABLED" in str(excinfo.value)
    finally:
        stores.close()


def test_the_flag_check_reads_the_injected_config_not_the_environment(
    corpus: tuple[object, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the env var absent, an explicitly enabled config still builds a run.

    This is the assertion that distinguishes injection from a singleton read.
    Against code calling `get_migration_config()` the pipeline would refuse,
    because the environment says off — so this test is red for a
    non-injected implementation and green for an injected one, which is what
    obligation 5 asks of it.
    """
    monkeypatch.delenv("KGIS_INGESTION_ENABLED", raising=False)
    assert MigrationConfig().use_kgis_ingestion is False, (
        "the environment is not clean, so this test cannot distinguish an "
        "injected config from a singleton read"
    )
    stores = ShadowStores.in_memory()
    try:
        pipeline = build_shadow_pipeline(
            list(corpus)[:1],  # type: ignore[arg-type]
            config=MigrationConfig(use_kgis_ingestion=True),
            client=_never_called(),
            stores=stores,
        )
        assert pipeline.job_id
    finally:
        stores.close()


class _NeverCalled:
    """A `CompletionClient` that fails loudly if anything asks it to complete.

    Construction must be side-effect-free: `ExtractionPipeline`'s own docstring
    promises "constructing one touches no document and submits nothing", and
    these two tests rely on it. A client that raised only on a *network* call
    would let a regression here go unnoticed.
    """

    deterministic = True

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise AssertionError(
            "the completion client was called while only *building* a pipeline"
        )


def _never_called() -> _NeverCalled:
    return _NeverCalled()
