"""Unit + snapshot tests for the Jekyll site data emitter (AC-4, 6, 7, 10, 15, 16)."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "generate_site_data.py"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_site_data", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve forward references.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load_module()


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    """Assemble a mini-repo layout the generator can run against."""
    (tmp_path / "llm/memory_bank").mkdir(parents=True)
    (tmp_path / "llm/features").mkdir(parents=True)
    (tmp_path / "construction/sprints").mkdir(parents=True)
    (tmp_path / "docs/_data").mkdir(parents=True)

    (tmp_path / "llm/memory_bank/activeContext.md").write_text(
        (FIXTURE_DIR / "activeContext.md").read_text()
    )
    (tmp_path / "llm/features/BACKLOG.md").write_text(
        (FIXTURE_DIR / "BACKLOG.md").read_text()
    )
    for src in sorted((FIXTURE_DIR / "sprints").glob("sprint-*.md")):
        (tmp_path / "construction/sprints" / src.name).write_text(src.read_text())
    return tmp_path


# =============================================================================
# docs-stats (AC-15)
# =============================================================================


class TestDocsStatsExtraction:
    def test_extracts_fenced_block(self, mod):
        body = mod.extract_docs_stats_block(
            '```yaml\n# docs-stats\nlast_updated: "2026-01-01"\n```\n'
        )
        assert body.startswith("# docs-stats")
        assert 'last_updated: "2026-01-01"' in body

    def test_ignores_prose_mention_of_marker(self, mod):
        """Prose saying '# docs-stats' must not be mistaken for the block."""
        text = (
            "We talk about `# docs-stats` in prose on this line.\n\n"
            "Later:\n\n"
            "```yaml\n"
            "# docs-stats\n"
            'last_updated: "2026-01-01"\n'
            "graph_nodes: 1\n"
            "```\n"
        )
        body = mod.extract_docs_stats_block(text)
        assert "graph_nodes: 1" in body

    def test_missing_marker_raises(self, mod):
        with pytest.raises(mod.DocsStatsError, match="docs-stats block not found"):
            mod.extract_docs_stats_block("no marker here\n```yaml\nfoo: 1\n```")

    def test_marker_without_fence_is_not_found(self, mod):
        """Bare marker without a ```yaml fence is treated as missing."""
        with pytest.raises(mod.DocsStatsError, match="docs-stats block not found"):
            mod.extract_docs_stats_block("# docs-stats\nlast_updated: x\n")

    def test_unterminated_block_raises(self, mod):
        with pytest.raises(mod.DocsStatsError, match="not terminated"):
            mod.extract_docs_stats_block("```yaml\n# docs-stats\nlast_updated: x\n")

    def test_non_marker_yaml_block_is_skipped(self, mod):
        """A yaml fence whose first content line isn't the marker is ignored."""
        text = (
            "```yaml\n"
            "some: config\n"
            "```\n\n"
            "```yaml\n"
            "# docs-stats\n"
            "graph_nodes: 42\n"
            "```\n"
        )
        body = mod.extract_docs_stats_block(text)
        assert "graph_nodes: 42" in body


class TestLoadDocsStats:
    def test_loads_valid_fixture(self, mod):
        stats = mod.load_docs_stats(FIXTURE_DIR / "activeContext.md")
        assert stats.graph_nodes == 282
        assert stats.sanity_checks == "5/5 passing"
        assert stats.tests_passing == 1217

    def test_missing_file_raises(self, mod, tmp_path: Path):
        with pytest.raises(mod.DocsStatsError, match="activeContext file not found"):
            mod.load_docs_stats(tmp_path / "nope.md")

    def test_invalid_yaml_raises(self, mod, tmp_path: Path):
        bad = tmp_path / "ac.md"
        bad.write_text("```yaml\n# docs-stats\nkey: [unterminated\n```\n")
        with pytest.raises(mod.DocsStatsError, match="not valid YAML"):
            mod.load_docs_stats(bad)

    def test_non_mapping_raises(self, mod, tmp_path: Path):
        bad = tmp_path / "ac.md"
        bad.write_text("```yaml\n# docs-stats\n- just\n- a\n- list\n```\n")
        with pytest.raises(mod.DocsStatsError, match="must be a mapping"):
            mod.load_docs_stats(bad)

    def test_missing_required_field_raises(self, mod, tmp_path: Path):
        bad = tmp_path / "ac.md"
        bad.write_text(
            '```yaml\n# docs-stats\nlast_updated: "2026-01-01"\ngraph_nodes: 1\n```\n'
        )
        with pytest.raises(mod.DocsStatsError, match="failed validation"):
            mod.load_docs_stats(bad)

    def test_negative_int_rejected(self, mod, tmp_path: Path):
        bad = tmp_path / "ac.md"
        bad.write_text(
            "```yaml\n# docs-stats\n"
            'last_updated: "2026-01-01"\n'
            "graph_nodes: -1\n"
            "graph_edges: 0\n"
            "problem_mentions: 0\n"
            "problem_concepts: 0\n"
            'sanity_checks: "5/5 passing"\n'
            "completed_sprints: 1\n"
            "tests_passing: 1\n"
            "```\n"
        )
        with pytest.raises(mod.DocsStatsError, match="failed validation"):
            mod.load_docs_stats(bad)


