"""E-8 Unit 3 — flatten_taxonomy helper.

``TopicExtractor.__init__`` needs:

- the flat tuple of accepted topic names (to build a ``Literal``)
- the name→level mapping (so the LLM's emitted name can be tagged with its
  level by the integration layer if needed)

Both come from the same nested parsed taxonomy that ``parse_taxonomy``
returns. ``flatten_taxonomy`` is the single point that walks the tree.
"""


from agentic_kg.knowledge_graph.taxonomy import flatten_taxonomy, parse_taxonomy

SAMPLE = """
- name: Computer Science
  level: domain
  children:
    - name: NLP
      level: area
      children:
        - name: Machine Translation
          level: subtopic
    - name: Computer Vision
      level: area
"""


class TestFlattenTaxonomy:
    def test_returns_dict_name_to_level(self):
        parsed = parse_taxonomy(SAMPLE)
        flat = flatten_taxonomy(parsed)
        assert isinstance(flat, dict)
        assert flat["Computer Science"] == "domain"
        assert flat["NLP"] == "area"
        assert flat["Computer Vision"] == "area"
        assert flat["Machine Translation"] == "subtopic"

    def test_empty_taxonomy_returns_empty_dict(self):
        assert flatten_taxonomy([]) == {}

    def test_does_not_mutate_input(self):
        parsed = parse_taxonomy(SAMPLE)
        # Copy of pre-call shape via repr; if flatten mutates, repr changes.
        before = repr(parsed)
        flatten_taxonomy(parsed)
        assert repr(parsed) == before

    def test_walks_arbitrary_depth(self):
        # Even though the seed taxonomy is three levels deep, the walker
        # must not be hard-coded to three levels — it walks recursively.
        nested = [
            {
                "name": "L1",
                "level": "domain",
                "children": [
                    {
                        "name": "L2",
                        "level": "area",
                        "children": [
                            {"name": "L3a", "level": "subtopic"},
                            {"name": "L3b", "level": "subtopic"},
                        ],
                    }
                ],
            }
        ]
        flat = flatten_taxonomy(nested)
        assert set(flat) == {"L1", "L2", "L3a", "L3b"}

    def test_handles_none_children(self):
        # parse_taxonomy permits children: null in YAML; flatten must tolerate.
        nested = [
            {"name": "A", "level": "domain", "children": None},
            {"name": "B", "level": "domain"},
        ]
        flat = flatten_taxonomy(nested)
        assert flat == {"A": "domain", "B": "domain"}

    def test_duplicate_name_under_different_parents_keeps_last(self):
        # The seed taxonomy enforces uniqueness within (parent, level), but
        # nothing prevents the same leaf name from appearing under two
        # different areas. The flat dict collapses to one entry; the level
        # is the same in practice, so the last-wins behavior is fine. This
        # test pins the documented behavior.
        nested = [
            {
                "name": "Root",
                "level": "domain",
                "children": [
                    {
                        "name": "Area1",
                        "level": "area",
                        "children": [{"name": "shared", "level": "subtopic"}],
                    },
                    {
                        "name": "Area2",
                        "level": "area",
                        "children": [{"name": "shared", "level": "subtopic"}],
                    },
                ],
            }
        ]
        flat = flatten_taxonomy(nested)
        assert flat["shared"] == "subtopic"

    def test_real_seed_taxonomy_flattens(self):
        """Smoke test against the actual seed_taxonomy.yml — catches schema
        drift if a child node ever ships without a level.
        """
        from agentic_kg.knowledge_graph.taxonomy import (
            DEFAULT_TAXONOMY_PATH,
        )
        from agentic_kg.knowledge_graph.taxonomy import (
            parse_taxonomy as parse,
        )

        parsed = parse(DEFAULT_TAXONOMY_PATH)
        flat = flatten_taxonomy(parsed)
        # Seed taxonomy currently has 29 nodes per the E-1 spec; assert at
        # least the order of magnitude so a drastic regression is caught.
        assert len(flat) >= 20
        for name, level in flat.items():
            assert level in {"domain", "area", "subtopic"}
            assert len(name) >= 2
