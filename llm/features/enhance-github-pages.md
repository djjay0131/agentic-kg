# Feature: Enhance GitHub Pages Site

**Status:** IMPLEMENTED (Phase A — 2026-04-21)
**Date:** 2026-04-16
**Author:** Feature Architect (AI-assisted)
**Depends On:** None (existing Pages site + CI workflow in place)

## Phased Delivery

This feature ships in two phases to separate technical migration from content authoring:

- **Phase A — Infrastructure Migration** (sized for one sprint). Theme, templates, data generator, workflow, navigation, PR preview deploys, automated link checking, structured stats block + snapshot tests, cleanup. Ships with minimal placeholder content (one paragraph per page) to prove the structure works. Acceptance criteria AC-1, AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10, AC-11, AC-12, AC-15, AC-16.
- **Phase B — Content Population & Quality Gates** (can run in parallel or follow). Authoring real prose for all 9 public-section content areas (overview, architecture, how-it-works, related work, screenshots, quickstart, roadmap, contributing, changelog). Adds Lighthouse CI thresholds and optional Playwright smoke test. Acceptance criteria AC-3, AC-13 (and AC-14 if Playwright adopted).

Phase A must complete and be green in CI before Phase B starts.

## Problem

The current GitHub Pages site is a narrow, partially broken showcase. The generator (`generate_docs.py`) is a 300-line regex script that directly manipulates inline HTML — fragile, with broken parsers (milestone regex expects `### M1: name ✅` but `progress.md` uses tables; status extraction uses obsolete patterns). It reads the stale `memory-bank/` path instead of the authoritative `llm/memory_bank/`. There is no unified navigation, no search, inconsistent styling across pages, no public-facing project overview, and the backlog page is a manual snapshot. Researchers, collaborators, and the team itself cannot discover what the system does, how far along it is, or what's next without reading raw markdown in the repo.

## Goals

- Rebuild the Pages site on Jekyll `just-the-docs` theme with dynamic sidebar navigation, search, and breadcrumbs
- Create a **public section** covering: project overview, architecture, how-it-works, related work/papers, screenshots, quickstart, roadmap summary, contributing info, and changelog
- Create an **internal status section** with auto-generated backlog (from `llm/features/BACKLOG.md`), sprint list (from `construction/sprints/`), and project status dashboard (from `llm/memory_bank/`)
- Replace `generate_docs.py` with a lean data-emitter script (~60 lines) that outputs YAML data files consumed by Liquid templates — no HTML string manipulation
- Fix workflow trigger paths: watch `llm/memory_bank/**`, `llm/features/**`, `construction/sprints/**`, `docs/**` instead of stale `memory-bank/**` (closes P-7)
- Auto-regenerate backlog page from source on every push (closes P-6)
- Dynamic navigation: adding a new markdown page with valid frontmatter causes it to appear in nav automatically — no menu config edits required

## Non-Goals

- Consolidating content from the `agentic-kg-rtsi-dt-proposal` repo (link out instead)
- Custom domain or DNS setup
- Pixel-perfect branding or custom CSS theme beyond `just-the-docs` defaults
- Interactive graph visualization embedded in the site (link to Neo4j browser or screenshots only)
- Blog/CMS functionality (changelog is a single manually-edited markdown file)

## User Stories

- As a researcher, I want a project overview page so I can understand what agentic-kg does in 60 seconds without reading the repo.
- As a collaborator, I want a quickstart page so I can run the system locally without guessing at setup steps.
- As a team member, I want the backlog and sprint status to stay current automatically so I never have to manually update the site.
- As any visitor, I want sidebar navigation and search so I can find any page from any other page in one click.
- As a maintainer, I want to add a new page by creating a markdown file with frontmatter — no template or config edits required.

## Design Approach

### Architecture

```
Source of truth          Data emitter (CI)         Jekyll build (CI)       Published
─────────────          ────────────────          ────────────────       ──────────
llm/memory_bank/*.md ─┐
llm/features/BACKLOG.md┼─► generate_site_data.py ─► docs/_data/*.yml ─┐
construction/sprints/ ─┘                                               ├─► jekyll build ─► gh-pages branch
                                                     docs/**/*.md ─────┘     (just-the-docs theme)
```

### Directory structure