# =============================================================================
# Backlog parser (AC-4, AC-10)
# =============================================================================


class TestParseBacklog:
    def test_parses_variable_column_categories(self, mod):
        items = mod.parse_backlog(FIXTURE_DIR / "BACKLOG.md")
        ids = [r["id"] for r in items]
        assert ids == ["S-1", "S-3", "D-1", "D-2", "V-1", "V-2"]

    def test_marks_resolved_from_strikethrough(self, mod):
        items = {r["id"]: r for r in mod.parse_backlog(FIXTURE_DIR / "BACKLOG.md")}
        assert items["S-1"]["resolved"] is True
        assert items["D-1"]["resolved"] is True
        assert items["S-3"]["resolved"] is False

    def test_assigns_category_from_heading(self, mod):
        items = {r["id"]: r for r in mod.parse_backlog(FIXTURE_DIR / "BACKLOG.md")}
        assert items["S-1"]["category"] == "Stabilization & Test Fixes"
        assert items["D-1"]["category"] == "Real Data Ingestion & Validation"
        assert items["V-1"]["category"] == "Unvalidated Success Criteria"

    def test_category_7_uses_its_own_headers(self, mod):
        items = {r["id"]: r for r in mod.parse_backlog(FIXTURE_DIR / "BACKLOG.md")}
        assert items["V-1"]["criterion"].startswith("Extraction F1")
        assert items["V-1"]["source"] == "productContext.md"
        assert items["V-1"]["status"] == "Not measured"
        # "feature" / "priority" absent because V-table has different schema
        assert "feature" not in items["V-1"]
        assert "priority" not in items["V-1"]

    def test_strips_markdown_emphasis_from_cells(self, mod):
        items = {r["id"]: r for r in mod.parse_backlog(FIXTURE_DIR / "BACKLOG.md")}
        assert items["S-1"]["status"] == "Resolved"  # raw cell had `**Resolved**`
        assert items["S-1"]["priority"] == "Critical"  # raw cell had `~~Critical~~`

    def test_missing_file_returns_empty(self, mod, tmp_path: Path, caplog):
        with caplog.at_level(logging.WARNING, logger="generate_site_data"):
            assert mod.parse_backlog(tmp_path / "missing.md") == []
        assert any("BACKLOG.md not found" in m for m in caplog.messages)

    def test_warns_on_unparseable_row(self, mod, tmp_path: Path, caplog):
        bad = tmp_path / "bl.md"
        bad.write_text(
            "## Category 1: Test\n\n"
            "| # | Feature | Status | Priority | Notes |\n"
            "|---|---------|--------|----------|-------|\n"
            "| S-1 | Good | Done | High | ok |\n"
            "| nope | not an id | ? | ? | bad row |\n"
        )
        with caplog.at_level(logging.WARNING, logger="generate_site_data"):
            items = mod.parse_backlog(bad)
        assert [r["id"] for r in items] == ["S-1"]
        assert any("Skipping unparseable backlog row" in m for m in caplog.messages)

    def test_extra_cells_without_header_fall_back_to_col_keys(
        self, mod, tmp_path: Path
    ):
        bad = tmp_path / "bl.md"
        # Header has 3 columns; rows have 5 → trailing cells land under col_N.
        bad.write_text(
            "## Category 1: Mismatch\n\n"
            "| # | Feature | Status |\n"
            "|---|---------|--------|\n"
            "| S-1 | Good | Done | extra1 | extra2 |\n"
        )
        items = mod.parse_backlog(bad)
        assert items[0]["feature"] == "Good"
        assert items[0]["status"] == "Done"
        assert items[0]["col_3"] == "extra1"
        assert items[0]["col_4"] == "extra2"

    def test_fails_when_parse_ratio_below_threshold(self, mod, tmp_path: Path):
        bad = tmp_path / "bl.md"
        bad.write_text(
            "## Category 1: Test\n\n"
            "| # | Feature | Status | Priority | Notes |\n"
            "|---|---------|--------|----------|-------|\n"
            "| oops | a | b | c | d |\n"
            "| nope | a | b | c | d |\n"
            "| S-1 | Good | Done | High | ok |\n"
        )
        with pytest.raises(ValueError, match="below minimum ratio"):
            mod.parse_backlog(bad)


# =============================================================================
# Sprint parser (AC-7)
# =============================================================================


