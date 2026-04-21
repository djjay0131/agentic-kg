"""
Tests for ResearchConcept schema declarations (E-2, Unit 2).

Verifies SCHEMA_VERSION bump to 4 and that the new constraint, name
index, and vector index are declared in the schema module without
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
    """E-2 must advance the schema version to at least 4."""

    def test_version_is_at_least_four(self):
        assert SCHEMA_VERSION >= 4

    def test_module_attribute_matches(self):
        assert schema_module.SCHEMA_VERSION >= 4


class TestResearchConceptConstraint:
    """research_concept_id_unique must be declared."""

    def test_constraint_present(self):
        names = {name for name, _ in CONSTRAINTS}
        assert "research_concept_id_unique" in names

    def test_constraint_query_shape(self):
        query = dict(CONSTRAINTS)["research_concept_id_unique"]
        assert "CONSTRAINT research_concept_id_unique" in query
        assert "(rc:ResearchConcept)" in query
        assert "rc.id IS UNIQUE" in query


class TestResearchConceptIndexes:
    """Name index declared; targets the right property."""

    def test_name_index_present(self):
        names = {name for name, _ in INDEXES}
        assert "research_concept_name_idx" in names

    def test_name_index_targets_name(self):
        query = dict(INDEXES)["research_concept_name_idx"]
        assert "(rc:ResearchConcept)" in query
        assert "(rc.name)" in query


class TestResearchConceptVectorIndex:
    """Vector index declared with 1536 dims + cosine similarity."""

    def test_embedding_index_present(self):
        names = {name for name, _ in VECTOR_INDEXES}
        assert "research_concept_embedding_idx" in names

    def test_vector_index_config(self):
        query = dict(VECTOR_INDEXES)["research_concept_embedding_idx"]
        assert "CREATE VECTOR INDEX research_concept_embedding_idx" in query
        assert "(rc:ResearchConcept)" in query
        assert "rc.embedding" in query
        assert "`vector.dimensions`: 1536" in query
        assert "`vector.similarity_function`: 'cosine'" in query


class TestExistingSchemaPreserved:
    """Unit 2 is additive — existing declarations must remain."""

    def test_core_constraints_preserved(self):
        names = {name for name, _ in CONSTRAINTS}
        assert "problem_id_unique" in names
        assert "paper_doi_unique" in names
        assert "topic_id_unique" in names
        assert "schema_version_unique" in names

    def test_topic_indexes_preserved(self):
        names = {name for name, _ in INDEXES}
        assert "topic_name_idx" in names
        assert "topic_level_idx" in names
        assert "topic_source_idx" in names

    def test_vector_indexes_preserved(self):
        names = {name for name, _ in VECTOR_INDEXES}
        assert "problem_embedding_idx" in names
        assert "mention_embedding_idx" in names
        assert "concept_embedding_idx" in names
        assert "topic_embedding_idx" in names
