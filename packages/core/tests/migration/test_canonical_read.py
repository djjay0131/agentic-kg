"""The read-only canonical facade, tested without the migration extra.

``agentic_kg.migration.canonical_read`` has no optional imports at module scope
on purpose: the API depends on it and must import on a default install. These
tests use a plain fake reader returning dicts, which is exactly why the facade
coerces records through ``_as_dict`` rather than importing their pydantic types.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.canonical_read import (
    DEFAULT_CANONICAL_NAMESPACE,
    CanonicalReadTarget,
    canonical_read_target_from_env,
    canonical_summary,
    entity_detail,
    list_entities,
)


def _entity(identity_id: str, entity_type: str, name: str, status: str = "ACTIVE"):
    return {
        "identity_id": identity_id,
        "entity_type": entity_type,
        "display_name": name,
        "status": status,
        "aliases": [{"namespace": "doi", "key": name.lower()}],
    }


ENTITIES = [
    _entity("id-1", "Paper", "CS-KG"),
    _entity("id-2", "Paper", "Empire"),
    _entity("id-3", "Topic", "Knowledge Graphs"),
    _entity("id-4", "Topic", "Retired Topic", status="SUPERSEDED"),
]
ASSERTIONS = {
    "id-1": [
        {
            "assertion_id": "as-1",
            "subject_identity": "id-1",
            "predicate": "title",
            "evidence_refs": [{"evidence_id": "ev-1"}, {"evidence_id": "ev-2"}],
        },
        {
            "assertion_id": "as-2",
            "subject_identity": "id-1",
            "predicate": "year",
            "evidence_refs": [{"evidence_id": "ev-2"}],
        },
    ]
}


class FakeReader:
    def __init__(self, epoch: int = 7):
        self._epoch = epoch
        self.entity_type_calls: list[str | None] = []

    def current_epoch(self) -> int:
        return self._epoch

    def find_entities(self, entity_type=None, alias=None, options=None):
        self.entity_type_calls.append(entity_type)
        if entity_type is None:
            return list(ENTITIES)
        return [entity for entity in ENTITIES if entity["entity_type"] == entity_type]

    def get_entity(self, identity_id, options=None):
        return next((e for e in ENTITIES if e["identity_id"] == identity_id), None)

    def assertions_for(self, identity_id, options=None):
        return list(ASSERTIONS.get(identity_id, []))


def test_summary_counts_by_type_and_status():
    summary = canonical_summary(FakeReader(epoch=7))
    assert summary["epoch"] == 7
    assert summary["total_entities"] == 4
    assert summary["entities_by_type"] == {"Paper": 2, "Topic": 2}
    assert summary["entities_by_status"] == {"ACTIVE": 3, "SUPERSEDED": 1}


def test_list_entities_pushes_type_to_the_reader():
    reader = FakeReader()
    page = list_entities(reader, entity_type="Paper")
    assert reader.entity_type_calls == ["Paper"]
    assert page["count"] == 2
    assert {e["entity_type"] for e in page["entities"]} == {"Paper"}


def test_list_entities_filters_by_text_over_name_and_alias():
    page = list_entities(FakeReader(), query="know")
    assert [e["identity_id"] for e in page["entities"]] == ["id-3"]


def test_list_entities_paginates_stably():
    reader = FakeReader()
    first = list_entities(reader, limit=2, offset=0)
    second = list_entities(reader, limit=2, offset=2)
    assert [e["identity_id"] for e in first["entities"]] == ["id-1", "id-2"]
    assert [e["identity_id"] for e in second["entities"]] == ["id-3", "id-4"]
    assert first["count"] == second["count"] == 4


def test_list_entities_rejects_negative_window():
    with pytest.raises(ValueError):
        list_entities(FakeReader(), limit=-1)
    with pytest.raises(ValueError):
        list_entities(FakeReader(), offset=-1)


def test_entity_detail_carries_assertions_and_deduplicated_evidence_refs():
    detail = entity_detail(FakeReader(), "id-1")
    assert detail is not None
    assert detail["identity_id"] == "id-1"
    assert len(detail["assertions"]) == 2
    assert detail["evidence_refs"] == ["ev-1", "ev-2"]


def test_entity_detail_returns_none_for_a_missing_identity():
    assert entity_detail(FakeReader(), "nope") is None


def test_target_from_env_defaults_and_overrides(monkeypatch):
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KGCS_CANONICAL_NAMESPACE", "staging")
    target = canonical_read_target_from_env()
    assert isinstance(target, CanonicalReadTarget)
    assert target.namespace == "staging"
    assert target.username == "neo4j"
    assert target.database == "neo4j"


def test_target_from_env_namespace_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("KGCS_CANONICAL_NAMESPACE", raising=False)
    assert canonical_read_target_from_env().namespace == DEFAULT_CANONICAL_NAMESPACE
