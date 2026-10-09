"""The deterministic nightly planner."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from agentic_kg.pipeline.catalog import QueryCatalog
from agentic_kg.pipeline.plan import main, plan_queries

SHIPPED_CATALOG = Path(__file__).resolve().parents[4] / "config" / "ingest-queries.yaml"


def _catalog(*specs) -> QueryCatalog:
    return QueryCatalog(queries=list(specs))


def _spec(qid, limit=5, weight=1.0, enabled=True):
    return {
        "id": qid,
        "query": f"query {qid}",
        "topic": "topic",
        "limit": limit,
        "weight": weight,
        "enabled": enabled,
    }


def test_same_date_is_deterministic():
    catalog = _catalog(_spec("a"), _spec("b"), _spec("c"))
    day = date(2026, 10, 9)
    first = [q.query_id for q in plan_queries(catalog, day=day)]
    second = [q.query_id for q in plan_queries(catalog, day=day)]
    assert first == second


def test_budget_is_respected():
    catalog = _catalog(_spec("a", limit=8), _spec("b", limit=8), _spec("c", limit=8))
    plan = plan_queries(catalog, day=date(2026, 10, 9), max_papers=20)
    assert sum(q.limit for q in plan) <= 20
    assert len(plan) == 2


def test_all_enabled_covered_over_consecutive_nights():
    catalog = _catalog(
        _spec("a", limit=8),
        _spec("b", limit=8),
        _spec("c", limit=8),
        _spec("d", limit=8),
    )
    covered: set[str] = set()
    start = date(2026, 10, 9)
    for offset in range(len(catalog.queries)):
        plan = plan_queries(catalog, day=start + timedelta(days=offset), max_papers=20)
        covered.update(q.query_id for q in plan)
    assert covered == {"a", "b", "c", "d"}


def test_higher_weight_is_served_first_when_budget_tight():
    # Weight desc, then id asc: b (w=2) before a (w=1). On day with offset 0.
    catalog = _catalog(_spec("a", limit=8, weight=1.0), _spec("b", limit=8, weight=2.0))
    # 2026-10-09 -> ordinal 739588; 739588 % 2 == 0, so no rotation.
    plan = plan_queries(catalog, day=date(2026, 10, 9), max_papers=8)
    assert [q.query_id for q in plan] == ["b"]


def test_disabled_queries_are_never_planned():
    catalog = _catalog(_spec("a"), _spec("b", enabled=False))
    plan = plan_queries(catalog, day=date(2026, 10, 9), max_papers=50)
    assert [q.query_id for q in plan] == ["a"]


def test_budget_below_every_limit_raises():
    catalog = _catalog(_spec("a", limit=10))
    with pytest.raises(ValueError, match="below every query's limit"):
        plan_queries(catalog, day=date(2026, 10, 9), max_papers=5)


def test_zero_budget_raises():
    catalog = _catalog(_spec("a", limit=1))
    with pytest.raises(ValueError, match="max_papers must be >= 1"):
        plan_queries(catalog, day=date(2026, 10, 9), max_papers=0)


def test_cli_prints_json(capsys):
    code = main(
        ["--date", "2026-10-09", "--max-papers", "50", "--catalog", str(SHIPPED_CATALOG)]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload and set(payload[0]) == {
        "query_id",
        "query",
        "topic",
        "limit",
        "weight",
    }