class TestParseSprints:
    def test_extracts_number_name_status_filename(self, mod):
        items = mod.parse_sprints(FIXTURE_DIR / "sprints")
        assert items == [
            {
                "number": 0,
                "name": "Fixture Setup",
                "status": "Complete",
                "filename": "sprint-00-fixture-setup.md",
            },
            {
                "number": 1,
                "name": "Fixture Ingest",
                "status": "In Progress",
                "filename": "sprint-01-fixture-ingest.md",
            },
        ]

    def test_missing_dir_returns_empty(self, mod, tmp_path: Path, caplog):
        with caplog.at_level(logging.WARNING, logger="generate_site_data"):
            assert mod.parse_sprints(tmp_path / "nope") == []
        assert any("sprints dir not found" in m for m in caplog.messages)

    def test_defaults_status_when_missing(self, mod, tmp_path: Path):
        (tmp_path / "sprint-09-test.md").write_text("# Sprint 09: Noteless\n\nNo status.")
        items = mod.parse_sprints(tmp_path)
        assert items[0]["status"] == "Unknown"

    def test_warns_on_bad_heading(self, mod, tmp_path: Path, caplog):
        (tmp_path / "sprint-05-weird.md").write_text("No heading at all\n")
        with caplog.at_level(logging.WARNING, logger="generate_site_data"):
            items = mod.parse_sprints(tmp_path)
        assert items == []
        assert any("no '# Sprint N: name' heading" in m for m in caplog.messages)


# =============================================================================
# Snapshot test (AC-16)
# =============================================================================


class TestSnapshot:
    def _assert_matches_golden(self, produced: Path, golden: Path) -> None:
        produced_data = yaml.safe_load(produced.read_text())
        golden_data = yaml.safe_load(golden.read_text())
        assert produced_data == golden_data, (
            f"{produced.name} drifted from golden fixture"
        )

    def test_snapshot_matches_golden(self, mod, fixture_repo: Path):
        paths = mod.Paths.from_root(fixture_repo)
        mod.generate(paths)
        for name in ("status.yml", "backlog.yml", "sprints.yml"):
            self._assert_matches_golden(
                fixture_repo / "docs/_data" / name,
                GOLDEN_DIR / name,
            )


# =============================================================================
# main() exit codes (AC-10, AC-15)
# =============================================================================


class TestMainExitCodes:
    def test_returns_zero_on_success(self, mod, fixture_repo: Path):
        rc = mod.main(["--root", str(fixture_repo)])
        assert rc == 0

    def test_returns_two_when_docs_stats_missing(self, mod, tmp_path: Path):
        (tmp_path / "llm/memory_bank").mkdir(parents=True)
        (tmp_path / "llm/features").mkdir(parents=True)
        (tmp_path / "construction/sprints").mkdir(parents=True)
        (tmp_path / "llm/memory_bank/activeContext.md").write_text(
            "# No stats block here.\n"
        )
        rc = mod.main(["--root", str(tmp_path)])
        assert rc == 2

    def test_returns_three_when_backlog_unparseable(
        self, mod, tmp_path: Path, monkeypatch
    ):
        (tmp_path / "llm/memory_bank").mkdir(parents=True)
        (tmp_path / "llm/features").mkdir(parents=True)
        (tmp_path / "construction/sprints").mkdir(parents=True)
        (tmp_path / "llm/memory_bank/activeContext.md").write_text(
            (FIXTURE_DIR / "activeContext.md").read_text()
        )
        (tmp_path / "llm/features/BACKLOG.md").write_text(
            "## Category 1: Bad\n\n"
            "| # | Feature | Status | Priority | Notes |\n"
            "|---|---------|--------|----------|-------|\n"
            "| nope1 | a | b | c | d |\n"
            "| nope2 | a | b | c | d |\n"
            "| nope3 | a | b | c | d |\n"
        )
        rc = mod.main(["--root", str(tmp_path)])
        assert rc == 3

    def test_default_root_resolves_from_script_location(self, mod):
        paths = mod.Paths.from_root()
        # The script lives under <repo>/.github/scripts/, so the default root
        # is the repo. Verify by checking that the backlog path ends with
        # llm/features/BACKLOG.md relative to that root.
        assert paths.backlog == paths.root / "llm/features/BACKLOG.md"


# =============================================================================
# Paths
# =============================================================================


class TestPaths:
    def test_explicit_root_overrides_default(self, mod, tmp_path: Path):
        p = mod.Paths.from_root(tmp_path)
        assert p.root == tmp_path
        assert p.backlog == tmp_path / "llm/features/BACKLOG.md"
        assert p.active_context == tmp_path / "llm/memory_bank/activeContext.md"
        assert p.sprints_dir == tmp_path / "construction/sprints"
        assert p.out_dir == tmp_path / "docs/_data"
