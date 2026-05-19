"""
Tests for Topic-related schema declarations (E-1, Unit 2).

Verifies SCHEMA_VERSION bump and that Topic constraints, indexes,
and the vector index are declared in the schema module without
requiring a live Neo4j connection.
"""

from agentic_kg.knowledge_graph import schema as schema_module
from agentic_kg.knowledge_graph.schema import (
    CONSTRAINTS,
    INDEXES,
    SCHEMA_VERSION,
    VECTOR_INDEXES,
)


class TestSchemaVersion:
    """Schema version must advance to 3 for the Topic hierarchy."""

    def test_version_is_three(self):
        assert SCHEMA_VERSION == 3

    def test_module_attribute_matches(self):
        assert schema_module.SCHEMA_VERSION == 3


class TestTopicConstraints:
    """Topic constraints must be declared in the schema."""

    def test_topic_id_unique_present(self):
        names = {name for name, _ in CONSTRAINTS}
        assert "topic_id_unique" in names

    def test_topic_id_unique_query_shape(self):
        query = dict(CONSTRAINTS)["topic_id_unique"]
        assert "CONSTRAINT topic_id_unique" in query
        assert "(t:Topic)" in query
        assert "t.id IS UNIQUE" in query


class TestTopicIndexes:
    """Topic indexes must be declared in the schema."""

    def test_topic_name_idx_present(self):
        names = {name for name, _ in INDEXES}
        assert "topic_name_idx" in names

    def test_topic_level_idx_present(self):
        names = {name for name, _ in INDEXES}
        assert "topic_level_idx" in names

    def test_topic_source_idx_present(self):
        names = {name for name, _ in INDEXES}
        assert "topic_source_idx" in names

    def test_topic_name_index_targets_topic_name(self):
        query = dict(INDEXES)["topic_name_idx"]
        assert "(t:Topic)" in query
        assert "(t.name)" in query

    def test_topic_level_index_targets_topic_level(self):
        query = dict(INDEXES)["topic_level_idx"]
        assert "(t:Topic)" in query
        assert "(t.level)" in query

    def test_topic_source_index_targets_topic_source(self):
        query = dict(INDEXES)["topic_source_idx"]
        assert "(t:Topic)" in query
        assert "(t.source)" in query


class TestTopicVectorIndex:
    """Topic vector index must be declared with 1536 dims + cosine."""

    def test_topic_embedding_idx_present(self):
        names = {name for name, _ in VECTOR_INDEXES}
        assert "topic_embedding_idx" in names

    def test_vector_index_config(self):
        query = dict(VECTOR_INDEXES)["topic_embedding_idx"]
        assert "CREATE VECTOR INDEX topic_embedding_idx" in query
        assert "(t:Topic)" in query
        assert "t.embedding" in query
        assert "`vector.dimensions`: 1536" in query
        assert "`vector.similarity_function`: 'cosine'" in query


class TestExistingSchemaPreserved:
    """Unit 2 is additive — existing constraints/indexes must remain."""

    def test_problem_constraints_preserved(self):
        names = {name for name, _ in CONSTRAINTS}
        assert "problem_id_unique" in names
        assert "paper_doi_unique" in names
        assert "author_id_unique" in names
        assert "problem_mention_id_unique" in names
        assert "problem_concept_id_unique" in names
        assert "schema_version_unique" in names

    def test_problem_indexes_preserved(self):
        names = {name for name, _ in INDEXES}
        # problem_domain_idx was dropped in Unit 6 (domain field removal).
        assert "problem_domain_idx" not in names
        assert "concept_domain_idx" not in names
        assert "problem_status_idx" in names
        assert "paper_year_idx" in names

    def test_existing_vector_indexes_preserved(self):
        names = {name for name, _ in VECTOR_INDEXES}
        assert "problem_embedding_idx" in names
        assert "mention_embedding_idx" in names
        assert "concept_embedding_idx" in names
