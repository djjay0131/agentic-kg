"""
Tests for the Topic taxonomy loader/exporter (E-1, Unit 4).

Pure parsing/validation tests don't touch Neo4j and always run. The
end-to-end loader/exporter tests require a live Neo4j instance and are
marked with ``pytest.mark.integration`` so they skip in offline CI.
"""

from pathlib import Path

import pytest

from agentic_kg.knowledge_graph.models import TopicLevel
from agentic_kg.knowledge_graph.taxonomy import (
    DEFAULT_TAXONOMY_PATH,
    TaxonomyError,
    dump_taxonomy_to_yaml,
    export_taxonomy,
    load_taxonomy,
    parse_taxonomy,
    taxonomy_to_yaml,
)


# =============================================================================
# Pure parser tests (no Neo4j required)
# =============================================================================


class TestParseTaxonomyValid:
    """Valid structures accepted by parse_taxonomy."""

    def test_minimal_domain_only(self):
        tax = parse_taxonomy(
            [
                {"name": "Computer Science", "level": "domain"},
            ]
        )
        assert len(tax) == 1
        assert tax[0]["name"] == "Computer Science"
        assert tax[0]["level"] == "domain"

    def test_three_level_tree(self):
        tax = parse_taxonomy(
            [
                {
                    "name": "Computer Science",
                    "level": "domain",
                    "children": [
                        {
                            "name": "Natural Language Processing",
                            "level": "area",
                            "children": [
                                {"name": "Question Answering", "level": "subtopic"},
                            ],
                        }
                    ],
                }
            ]
        )
        assert tax[0]["children"][0]["children"][0]["level"] == "subtopic"

    def test_accepts_yaml_string(self):
        source = """
- name: Computer Science
  level: domain
  children:
    - name: Natural Language Processing
      level: area
"""
        tax = parse_taxonomy(source)
        assert tax[0]["name"] == "Computer Science"
        assert tax[0]["children"][0]["level"] == "area"

    def test_accepts_file_path(self):
        tax = parse_taxonomy(DEFAULT_TAXONOMY_PATH)
        assert isinstance(tax, list)
        assert len(tax) >= 1

    def test_seed_taxonomy_structure(self):
        """The shipped seed taxonomy must parse cleanly."""
        tax = parse_taxonomy(DEFAULT_TAXONOMY_PATH)

        def count(nodes: list[dict]) -> int:
            return sum(1 + count(n.get("children") or []) for n in nodes)

        total = count(tax)
        # Spec calls for ~30-50 nodes; cap is a sanity ceiling, not a goal.
        assert 20 <= total <= 60, f"Unexpected node count: {total}"

    def test_description_and_source_preserved(self):
        tax = parse_taxonomy(
            [
                {
                    "name": "Computer Science",
                    "level": "domain",
                    "description": "the field",
                    "source": "manual",
                }
            ]
        )
        assert tax[0]["description"] == "the field"
        assert tax[0]["source"] == "manual"


class TestParseTaxonomyInvalid:
    """Malformed structures rejected by parse_taxonomy."""

    def test_empty_string_raises(self):
        # Empty YAML document parses to None — treat as malformed input.
        with pytest.raises(TaxonomyError, match="empty"):
            parse_taxonomy("")

    def test_empty_list_is_accepted(self):
        # An empty list is a valid (if uninteresting) taxonomy.
        assert parse_taxonomy([]) == []

    def test_root_must_be_list(self):
        with pytest.raises(TaxonomyError, match="list"):
            parse_taxonomy({"name": "X", "level": "domain"})

    def test_missing_name_raises(self):
        with pytest.raises(TaxonomyError, match="name"):
            parse_taxonomy([{"level": "domain"}])

    def test_short_name_raises(self):
        with pytest.raises(TaxonomyError, match="name"):
            parse_taxonomy([{"name": "X", "level": "domain"}])

    def test_invalid_level_raises(self):
        with pytest.raises(TaxonomyError, match="level"):
            parse_taxonomy([{"name": "Thing", "level": "bogus"}])

    def test_missing_level_raises(self):
        with pytest.raises(TaxonomyError, match="level"):
            parse_taxonomy([{"name": "Thing"}])

    def test_subtopic_at_root_raises(self):
        with pytest.raises(TaxonomyError, match="subtopic"):
            parse_taxonomy([{"name": "Machine Translation", "level": "subtopic"}])

    def test_area_at_root_raises(self):
        with pytest.raises(TaxonomyError, match="area"):
            parse_taxonomy([{"name": "NLP", "level": "area"}])

    def test_domain_as_child_raises(self):
        with pytest.raises(TaxonomyError, match="domain"):
            parse_taxonomy(
                [
                    {
                        "name": "Computer Science",
                        "level": "domain",
                        "children": [
                            {"name": "Another Domain", "level": "domain"}
                        ],
                    }
                ]
            )

    def test_subtopic_under_domain_raises(self):
        with pytest.raises(TaxonomyError, match="subtopic"):
            parse_taxonomy(
                [
                    {
                        "name": "Computer Science",
                        "level": "domain",
                        "children": [
                            {"name": "Question Answering", "level": "subtopic"}
                        ],
                    }
                ]
            )

    def test_duplicate_siblings_raise(self):
        with pytest.raises(TaxonomyError, match="duplicate"):
            parse_taxonomy(
                [
                    {
                        "name": "Computer Science",
                        "level": "domain",
                        "children": [
                            {"name": "Natural Language Processing", "level": "area"},
                            {"name": "Natural Language Processing", "level": "area"},
                        ],
                    }
                ]
            )

    def test_same_name_different_levels_allowed(self):
        """Different levels at the same sibling position are distinct identities."""
        # This currently just exercises one level — truly distinct identities
        # across different parents are already covered by the merge tests.
        tax = parse_taxonomy(
            [
                {
                    "name": "Computer Science",
                    "level": "domain",
                    "children": [
                        {
                            "name": "Information Retrieval",
                            "level": "area",
                            "children": [
                                {
                                    "name": "Information Retrieval",
                                    "level": "subtopic",
                                },
                            ],
                        }
                    ],
                }
            ]
        )
        assert tax[0]["children"][0]["children"][0]["level"] == "subtopic"

    def test_missing_file_raises(self):
        with pytest.raises(TaxonomyError, match="not found"):
            parse_taxonomy(Path("/tmp/does-not-exist-123.yml"))

    def test_children_must_be_list(self):
        with pytest.raises(TaxonomyError, match="children"):
            parse_taxonomy(
                [
                    {
                        "name": "Computer Science",
                        "level": "domain",
                        "children": "not a list",
                    }
                ]
            )


