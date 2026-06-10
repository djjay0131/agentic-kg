"""Schema additions for the Method entity (E-4, Unit 2)."""

import pytest
from agentic_kg.knowledge_graph import schema


class TestMethodConstraintInSchema:
    def test_method_id_unique_constraint_present(self):
        names = [name for name, _ in schema.CONSTRAINTS]
        assert "method_id_unique" in names

    def test_method_id_unique_constraint_cypher_shape(self):
        for name, cypher in schema.CONSTRAINTS:
            if name == "method_id_unique":
                assert "REQUIRE m.id IS UNIQUE" in cypher
                assert "FOR (m:Method)" in cypher
                assert "IF NOT EXISTS" in cypher
                return
        pytest.fail("method_id_unique not found")


class TestMethodIndexesInSchema:
    def test_method_name_idx_present(self):
        names = [name for name, _ in schema.INDEXES]
        assert "method_name_idx" in names

    def test_method_name_idx_targets_name_property(self):
        for name, cypher in schema.INDEXES:
            if name == "method_name_idx":
                assert "ON (m.name)" in cypher
                assert "FOR (m:Method)" in cypher
                return
        pytest.fail("method_name_idx not found")

    def test_no_method_is_canonical_idx(self):
        """E-4 deliberately has no is_canonical field; sanity-check that
        nobody copy-pasted the E-3 Model index by mistake."""
        names = [name for name, _ in schema.INDEXES]
        assert "method_is_canonical_idx" not in names


class TestMethodVectorIndexInSchema:
    def test_method_embedding_idx_present(self):
        names = [name for name, _ in schema.VECTOR_INDEXES]
        assert "method_embedding_idx" in names

    def test_method_embedding_idx_dimensions_and_similarity(self):
        for name, cypher in schema.VECTOR_INDEXES:
            if name == "method_embedding_idx":
                assert "`vector.dimensions`: 1536" in cypher
                assert "'cosine'" in cypher
                assert "FOR (m:Method)" in cypher
                assert "ON m.embedding" in cypher
                return
        pytest.fail("method_embedding_idx not found")


class TestSchemaVersion:
    def test_schema_version_bumped_for_e4(self):
        """E-3 left it at 5; E-4 bumps to 6."""
        assert schema.SCHEMA_VERSION >= 6
