"""Schema additions for the Model entity (E-3, Unit 2).

Asserts the constraint + indexes + vector index land in the schema module
and the SCHEMA_VERSION is bumped past E-2.
"""

import pytest
from agentic_kg.knowledge_graph import schema


class TestModelConstraintInSchema:
    def test_model_id_unique_constraint_present(self):
        names = [name for name, _ in schema.CONSTRAINTS]
        assert "model_id_unique" in names

    def test_model_id_unique_constraint_cypher_shape(self):
        for name, cypher in schema.CONSTRAINTS:
            if name == "model_id_unique":
                assert "REQUIRE m.id IS UNIQUE" in cypher
                assert "FOR (m:Model)" in cypher
                assert "IF NOT EXISTS" in cypher
                return
        pytest.fail("model_id_unique not found")


class TestModelIndexesInSchema:
    def test_model_name_idx_present(self):
        names = [name for name, _ in schema.INDEXES]
        assert "model_name_idx" in names

    def test_model_is_canonical_idx_present(self):
        names = [name for name, _ in schema.INDEXES]
        assert "model_is_canonical_idx" in names

    def test_model_name_idx_targets_name_property(self):
        for name, cypher in schema.INDEXES:
            if name == "model_name_idx":
                assert "ON (m.name)" in cypher
                assert "FOR (m:Model)" in cypher
                return
        pytest.fail("model_name_idx not found")

    def test_model_is_canonical_idx_targets_canonical_property(self):
        for name, cypher in schema.INDEXES:
            if name == "model_is_canonical_idx":
                assert "ON (m.is_canonical)" in cypher
                assert "FOR (m:Model)" in cypher
                return
        pytest.fail("model_is_canonical_idx not found")


class TestModelVectorIndexInSchema:
    def test_model_embedding_idx_present(self):
        names = [name for name, _ in schema.VECTOR_INDEXES]
        assert "model_embedding_idx" in names

    def test_model_embedding_idx_dimensions_and_similarity(self):
        for name, cypher in schema.VECTOR_INDEXES:
            if name == "model_embedding_idx":
                assert "`vector.dimensions`: 1536" in cypher
                assert "'cosine'" in cypher
                assert "FOR (m:Model)" in cypher
                assert "ON m.embedding" in cypher
                return
        pytest.fail("model_embedding_idx not found")


class TestSchemaVersion:
    def test_schema_version_bumped_for_e3(self):
        """E-3 promoted Model to a first-class node — schema bump required
        so the version-tracking node reflects it."""
        assert schema.SCHEMA_VERSION >= 5
