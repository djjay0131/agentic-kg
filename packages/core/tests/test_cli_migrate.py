"""CLI wiring for ``agentic-kg migrate run``.

The refusal paths run without the opt-in ``migration`` extra: flag validation
happens before the pipeline is imported, which is the property that lets a
default install give an actionable error instead of an ImportError.
"""

from __future__ import annotations

import pytest
from agentic_kg.cli import build_parser, main
from agentic_kg.migration.config import reset_migration_config


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    for name in ("KGIS_INGESTION_ENABLED", "KGCS_RESOLUTION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    reset_migration_config()
    yield
    reset_migration_config()


def test_migrate_run_is_parsed():
    args = build_parser().parse_args(["migrate", "run", "--dois", "10.1/a", "10.1/b"])
    assert args.command == "migrate"
    assert args.migrate_command == "run"
    assert args.dois == ["10.1/a", "10.1/b"]


def test_migrate_without_a_subcommand_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["migrate"])
    assert excinfo.value.code == 2
    assert "subcommand" in capsys.readouterr().err


def test_run_refuses_when_both_flags_are_off(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["migrate", "run"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "KGIS_INGESTION_ENABLED" in err
    assert "KGCS_RESOLUTION_ENABLED" in err


def test_run_names_only_the_missing_flag(monkeypatch, capsys):
    monkeypatch.setenv("KGIS_INGESTION_ENABLED", "1")
    reset_migration_config()
    with pytest.raises(SystemExit) as excinfo:
        main(["migrate", "run"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "KGCS_RESOLUTION_ENABLED" in err
    assert "KGIS_INGESTION_ENABLED" not in err
