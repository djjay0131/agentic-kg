"""Structural assertions on the ``smoke-local`` Makefile target.

Covers AC-15 (smoke-local mirrors the CI workflow, accepts QUERY/LIMIT
env vars, doesn't interfere with other targets).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAKEFILE = _REPO_ROOT / "Makefile"


@pytest.fixture(scope="module")
def makefile_text() -> str:
    return _MAKEFILE.read_text()


def _target_block(text: str, target: str) -> str:
    """Return the recipe block for a target — from `<target>:` to the
    next blank line."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{target}:"):
            start = i
            break
    assert start is not None, f"target {target!r} not found in Makefile"
    end = start + 1
    while end < len(lines) and lines[end].strip() != "":
        end += 1
    return "\n".join(lines[start:end])


class TestMakefileHasTarget:
    def test_smoke_local_in_phony(self, makefile_text):
        assert "smoke-local" in makefile_text.split("\n")[0]

    def test_smoke_local_target_exists(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert block.startswith("smoke-local:")

    def test_existing_smoke_test_target_preserved(self, makefile_text):
        # AC-13-analog: existing smoke-test target (against staging)
        # must still exist untouched by this change.
        block = _target_block(makefile_text, "smoke-test")
        assert "scripts/smoke_test.py" in block


class TestSmokeLocalRecipe:
    def test_requires_docker(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "command -v docker" in block

    def test_requires_openai_key(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        # Env-var presence check.
        assert "OPENAI_API_KEY" in block

    def test_starts_neo4j_5_26(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "neo4j:5.26-community" in block

    def test_apoc_plugin_configured(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "apoc" in block

    def test_calls_initialize_schema(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "initialize_schema" in block
        assert "force=True" in block

    def test_invokes_agentic_kg_ingest_with_json(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "agentic-kg ingest" in block
        assert "--json" in block

    def test_calls_smoke_assert_script(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "scripts/smoke_assert.py" in block

    def test_query_env_var_override(self, makefile_text):
        """AC-15: QUERY env-var overrides the default."""
        block = _target_block(makefile_text, "smoke-local")
        # Shell parameter expansion syntax with default:
        # `$${QUERY:-retrieval augmented generation}` — Makefile escapes
        # the outer $ so shell sees `${QUERY:-...}`.
        assert "$${QUERY:-retrieval augmented generation}" in block

    def test_limit_env_var_override(self, makefile_text):
        block = _target_block(makefile_text, "smoke-local")
        assert "$${LIMIT:-3}" in block

    def test_uses_localhost_neo4j(self, makefile_text):
        """AC-12-analog: local target must not touch staging Neo4j."""
        block = _target_block(makefile_text, "smoke-local")
        assert "bolt://localhost:7687" in block
        # Guard against copy-paste from staging URLs.
        assert "34.173" not in block

    def test_cleans_up_container_on_completion(self, makefile_text):
        """Trailing docker rm keeps the developer's Docker state tidy."""
        block = _target_block(makefile_text, "smoke-local")
        # Two rm invocations: pre-run cleanup + post-run cleanup.
        assert block.count("docker rm -f smoke-neo4j") == 2
