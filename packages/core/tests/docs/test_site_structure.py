"""Static assertions about the docs/ tree and CI workflows (AC-5, AC-9, AC-11, AC-12)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS = REPO_ROOT / "docs"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


# =============================================================================
# AC-9: legacy files deleted
# =============================================================================


class TestCleanup:
    @pytest.mark.parametrize(
        "legacy",
        [
            DOCS / "index.html",
            DOCS / "architecture.html",
            DOCS / "progress.html",
            DOCS / "sprints.html",
            DOCS / "backlog.md",
            REPO_ROOT / ".github" / "scripts" / "generate_docs.py",
        ],
    )
    def test_legacy_file_removed(self, legacy: Path):
        assert not legacy.exists(), f"Legacy file still present: {legacy}"


# =============================================================================
# Required Jekyll scaffolding present
# =============================================================================


REQUIRED_FILES = [
    DOCS / "_config.yml",
    DOCS / "Gemfile",
    DOCS / "index.md",
    DOCS / "about" / "index.md",
    DOCS / "about" / "overview.md",
    DOCS / "about" / "architecture.md",
    DOCS / "about" / "how-it-works.md",
    DOCS / "about" / "related-work.md",
    DOCS / "about" / "screenshots.md",
    DOCS / "about" / "quickstart.md",
    DOCS / "about" / "roadmap.md",
    DOCS / "about" / "contributing.md",
    DOCS / "status" / "index.md",
    DOCS / "status" / "backlog.md",
    DOCS / "status" / "sprints.md",
    DOCS / "status" / "changelog.md",
    DOCS / "status" / "service-inventory.md",
    DOCS / "_includes" / "backlog-table.html",
    DOCS / "_includes" / "sprint-list.html",
    DOCS / "_includes" / "status-badge.html",
]


class TestSiteSkeleton:
    @pytest.mark.parametrize("path", REQUIRED_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
    def test_file_present(self, path: Path):
        assert path.exists(), f"Missing site file: {path}"

    def test_config_declares_just_the_docs_theme(self):
        cfg = yaml.safe_load((DOCS / "_config.yml").read_text())
        assert cfg.get("remote_theme") == "just-the-docs/just-the-docs"
        assert cfg.get("search_enabled") is True

    def test_gemfile_pins_just_the_docs(self):
        gemfile = (DOCS / "Gemfile").read_text()
        assert re.search(r'gem ["\']jekyll["\']', gemfile)
        assert re.search(r'gem ["\']just-the-docs["\']', gemfile)


# =============================================================================
# Frontmatter: every public page has valid just-the-docs frontmatter
# =============================================================================


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
PUBLIC_PAGES = sorted(
    [p for p in DOCS.rglob("*.md") if p.is_file()]
)


class TestFrontmatter:
    @pytest.mark.parametrize(
        "page",
        PUBLIC_PAGES,
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_page_has_frontmatter_with_title(self, page: Path):
        text = page.read_text()
        match = FRONTMATTER_RE.match(text)
        assert match, f"{page} is missing '---' frontmatter"
        data = yaml.safe_load(match.group(1)) or {}
        assert isinstance(data, dict), f"{page} frontmatter must be a mapping"
        assert data.get("title"), f"{page} frontmatter missing 'title'"


# =============================================================================
# AC-5: update-docs workflow watches the new authoritative paths
# =============================================================================


EXPECTED_PATHS = {
    "llm/memory_bank/**",
    "llm/features/**",
    "construction/sprints/**",
    "docs/**",
}
FORBIDDEN_PATHS = {"memory-bank/**"}


def _on_block(workflow: dict) -> dict:
    # PyYAML parses the literal `on:` key as Python's `True` bool.
    if True in workflow:
        return workflow[True]
    return workflow["on"]


class TestUpdateDocsWorkflow:
    @pytest.fixture()
    def wf(self) -> dict:
        return yaml.safe_load((WORKFLOWS / "update-docs.yml").read_text())

    def test_triggers_on_push_to_master(self, wf):
        on = _on_block(wf)
        assert on["push"]["branches"] == ["master"]

    def test_watches_expected_paths(self, wf):
        on = _on_block(wf)
        paths = set(on["push"]["paths"])
        assert EXPECTED_PATHS.issubset(paths), (
            f"Missing watched paths: {EXPECTED_PATHS - paths}"
        )

    def test_does_not_watch_legacy_memory_bank(self, wf):
        on = _on_block(wf)
        paths = set(on["push"]["paths"])
        assert paths.isdisjoint(FORBIDDEN_PATHS), (
            f"Legacy path still watched: {paths & FORBIDDEN_PATHS}"
        )

    def test_has_concurrency_group(self, wf):
        concurrency = wf.get("concurrency", {})
        assert concurrency.get("group") == "pages-production"
        assert concurrency.get("cancel-in-progress") is True

    def test_runs_generator_before_jekyll(self, wf):
        steps = wf["jobs"]["build-and-deploy"]["steps"]
        step_order = [s.get("name") or s.get("uses") for s in steps]
        gen_idx = next(
            i for i, name in enumerate(step_order)
            if isinstance(name, str) and "Regenerate site data" in name
        )
        jekyll_idx = next(
            i for i, name in enumerate(step_order)
            if isinstance(name, str) and "Build Jekyll site" in name
        )
        assert gen_idx < jekyll_idx

    def test_uses_htmlproofer_to_gate_deploy(self, wf):
        steps = wf["jobs"]["build-and-deploy"]["steps"]
        used = [s.get("uses", "") for s in steps]
        assert any("htmlproofer" in u.lower() for u in used)


# =============================================================================
# AC-11: PR preview workflow
# =============================================================================


class TestPreviewDocsWorkflow:
    @pytest.fixture()
    def wf(self) -> dict:
        return yaml.safe_load((WORKFLOWS / "preview-docs.yml").read_text())

    def test_triggers_on_pull_request(self, wf):
        on = _on_block(wf)
        assert "pull_request" in on
        types = on["pull_request"]["types"]
        for needed in ("opened", "reopened", "synchronize", "closed"):
            assert needed in types, f"Missing PR type: {needed}"

    def test_watches_same_paths_as_production(self, wf):
        on = _on_block(wf)
        paths = set(on["pull_request"]["paths"])
        assert EXPECTED_PATHS.issubset(paths)
        assert paths.isdisjoint(FORBIDDEN_PATHS)

    def test_has_per_pr_concurrency_group(self, wf):
        group = wf["concurrency"]["group"]
        assert "pull_request.number" in group
        assert wf["concurrency"]["cancel-in-progress"] is True

    def test_deploy_job_skips_on_close(self, wf):
        build = wf["jobs"]["build-preview"]
        assert build.get("if") == "github.event.action != 'closed'"

    def test_cleanup_job_runs_only_on_close(self, wf):
        cleanup = wf["jobs"]["cleanup-preview"]
        assert cleanup.get("if") == "github.event.action == 'closed'"

    def test_uses_pr_preview_action(self, wf):
        used = [
            s.get("uses", "")
            for s in wf["jobs"]["build-preview"]["steps"]
        ]
        assert any("rossjrw/pr-preview-action" in u for u in used)

    def test_uses_htmlproofer_to_gate_preview(self, wf):
        used = [
            s.get("uses", "")
            for s in wf["jobs"]["build-preview"]["steps"]
        ]
        assert any("htmlproofer" in u.lower() for u in used)
