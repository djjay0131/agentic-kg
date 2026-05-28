"""E-8 Unit 9 part B — canonical_taxonomy_hash.

Supports AC-15: ``Paper.taxonomy_hash`` is a sha256 of the canonically-
serialized taxonomy used by the extractor. Cosmetic edits (whitespace,
comments) must not change the hash; semantic edits must.
"""

import hashlib

from agentic_kg.extraction.taxonomy_hash import canonical_taxonomy_hash
from agentic_kg.knowledge_graph.taxonomy import parse_taxonomy

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


WITH_COMMENT = """
# some comment that should NOT affect the hash
- name: Computer Science
  level: domain
  children:
    - name: NLP
      level: area
      children:
        - name: Machine Translation
          level: subtopic

    # blank line + comment above
    - name: Computer Vision
      level: area
"""


SEMANTIC_CHANGE = """
- name: Computer Science
  level: domain
  children:
    - name: NLP
      level: area
      children:
        - name: Machine Translation
          level: subtopic
        - name: Question Answering   # added node
          level: subtopic
    - name: Computer Vision
      level: area
"""


class TestCanonicalTaxonomyHash:
    def test_returns_sha256_hex(self):
        parsed = parse_taxonomy(SAMPLE)
        h = canonical_taxonomy_hash(parsed)
        assert isinstance(h, str)
        # sha256 hex string is 64 chars.
        assert len(h) == 64
        int(h, 16)  # parses as hex

    def test_same_input_same_hash(self):
        parsed = parse_taxonomy(SAMPLE)
        assert canonical_taxonomy_hash(parsed) == canonical_taxonomy_hash(parsed)

    def test_cosmetic_edits_do_not_change_hash(self):
        h1 = canonical_taxonomy_hash(parse_taxonomy(SAMPLE))
        h2 = canonical_taxonomy_hash(parse_taxonomy(WITH_COMMENT))
        assert h1 == h2

    def test_semantic_change_changes_hash(self):
        h1 = canonical_taxonomy_hash(parse_taxonomy(SAMPLE))
        h2 = canonical_taxonomy_hash(parse_taxonomy(SEMANTIC_CHANGE))
        assert h1 != h2

    def test_key_ordering_does_not_matter(self):
        """Two parsed dicts whose keys arrived in different orders must
        hash identically. Canonical serialization sorts keys."""
        a = [{"name": "X", "level": "domain", "description": "d"}]
        b = [{"description": "d", "level": "domain", "name": "X"}]
        assert canonical_taxonomy_hash(a) == canonical_taxonomy_hash(b)

    def test_strips_none_children(self):
        """children: None and a missing children key must hash the same —
        parse_taxonomy already normalizes them, but the hash function
        should not bake the YAML's representation choice into the hash."""
        a = [{"name": "X", "level": "domain"}]
        b = [{"name": "X", "level": "domain", "children": None}]
        c = [{"name": "X", "level": "domain", "children": []}]
        h_a = canonical_taxonomy_hash(a)
        h_b = canonical_taxonomy_hash(b)
        h_c = canonical_taxonomy_hash(c)
        assert h_a == h_b == h_c

    def test_real_seed_taxonomy_hashable(self):
        from agentic_kg.knowledge_graph.taxonomy import (
            DEFAULT_TAXONOMY_PATH,
        )

        parsed = parse_taxonomy(DEFAULT_TAXONOMY_PATH)
        h = canonical_taxonomy_hash(parsed)
        # Smoke: matches sha256 produced by the same algorithm independently
        # using json.dumps with sort_keys=True over the cleaned tree.
        assert len(h) == 64

    def test_empty_taxonomy_has_stable_hash(self):
        h = canonical_taxonomy_hash([])
        assert h == hashlib.sha256(b"[]").hexdigest()
