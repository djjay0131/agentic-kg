"""Schema and validation for ``config/ingest-queries.yaml``."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.pipeline.catalog import (
    ENV_CATALOG,
    QueryCatalog,
    QuerySpec,
    default_catalog_path,
    load_catalog,
)

# The test file lives in the checkout even when the package is installed
# non-editable, so point at the shipped catalog explicitly rather than relying
# on default_catalog_path (which resolves relative to the installed package).
SHIPPED_CATALOG = Path(__file__).resolve().parents[4] / "config" / "ingest-queries.yaml"


def test_shipped_catalog_is_valid_and_seeded():
    catalog = load_catalog(SHIPPED_CATALOG)
    assert 6 <= len(catalog.queries) <= 10
    ids = [spec.id for spec in catalog.queries]
    assert len(ids) == len(set(ids))
    # Every shipped query is enabled by default.
    assert len(catalog.enabled) == len(catalog.queries)
    for spec in catalog.queries:
        assert 1 <= spec.limit <= 50
        assert spec.weight > 0


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        QuerySpec(id="a", query="q", topic="t", limit=1, surprise=True)


def test_limit_bounds_are_enforced():
    with pytest.raises(ValueError):
        QuerySpec(id="a", query="q", topic="t", limit=0)
    with pytest.raises(ValueError):
        QuerySpec(id="a", query="q", topic="t", limit=51)


def test_id_pattern_is_enforced():
    with pytest.raises(ValueError):
        QuerySpec(id="Has Spaces", query="q", topic="t", limit=1)
    with pytest.raises(ValueError):
        QuerySpec(id="UPPER", query="q", topic="t", limit=1)


def test_weight_must_be_positive():
    with pytest.raises(ValueError):
        QuerySpec(id="a", query="q", topic="t", limit=1, weight=0)


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate query id"):
        QueryCatalog(
            queries=[
                {"id": "a", "query": "q", "topic": "t", "limit": 1},
                {"id": "a", "query": "q2", "topic": "t", "limit": 1},
            ]
        )


def test_at_least_one_enabled_required():
    with pytest.raises(ValueError, match="at least one query must be enabled"):
        QueryCatalog(
            queries=[
                {"id": "a", "query": "q", "topic": "t", "limit": 1, "enabled": False}
            ]
        )


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_catalog(tmp_path / "nope.yaml")


def test_malformed_yaml_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("queries: [this is not a mapping]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


def test_non_mapping_yaml_raises(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_catalog(path)


def test_default_catalog_path_honours_env(monkeypatch, tmp_path):
    override = tmp_path / "catalog.yaml"
    monkeypatch.setenv(ENV_CATALOG, str(override))
    assert default_catalog_path() == override

    monkeypatch.delenv(ENV_CATALOG, raising=False)
    assert default_catalog_path().name == "ingest-queries.yaml"
