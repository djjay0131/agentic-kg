"""``INGEST_MODE`` dispatch in the Cloud Run Job entrypoint.

The default remains the legacy ingest. Only an explicit ``kgis_kgcs`` selects
the migration path, and it refuses before importing the optional pipeline when
its flags are off — so these tests need no migration extra.
"""

from __future__ import annotations

import pytest
from agentic_kg.job_runner import _env_list, main
from agentic_kg.migration.config import reset_migration_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("INGEST_MODE", raising=False)
    monkeypatch.delenv("INGEST_QUERY", raising=False)
    for name in ("KGIS_INGESTION_ENABLED", "KGCS_RESOLUTION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    reset_migration_config()
    yield
    reset_migration_config()


def test_env_list_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("INGEST_SLUGS", " cskg , empire ,")
    assert _env_list("INGEST_SLUGS") == ["cskg", "empire"]
    assert _env_list("MISSING") == []


def test_unknown_mode_exits_2(monkeypatch):
    monkeypatch.setenv("INGEST_MODE", "not-a-mode")
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2


def test_kgis_kgcs_without_flags_exits_2(monkeypatch):
    monkeypatch.setenv("INGEST_MODE", "kgis_kgcs")
    monkeypatch.setenv("INGEST_QUERY", "ignored-in-migration-mode")
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2


def test_default_mode_is_legacy_and_still_requires_a_query():
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
