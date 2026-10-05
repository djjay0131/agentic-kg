"""Config is injected, never fetched from the process-global singleton.

ADR-0004 decision 4 / AC-15c. ``get_migration_config()`` is a cached
process-global: adequate for a per-deployment legacy/KGIS split, unable to
express running both paths in one process to diff them — which is precisely
what the three-way comparison needs. A module that reached for it would make
every downstream test depend on env-var ordering, and would quietly defeat the
comparison this subpackage exists to enable.
"""

from __future__ import annotations

import inspect

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import run_curation

from . import _scan


def test_no_module_calls_the_config_singleton() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _scan.modules():
        lines = _scan.calls_named(path.read_text(encoding="utf-8"), "get_migration_config")
        if lines:
            offenders[path.name] = lines
    assert offenders == {}, (
        f"these modules call the config singleton instead of taking an injected "
        f"MigrationConfig: {offenders}"
    )


def test_the_detector_catches_a_singleton_call() -> None:
    """The control: an empty offender dict is also what a broken matcher gives."""
    assert _scan.calls_named("c = get_migration_config()", "get_migration_config") == [1]
    assert _scan.calls_named(
        "from agentic_kg.migration import config\nc = config.get_migration_config()",
        "get_migration_config",
    ) == [2]


def test_the_entry_point_requires_a_config_argument() -> None:
    """``config`` is keyword-only and has no default.

    A default would let a caller omit it and silently pick up whatever the
    environment happened to say — the same failure as reading the singleton,
    one layer out.
    """
    parameter = inspect.signature(run_curation).parameters["config"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation == "MigrationConfig"


def test_an_injected_config_beats_the_environment(monkeypatch) -> None:
    """The flag is read from the argument, not from the process.

    With ``KGCS_RESOLUTION_ENABLED=1`` exported, an explicitly-disabled injected
    config must still refuse. A test that only checked the enabled direction
    would pass against code that ignored its argument entirely.
    """
    from agentic_kg.migration.curation import CurationDisabled

    monkeypatch.setenv("KGCS_RESOLUTION_ENABLED", "1")
    import pytest

    with pytest.raises(CurationDisabled):
        run_curation([], config=MigrationConfig(use_kgcs_resolution=False))