```
docs/
├── _config.yml                  # just-the-docs theme config
├── Gemfile                      # gem dependency
├── _data/                       # ← CI-regenerated YAML
│   ├── status.yml
│   ├── sprints.yml
│   └── backlog.yml
├── index.md                     # Landing page (nav_order: 1)
├── about/                       # Public section
│   ├── overview.md              # One-paragraph description
│   ├── architecture.md          # Three-layer diagram
│   ├── how-it-works.md          # Ingestion pipeline flow
│   ├── related-work.md          # Paper links, references
│   ├── quickstart.md            # Local dev setup
│   ├── roadmap.md               # High-level roadmap (renders backlog summary)
│   ├── screenshots.md           # UI/graph screenshots or GIFs
│   └── contributing.md          # Contact, contributing guidelines
├── status/                      # Internal section
│   ├── index.md                 # Dashboard (renders _data/status.yml)
│   ├── backlog.md               # Full backlog table (renders _data/backlog.yml)
│   ├── sprints.md               # Sprint list (renders _data/sprints.yml)
│   └── changelog.md             # What's new (manual edits)
└── _includes/
    ├── backlog-table.html       # Liquid template for backlog rendering
    ├── sprint-list.html         # Liquid template for sprint cards
    └── status-badge.html        # Reusable status badge partial
```

### Navigation model

`just-the-docs` generates sidebar nav from page frontmatter:

```yaml
# docs/about/architecture.md
---
title: Architecture
parent: About
nav_order: 2
---
```

Parent pages declare themselves:

```yaml
# docs/about/overview.md (or an about/index.md)
---
title: About
nav_order: 2
has_children: true
---
```

Adding a page = creating a `.md` file with the right `parent` and `nav_order`. The sidebar updates automatically on next build. No menu YAML or config edits needed.

### Data pipeline

**`generate_site_data.py`** replaces `generate_docs.py`. It:
1. Reads `llm/memory_bank/activeContext.md` → extracts the **fenced `# docs-stats` YAML block** (authoritative source, not prose) → emits `_data/status.yml`
2. Reads `llm/features/BACKLOG.md` → parses category tables → emits `_data/backlog.yml` (flat list of feature records with id, name, status, priority, category)
3. Reads `construction/sprints/sprint-*.md` → extracts number, name, status → emits `_data/sprints.yml`

Templates use `{% for item in site.data.backlog.items %}` to render tables, badges, counts. Content stays in markdown source files; dynamic data comes from YAML.

### Structured source: docs-stats block

Dashboard numbers live in a fenced YAML block in `activeContext.md` — prose can be reworded freely without breaking the dashboard:

````markdown
<!-- docs-stats: authoritative source for the Pages status dashboard. Keep in sync with prose. -->
```yaml
# docs-stats
last_updated: 2026-04-16
graph_nodes: 282
graph_edges: 151
problem_mentions: 18
problem_concepts: 18
sanity_checks: "5/5 passing"
```
````

The generator validates the extracted block against a Pydantic `DocsStats` model. Missing block, missing field, or type mismatch → exit non-zero with a clear error. The `constellize:memory:update` skill should be updated to keep this block in sync when it modifies `activeContext.md` — captured as a follow-on task, not a hard dependency.

### Validation: catching drift before deploy

Three overlapping checks:

1. **Schema assertion in generator** — Pydantic validation on every run (per above).
2. **Snapshot unit test** — `packages/core/tests/docs/test_generate_site_data.py` runs the generator against fixture files mirroring current memory-bank format; asserts `_data/*.yml` matches golden YAML. Runs in the standard test workflow on every PR. Drift surfaces at PR time, not after deploy.
3. **PR preview deploy** (AC-11) — renders the site and the validator comment-bots the PR. Reviewer sees the rendered dashboard before merge.

### Workflow changes

Two workflows: `update-docs.yml` (production deploy on merge) and `preview-docs.yml` (PR preview deploy).

```yaml
# .github/workflows/update-docs.yml
on:
  push:
    branches: [master]
    paths:
      - 'llm/memory_bank/**'
      - 'llm/features/**'
      - 'construction/sprints/**'
      - 'docs/**'
      - '.github/scripts/generate_site_data.py'
  workflow_dispatch: {}

concurrency:
  group: pages-production
  cancel-in-progress: true

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install pyyaml
      - run: python .github/scripts/generate_site_data.py
      - uses: ruby/setup-ruby@v1
        with: { ruby-version: '3.2', bundler-cache: true, working-directory: docs }
      - run: cd docs && bundle exec jekyll build --strict_front_matter
      - name: Validate HTML and links
        uses: chabad360/htmlproofer@master
        with:
          directory: docs/_site
          arguments: --disable-external --check-html --check-favicon
      - uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: docs/_site
```

