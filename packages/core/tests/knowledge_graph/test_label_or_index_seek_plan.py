"""Docker-free unit tests for the query-plan operator walker.

The plan-shape assertions themselves need a live Neo4j (see
``test_label_or_index_seek.py``). The parsing of the driver's plan map does
not, and a broken parser would make those assertions silently vacuous — so it
is pinned here, where the unit job runs it without Docker.
"""

from __future__ import annotations

from .test_label_or_index_seek import (
    SCAN_OPERATORS,
    SEEK_OPERATORS,
    operator_types,
)

_DICT_PLAN = {
    "operatorType": "ProduceResults",
    "children": [
        {
            "operatorType": "Union",
            "children": [
                {"operatorType": "NodeUniqueIndexSeek", "children": []},
                {"operatorType": "NodeUniqueIndexSeek", "children": []},
            ],
        }
    ],
}


class _FakePlan:
    """Stands in for a future driver that returns objects, not mappings."""

    def __init__(self, operator_type, children=()):
        self.operator_type = operator_type
        self.children = list(children)


class TestOperatorTypes:
    def test_walks_a_mapping_plan_depth_first(self):
        assert operator_types(_DICT_PLAN) == [
            "ProduceResults",
            "Union",
            "NodeUniqueIndexSeek",
            "NodeUniqueIndexSeek",
        ]

    def test_walks_an_object_plan(self):
        plan = _FakePlan(
            "ProduceResults",
            [_FakePlan("AllNodesScan")],
        )
        assert operator_types(plan) == ["ProduceResults", "AllNodesScan"]

    def test_none_plan_yields_nothing(self):
        assert operator_types(None) == []

    def test_missing_children_is_tolerated(self):
        assert operator_types({"operatorType": "NodeByLabelScan"}) == [
            "NodeByLabelScan"
        ]

    def test_database_qualifier_is_stripped(self):
        """Neo4j 5.26 names operators ``NodeUniqueIndexSeek@neo4j``."""
        plan = {
            "operatorType": "NodeUniqueIndexSeek@neo4j",
            "children": [{"operatorType": "AllNodesScan@neo4j"}],
        }
        assert operator_types(plan) == ["NodeUniqueIndexSeek", "AllNodesScan"]


class TestOperatorSets:
    def test_scan_and_seek_sets_are_disjoint(self):
        assert not SCAN_OPERATORS & SEEK_OPERATORS

    def test_the_bare_scans_are_named(self):
        assert {"AllNodesScan", "NodeByLabelScan"} <= SCAN_OPERATORS

    def test_the_unique_index_seek_is_named(self):
        assert "NodeUniqueIndexSeek" in SEEK_OPERATORS