class TestTaxonomySerialization:
    """YAML round-trip preserves the logical structure."""

    def test_dump_and_reparse_roundtrip(self, tmp_path):
        original = parse_taxonomy(DEFAULT_TAXONOMY_PATH)
        out = tmp_path / "out.yml"
        dump_taxonomy_to_yaml(original, out)
        reparsed = parse_taxonomy(out)
        assert reparsed == original

    def test_taxonomy_to_yaml_produces_valid_yaml(self):
        tax = [
            {
                "name": "Computer Science",
                "level": "domain",
                "children": [
                    {"name": "Natural Language Processing", "level": "area"}
                ],
            }
        ]
        rendered = taxonomy_to_yaml(tax)
        assert "Computer Science" in rendered
        # Round-trip
        assert parse_taxonomy(rendered) == tax


# =============================================================================
# Database-backed loader / exporter (integration)
# =============================================================================


@pytest.mark.integration
class TestLoadTaxonomy:
    """load_taxonomy against Neo4j."""

    def test_load_small_tree_creates_nodes_and_edges(self, neo4j_repository):
        source = [
            {
                "name": "TEST_Domain_Load",
                "level": "domain",
                "children": [
                    {
                        "name": "TEST_Area_Load",
                        "level": "area",
                        "children": [
                            {"name": "TEST_Subtopic_Load", "level": "subtopic"}
                        ],
                    }
                ],
            }
        ]
        stats = load_taxonomy(
            neo4j_repository, source, generate_embeddings=False
        )
        assert stats["created"] == 3
        assert stats["matched"] == 0

        domains = neo4j_repository.get_topics_by_level(TopicLevel.DOMAIN)
        domain = next(d for d in domains if d.name == "TEST_Domain_Load")
        children = neo4j_repository.get_topic_children(domain.id)
        assert {c.name for c in children} == {"TEST_Area_Load"}

        area = children[0]
        subs = neo4j_repository.get_topic_children(area.id)
        assert {s.name for s in subs} == {"TEST_Subtopic_Load"}

    def test_load_is_idempotent(self, neo4j_repository):
        source = [
            {
                "name": "TEST_Domain_Idem",
                "level": "domain",
                "children": [
                    {"name": "TEST_Area_Idem", "level": "area"}
                ],
            }
        ]
        first = load_taxonomy(
            neo4j_repository, source, generate_embeddings=False
        )
        second = load_taxonomy(
            neo4j_repository, source, generate_embeddings=False
        )
        assert first["created"] == 2
        assert second["created"] == 0
        assert second["matched"] == 2


@pytest.mark.integration
class TestExportTaxonomy:
    """export_taxonomy reads Neo4j back to a nested structure."""

    def test_export_roundtrips_loaded_taxonomy(self, neo4j_repository, tmp_path):
        source = [
            {
                "name": "TEST_RT_Domain",
                "level": "domain",
                "description": "round-trip domain",
                "children": [
                    {
                        "name": "TEST_RT_Area",
                        "level": "area",
                        "children": [
                            {"name": "TEST_RT_Sub", "level": "subtopic"},
                        ],
                    },
                ],
            }
        ]
        load_taxonomy(neo4j_repository, source, generate_embeddings=False)

        exported = export_taxonomy(neo4j_repository)
        exported_test = [
            d for d in exported if d["name"].startswith("TEST_RT_")
        ]
        assert len(exported_test) == 1

        roundtrip_path = tmp_path / "exported.yml"
        dump_taxonomy_to_yaml(exported_test, roundtrip_path)
        reparsed = parse_taxonomy(roundtrip_path)
        assert reparsed == exported_test

        # Re-loading the exported file is a no-op (idempotent).
        stats = load_taxonomy(
            neo4j_repository, reparsed, generate_embeddings=False
        )
        assert stats["created"] == 0
        assert stats["matched"] == 3