```yaml
# .github/workflows/preview-docs.yml
on:
  pull_request:
    types: [opened, reopened, synchronize, closed]
    paths:
      - 'llm/memory_bank/**'
      - 'llm/features/**'
      - 'construction/sprints/**'
      - 'docs/**'
      - '.github/scripts/generate_site_data.py'

concurrency:
  group: pages-preview-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  build-preview:
    if: github.event.action != 'closed'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install pyyaml
      - run: python .github/scripts/generate_site_data.py
      - uses: ruby/setup-ruby@v1
        with: { ruby-version: '3.2', bundler-cache: true, working-directory: docs }
      - run: cd docs && bundle exec jekyll build --baseurl "/agentic-kg/previews/pr-${{ github.event.pull_request.number }}" --strict_front_matter
      - name: Validate HTML and links
        uses: chabad360/htmlproofer@master
        with:
          directory: docs/_site
          arguments: --disable-external --check-html --check-favicon
      - uses: rossjrw/pr-preview-action@v1
        with:
          source-dir: docs/_site
          preview-branch: gh-pages
          umbrella-dir: previews

  cleanup-preview:
    if: github.event.action == 'closed'
    runs-on: ubuntu-latest
    steps:
      - uses: rossjrw/pr-preview-action@v1
        with:
          action: remove
          preview-branch: gh-pages
          umbrella-dir: previews
```

**Result:** Every PR gets a preview at `https://djjay0131.github.io/agentic-kg/previews/pr-<N>/` with a bot comment linking to it; HTMLProofer gates both preview and production builds; preview is auto-removed when PR closes.

### Phase B quality gates (optional until content exists)

Added as a separate job in `update-docs.yml` once Phase B begins:

```yaml
  lighthouse:
    needs: build-and-deploy
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: treosh/lighthouse-ci-action@v11
        with:
          urls: |
            https://djjay0131.github.io/agentic-kg/
            https://djjay0131.github.io/agentic-kg/about/architecture
            https://djjay0131.github.io/agentic-kg/status/
          configPath: './.lighthouserc.json'
```

Thresholds (`.lighthouserc.json`): accessibility ≥ 0.9, SEO ≥ 0.9, best-practices ≥ 0.9, performance ≥ 0.8.

Optional Playwright smoke test (run in CI on preview builds): visits every page in the sidebar, asserts no 4xx/5xx, no console errors, and takes a full-page screenshot for visual diff against baseline stored in `docs/__visual_baseline/`.

### Cleanup

Delete on migration:
- `docs/index.html` (replaced by `docs/index.md`)
- `docs/sprints.html`, `docs/architecture.html`, `docs/progress.html` (replaced by markdown pages)
- `docs/backlog.md` (current manual snapshot — replaced by data-driven version)
- `.github/scripts/generate_docs.py` (replaced by `generate_site_data.py`)

Preserve:
- `docs/SERVICE_INVENTORY.md` (move into `status/` section)
- `docs/_config.yml` (overwrite with new config)

## Sample Implementation

### `generate_site_data.py` (core logic, ~55 lines)

```python
"""Emit YAML data files for Jekyll. No HTML. Replaces generate_docs.py."""
import re, yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT / "llm/memory_bank"
FEATURES = ROOT / "llm/features"
SPRINTS = ROOT / "construction/sprints"
OUT = ROOT / "docs/_data"

def parse_backlog(path: Path) -> list[dict]:
    items, cat = [], None
    for line in path.read_text().splitlines():
        m = re.match(r"^## Category \d+\s*[:—\-]\s*(.+)", line)
        if m:
            cat = m.group(1).strip()
            continue
        row = re.match(r"^\|\s*(~~)?([A-Z]-\d+[a-z]?)(~~)?\s*\|(.+)\|$", line)
        if not row:
            continue
        cells = [c.strip().replace("*", "") for c in row.group(4).split("|")]
        if len(cells) < 3 or cells[0].startswith("--"):
            continue
        items.append({
            "id": row.group(2),
            "resolved": bool(row.group(1)),
            "category": cat,
            "feature": cells[0].lstrip("~").rstrip("~"),
            "status": cells[1],
            "priority": cells[2],
        })
    return items

def parse_sprints() -> list[dict]:
    out = []
    for f in sorted(SPRINTS.glob("sprint-*.md")):
        text = f.read_text()
        num = re.search(r"# Sprint (\d+):", text)
        name = re.search(r"# Sprint \d+: (.+)", text)
        status = re.search(r"\*\*Status:\*\*\s*(.+)", text)
        if num:
            out.append({
                "number": int(num.group(1)),
                "name": name.group(1).strip() if name else f.stem,
                "status": status.group(1).strip() if status else "Unknown",
                "filename": f.name,
            })
    return out

def snapshot_status() -> dict:
    active = (MEMORY / "activeContext.md").read_text()
    progress = (MEMORY / "progress.md").read_text()
    updated = re.search(r"Last updated:\s*(\S+)", active)
    nodes = re.search(r"(\d+)\s+nodes", active)
    edges = re.search(r"(\d+)\s+edges", active)
    return {
        "last_updated": updated.group(1) if updated else "unknown",
        "graph_nodes": int(nodes.group(1)) if nodes else None,
        "graph_edges": int(edges.group(1)) if edges else None,
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "backlog.yml").write_text(yaml.safe_dump({"items": parse_backlog(FEATURES / "BACKLOG.md")}))
    (OUT / "sprints.yml").write_text(yaml.safe_dump({"items": parse_sprints()}))
    (OUT / "status.yml").write_text(yaml.safe_dump(snapshot_status()))

if __name__ == "__main__":
    main()
```

