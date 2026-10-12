"""Schema additions for PipelineRun (nightly pipeline reports).

Asserts the ``PipelineRun`` constraint and index land in the schema module and
``SCHEMA_VERSION`` is bumped.

See ``docs/design/nightly-pipeline-contract.md``.
"""

import pytest
from agentic_kg.knowledge_graph import schema


class TestPipelineRunConstraintInSchema:
    def test_run_id_unique_constraint_present(self):
        names = [name for name, _ in schema.CONSTRAINTS]
        assert "pipeline_run_id_unique" in names

    def test_run_id_unique_constraint_cypher_shape(self):
        for name, cypher in schema.CONSTRAINTS:
            if name == "pipeline_run_id_unique":
                assert "FOR (r:PipelineRun)" in cypher
                assert "REQUIRE r.run_id IS UNIQUE" in cypher
                assert "IF NOT EXISTS" in cypher
                return
        pytest.fail("pipeline_run_id_unique not found")


class TestPipelineRunIndexInSchema:
    def test_started_at_idx_present(self):
        names = [name for name, _ in schema.INDEXES]
        assert "pipeline_run_started_at_idx" in names

    def test_started_at_idx_targets_started_at_property(self):
        for name, cypher in schema.INDEXES:
            if name == "pipeline_run_started_at_idx":
                assert "FOR (r:PipelineRun)" in cypher
                assert "ON (r.started_at)" in cypher
                return
        pytest.fail("pipeline_run_started_at_idx not found")


class TestSchemaVersion:
    def test_schema_version_bumped_for_pipeline_runs(self):
        assert schema.SCHEMA_VERSION >= 8
