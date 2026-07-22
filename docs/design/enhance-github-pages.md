---
title: Enhance GitHub Pages
parent: Design
nav_order: 13
---

# Enhance GitHub Pages

{: .label .label-green }
VERIFIED (Phase A)

**Backlog:** enhance-github-pages · **Spec:**
[`enhance-github-pages.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/enhance-github-pages.md)

## Why

The old Pages site was a narrow, partly broken showcase. Its generator was a
~300-line regex script that emitted inline HTML by hand, with parsers that had
drifted out of sync with the source: the milestone regex expected
`### M1: name ✅` while `progress.md` had moved to tables, and it still read the
stale `memory-bank/` path instead of the authoritative `llm/memory_bank/`. There
was no unified navigation, no search, no public project overview, and the backlog
page was a hand-maintained snapshot that went stale the moment anything changed.
Nobody — researcher, collaborator, or the team itself — could discover what the
system does or how far along it is without reading raw markdown in the repo.

## What shipped

A Jekyll [`just-the-docs`](https://just-the-docs.github.io/just-the-docs/) site
with a single dynamic sidebar, search, and breadcrumbs, plus a lean data pipeline
that keeps the status pages honest. A new Python emitter reads the project's
source-of-truth files and writes three YAML data files that Liquid templates
render — so the backlog, sprint list, and status dashboard regenerate on every
push instead of being copy-pasted. Two workflows drive it: production deploys on
merge to `master`, and per-PR preview deploys with a bot comment. HTMLProofer
gates both builds against broken links and malformed HTML.

This is **Phase A** — the structure, navigation, and data pipeline. Every public
page exists and is reachable from the sidebar, but the prose is still one-paragraph
placeholder with a `<!-- TODO(phase-b): -->` marker; authoring real content is
Phase B and has not shipped. The [Reference]({{ site.baseurl }}/reference/) and
[Design]({{ site.baseurl }}/design/) sections you are reading now — including this
note — are later hand-authored content built *on top of* the Phase A structure.

## Design decisions

**Data emitter, not HTML string-slinging.** The replacement generator emits
`_data/*.yml` only — never HTML. Templates own presentation; the script owns
parsing. This is the core reversal from the old approach and the reason the
dashboard can no longer render wrong-but-confident numbers.

**Structured stats block, not regex on prose.** Dashboard numbers come from a
fenced `# docs-stats` YAML block in `activeContext.md`, validated against a
Pydantic `DocsStats` model. Prose around it can be reworded freely without
breaking the dashboard; a missing block or a bad field fails the build loudly
(exit 2) rather than silently emitting `null`. The extractor deliberately keys
on the block's *first content line* being `# docs-stats`, so a prose mention of
the marker elsewhere is ignored.

**Fail visibly on parse drift.** If fewer than 50% of BACKLOG.md's detected table
rows parse into feature records, the generator exits non-zero (exit 3) and fails
CI — the format having drifted out from under the parser is treated as a bug, not
a warning to bury. Individual malformed rows are skipped with a stderr warning so
one bad row can't blank the whole table.

**Nav is emergent, not configured.** `just-the-docs` builds the sidebar from each
page's `parent` / `nav_order` frontmatter. Adding a page is creating a markdown
file — no menu YAML or template edits. `--strict_front_matter` fails the build if
a page ships without valid frontmatter.

## How it works

- **Generator:**
  [`generate_site_data.py`](https://github.com/djjay0131/agentic-kg/blob/master/.github/scripts/generate_site_data.py)
  reads `llm/memory_bank/activeContext.md` (the `# docs-stats` block),
  `llm/features/BACKLOG.md`, and `construction/sprints/sprint-*.md`, and writes
  `docs/_data/{status,backlog,sprints}.yml`.
- **Templates:** `docs/status/*.md` render those data files via Liquid
  (`{% raw %}{% for item in site.data.backlog.items %}{% endraw %}`) and the partials in
  `docs/_includes/` (`backlog-table.html`, `sprint-list.html`, `status-badge.html`).
- **Config:**
  [`docs/_config.yml`](https://github.com/djjay0131/agentic-kg/blob/master/docs/_config.yml)
  sets the remote `just-the-docs` theme, search, breadcrumbs, and GitHub edit links.
- **Production workflow:**
  [`update-docs.yml`](https://github.com/djjay0131/agentic-kg/blob/master/.github/workflows/update-docs.yml)
  — on push to `master` under the watched paths, regenerates data, runs
  `jekyll build --strict_front_matter`, HTMLProofs the output, and deploys to
  `gh-pages` (with `keep_files` so it doesn't clobber the previews subtree).
- **Preview workflow:**
  [`preview-docs.yml`](https://github.com/djjay0131/agentic-kg/blob/master/.github/workflows/preview-docs.yml)
  — on each PR touching those paths, builds under
  `/agentic-kg/previews/pr-<N>/`, comments the URL, and removes the preview when
  the PR closes.
- **Sections built on this structure:** the domain model lives under
  [Reference]({{ site.baseurl }}/reference/) and the completed-feature notes under
  [Design]({{ site.baseurl }}/design/).

## Verification

- **Tests:** two suites under `packages/core/tests/docs/` —
  `test_generate_site_data.py` (generator behavior: docs-stats extraction,
  Pydantic validation, backlog parsing, parse-ratio gate, sprint parsing) and
  `test_site_structure.py` (static assertions on the site + workflow YAML), with
  fixture and golden files for snapshot comparison.
- **CI gates:** `--strict_front_matter` blocks pages without frontmatter;
  HTMLProofer blocks broken internal links and malformed HTML on both preview and
  production builds; `concurrency` groups cancel superseded runs.
- **Status:** VERIFIED for **Phase A only** — structure, navigation, and the data
  pipeline. Real prose (Phase B, AC-3), Lighthouse thresholds (AC-13), and the
  optional Playwright smoke test (AC-14) are deferred and not yet shipped.

## Related

- Spec: [`enhance-github-pages.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/enhance-github-pages.md)
  (full acceptance criteria and Phase A verification record)
- Builds on this structure: [Reference]({{ site.baseurl }}/reference/) and
  [Design]({{ site.baseurl }}/design/) sections
- Deferred: **Phase B** — real content for every `about/*` and `status/*` page,
  plus Lighthouse and Playwright quality gates

<!-- Shipped-vs-spec divergences (verified against code):
     - Generator is ~390 lines, not the ~55-60 line sketch in the spec — but still
       data-only (no HTML), which was the actual design intent.
     - DocsStats shipped with 8 fields (added completed_sprints, tests_passing) vs
       the 6 fields sketched in the spec's docs-stats example.
     - SERVICE_INVENTORY.md was moved into status/ as service-inventory.md, as the
       spec's "Preserve" note anticipated.
-->