### `docs/status/backlog.md` (Liquid template consuming data)

```markdown
---
title: Backlog
parent: Status
nav_order: 2
---

# Feature Backlog

{% assign active = site.data.backlog.items | where: "resolved", false %}
{% assign done = site.data.backlog.items | where: "resolved", true %}

**{{ active | size }}** active features, **{{ done | size }}** resolved.

{% include backlog-table.html items=active %}

<details>
<summary>Resolved ({{ done | size }})</summary>
{% include backlog-table.html items=done %}
</details>
```

## Edge Cases & Error Handling

### Empty or missing source files
- **Scenario**: `BACKLOG.md` or a sprint file doesn't exist or is empty
- **Behavior**: `generate_site_data.py` emits `items: []` in the YAML; Liquid renders "No items" instead of crashing
- **Test**: Delete a sprint file, run generator, verify YAML is valid and Jekyll builds

### Malformed table rows in BACKLOG.md
- **Scenario**: A table row doesn't match the expected `| ID | ... |` pattern
- **Behavior**: Row is silently skipped; generator logs a warning to stderr
- **Test**: Insert a malformed row, run generator, verify other rows still parse

### Page with missing or invalid frontmatter
- **Scenario**: A contributor adds a markdown file without frontmatter
- **Behavior**: `jekyll build --strict_front_matter` fails the CI build with a clear error
- **Test**: Add a file without `---` header, verify build fails

### Concurrent workflow runs
- **Scenario**: Two pushes in quick succession both trigger the workflow
- **Behavior**: GitHub Actions `concurrency` group cancels the earlier run; latest push wins
- **Test**: Verify `concurrency` key in workflow YAML

## Acceptance Criteria

### AC-1: Site renders with just-the-docs theme
- **Given** the docs/ directory contains `_config.yml` with `theme: just-the-docs` and a `Gemfile`
- **When** `bundle exec jekyll build --strict_front_matter` runs
- **Then** `_site/` is generated with sidebar navigation, search bar, and breadcrumbs; zero build warnings

### AC-2: Dynamic navigation
- **Given** a new markdown file is added to `docs/about/` with valid `parent: About` and `nav_order` frontmatter
- **When** Jekyll builds
- **Then** the new page appears in the sidebar under "About" without editing any config or template file

### AC-3: Public landing content
- **Given** the site is deployed
- **When** a visitor navigates to the root URL
- **Then** they find pages for: overview, architecture, how-it-works, related work, screenshots, quickstart, roadmap, contributing, and changelog — all reachable from the sidebar
- **Phase A completion:** each page exists with placeholder content (≥1 paragraph + TODO marker)
- **Phase B completion:** each page has real, reviewed content meeting its stated purpose

### AC-10: Generator validates parse completeness
- **Given** `generate_site_data.py` runs against `BACKLOG.md`
- **When** fewer than 50% of detected table rows parse into items
- **Then** the script logs a warning to stderr and exits with non-zero status, failing the CI build visibly

### AC-11: PR preview deploys
- **Given** a PR is opened that modifies `docs/**` or any watched source path
- **When** the `preview-docs.yml` workflow completes
- **Then** the site is deployed to `https://djjay0131.github.io/agentic-kg/previews/pr-<N>/` and a bot comment with the preview URL appears on the PR
- **And** when the PR is closed, the preview directory is removed from the `gh-pages` branch

### AC-12: HTMLProofer gates every build
- **Given** either the preview or production workflow runs
- **When** the built site contains a broken internal link, missing image, or malformed HTML
- **Then** the HTMLProofer step fails with a specific error, blocking deploy

### AC-13: Lighthouse CI thresholds (Phase B)
- **Given** Phase B is complete with real content
- **When** Lighthouse CI runs against the production URLs
- **Then** accessibility, SEO, and best-practices scores are ≥ 0.9 and performance is ≥ 0.8

### AC-14: Playwright smoke test (Phase B, optional)
- **Given** the Playwright smoke test is adopted
- **When** it runs against a preview build
- **Then** every sidebar nav link returns 200, no console errors occur, and a visual diff against baseline shows no unintended regressions

### AC-15: Structured stats block in activeContext.md
- **Given** `llm/memory_bank/activeContext.md` contains a fenced `# docs-stats` YAML block
- **When** `generate_site_data.py` runs
- **Then** the block is parsed and validated against a Pydantic `DocsStats` schema; if missing or invalid, the generator exits non-zero with a specific error message
- **And** `_data/status.yml` is populated from the block's values (not regex on prose)

### AC-16: Snapshot test for generator
- **Given** fixture files in `packages/core/tests/docs/fixtures/` representing current memory-bank and backlog format
- **When** the existing test workflow runs `pytest packages/core/tests/docs/`
- **Then** the generator's output matches the committed golden `_data/*.yml` files exactly
- **And** drift between prose format and generator expectations fails the PR check

### AC-4: Backlog auto-regeneration (P-6)
- **Given** a change is pushed to `llm/features/BACKLOG.md`
- **When** the CI workflow completes
- **Then** `docs/_data/backlog.yml` reflects the change, and the backlog page renders the updated table

### AC-5: Workflow watches correct paths (P-7)
- **Given** the workflow YAML `paths:` section
- **When** inspected
- **Then** it includes `llm/memory_bank/**`, `llm/features/**`, `construction/sprints/**`, `docs/**` and does NOT include `memory-bank/**`

### AC-6: Status dashboard
- **Given** `llm/memory_bank/activeContext.md` contains "282 nodes, 151 edges"
- **When** the generator runs and Jekyll builds
- **Then** the status page displays "282 nodes" and "151 edges" from `_data/status.yml`

### AC-7: Sprint list renders
- **Given** 11 sprint files exist in `construction/sprints/`
- **When** the generator runs and Jekyll builds
- **Then** the sprints page lists all 11 with number, name, and status; each links to its source file on GitHub

### AC-8: Mobile-usable
- **Given** the deployed site
- **When** viewed at 375px viewport width
- **Then** sidebar collapses to hamburger menu, content is readable without horizontal scroll

### AC-9: Old files cleaned up
- **Given** the migration is complete
- **When** `docs/` is inspected
- **Then** `index.html`, `sprints.html`, `architecture.html`, `progress.html` no longer exist; `.github/scripts/generate_docs.py` is deleted

## Technical Notes

- **Affected components**: `docs/` (full rewrite), `.github/scripts/generate_docs.py` → `generate_site_data.py`, `.github/workflows/update-docs.yml`
- **No source code changes**: this feature touches only docs, CI scripts, and workflows
- **Theme dependency**: `just-the-docs` gem (~7.0) via Gemfile, built in CI with `ruby/setup-ruby`
- **CI additions**: Ruby setup step + `jekyll build` step; Python step simplified (only `pyyaml`)
- **Concurrency**: add `concurrency: { group: 'pages', cancel-in-progress: true }` to workflow

## Dependencies

- `just-the-docs` Ruby gem (Jekyll theme) — well-maintained, 7k+ GitHub stars
- `pyyaml` Python package (already available; used by generator)
- No changes to the Python application code, tests, or infrastructure

## Open Questions

- Which screenshots/GIFs to include on the screenshots page? (Deferred — use placeholder text, populate during implementation)
- Should the changelog be manually edited or auto-generated from `git log --oneline`? (Recommend manual for now — auto-changelogs are noisy)
- Should `SERVICE_INVENTORY.md` be converted to a proper page or kept as raw markdown in `status/`? (Recommend converting to `.md` with frontmatter)
- Should `constellize:memory:update` skill be updated to maintain the `# docs-stats` block automatically? (Recommend yes — file as follow-on feature, not a blocker for Phase A)
- Should the snapshot test fixtures live under `packages/core/tests/docs/` or a new top-level `tests/docs/` directory? (Defer to implementation — depends on CI test-collection config)
