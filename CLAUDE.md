# CLAUDE.md - Project Context for Claude Code

## IMPORTANT: Design-First Workflow (Constellize, 2026-04-15)

**⚠️ CRITICAL: No implementation without a spec.**

The previous `construction-agent` / `memory-agent` sub-agents were superseded on 2026-04-15 by the **Constellize** methodology. Use the Constellize skills and personas instead:

**Feature workflow** (`.claude/skills/constellize-feature-*`):
- `/constellize-feature-specify` — build a feature spec (repo analysis → problem → interview → draft → review). Stores spec in `llm/features/`. Does NOT implement.
- `/constellize-feature-implement` — implement from spec (context load → Star-Gap → TDD → adversarial review → integration)
- `/constellize-feature-verify` — gate a feature against test integrity, health checks, deployment readiness, maintainability

**Memory workflow** (`.claude/skills/constellize-memory-*`):
- `/constellize-memory-establish` — initialize memory bank for a new project
- `/constellize-memory-update` — sync memory bank with current state (supports `--full`)
- `/constellize-memory-revise` — restructure when files grow unwieldy
- `/constellize-memory-recover` — audit and rewrite a neglected bank

**Personas** (`.claude/agents/`): `construction-lead`, `knowledge-steward`, `feature-architect`.

📁 **Locations:** Feature specs in `llm/features/` (master index: `BACKLOG.md`), memory bank in `llm/memory_bank/`. `llm/sprints/` preserves completed sprint history (still read by the GitHub Pages generator). Legacy `memory-bank/` and `construction/{design,requirements,backlog}` folders were deleted 2026-07-07 — content superseded by the above.

---

## Current Work (2025-12-19)

### In Progress: Deploy DenarioApp to GCP for Testing
1. **Fix Applied**: PDF text extraction using PyMuPDF (fitz) in components.py
2. **PR Closed**: #10 - will reopen after testing
3. **Current**: Third deployment in progress

### The Fix
- Added `extract_text_from_file()` helper function using PyMuPDF
- File uploaders now accept .pdf, .md, .txt files
- PDFs are converted to text using `fitz.open()` and `page.get_text()`
- Proper error handling for empty PDFs or extraction failures

### Build Progress
- **First attempt**: Failed - permission denied (Dockerfile user issue)
- **Second attempt**: SUCCESS - but fix was wrong (blocked PDFs instead of extracting text)
- **Third attempt**: In progress - proper PDF text extraction
- **Deployed URL**: https://denario-app-tqpsba7pza-uc.a.run.app

### Testing
1. Go to the deployed URL
2. Upload a PDF file to one of the file uploaders
3. Text should be extracted and used (not blocked)

### To Resume Deployment
```bash
# Manual build command
cd c:\Code\Git\DenarioApp
gcloud builds submit --config=cloudbuild.yaml --project=vt-gcp-00042 --substitutions=COMMIT_SHA=$(git rev-parse HEAD)

# Check build status
gcloud builds list --project=vt-gcp-00042 --limit=5
```

### Related Repositories
| Repo | Purpose | Location |
|------|---------|----------|
| Denario | Core library | c:\Code\Git\Denario |
| DenarioApp | Streamlit UI (fix applied) | c:\Code\Git\DenarioApp |
| agentic-kg | Extension project | c:\Code\Git\agentic-kg |

## Pending Work

### Bug: arXiv_pdf variable scope in Denario core
- **Location**: c:\Code\Git\Denario\denario\langgraph_agents\literature.py:114
- **Bug**: `arXiv_pdf` and `arXiv_pdf2` referenced before assignment when `externalID` is None
- **Status**: Not yet fixed

## Architecture Clarification
- **DenarioApp** = Streamlit UI (port 8501) - has the file upload bug
- **Denario** = Core library (agents, paper generation)
- The UI imports and uses the core library

## Quick Reference

### GCP Deployment
- Project: `vt-gcp-00042`
- Region: `us-central1`
- Existing Service: `denario` (core) at https://denario-542888988741.us-central1.run.app
- New Service: `denario-app` (UI) at https://denario-app-tqpsba7pza-uc.a.run.app

### Key Files to Remember
- llm/memory_bank/activeContext.md - Current work phase
- llm/memory_bank/techContext.md - Technical details
- llm/memory_bank/progress.md - Task tracking
- llm/memory_bank/productContext.md - Problem statement + success criteria
- llm/features/BACKLOG.md - Master feature catalog (every spec + status)

## Notes for Future Sessions
- Always read this file AND llm/memory_bank/*.md on context reset
- Need to test DenarioApp fix before reopening PR
- Denario core still has arXiv_pdf scope bug to fix

<!-- BEGIN agentic-governance: repository layout -->
## Repository layout: two planes

The source of truth for this rule is agentic-governance
`llm/governance/project-operating-system.md` §Repository Areas, and the
decision behind it is
`llm/governance/adr/0001-llm-control-plane-docs-data-plane.md`. Both are
paths **inside the canonical repo**: resolve them against the
`Canon checkout` declared in `llm/governance/governance-delta.md`
§Canon Location, or read them at
<https://github.com/djjay0131/agentic-governance>.
Where this file and §Repository Areas disagree, §Repository Areas
wins. The paths below are the ones this repo declares in
`llm/governance/governance-delta.md` §Repository Layout.

The split is by **role**, not by authorship. Who wrote a document
decides nothing; what the document *does* decides everything.

**Control plane — the `llm/` tree.** Artifacts that govern, plan,
record, review, or operate this repository: governance policy and
the governance delta, role charters, workflows, prompts and skills,
design specs acting as design authority, implementation plans,
backlog and feature specs, the memory bank, ADRs, roadmaps,
execution patterns, and review and retrospective records.
Control-plane documents are sources of truth, and nothing downstream
is authoritative over them.

**Data plane — the artifacts tree (`docs/`).** Project and
domain deliverables, external material, and derived views of
control-plane content: product and API documentation, project/domain
technical specifications and reference material, vendor and
third-party specifications, external proposals, research sources,
PDFs, diagrams, datasets, and published sites and generated views.
Nothing here governs how this repository is operated.

**No artifact that governs repository operation lives in the
artifacts tree, and any view placed there must name the `llm/`
document it projects.**

### Before you create any document: Q1, then Q2

**Q1 — Does this artifact control how the repository is governed,
planned, remembered, reviewed, or operated?** YES → control plane
(`llm/`). This is governance policy and the governance delta, role
charters, workflows, prompts and skills, design specs acting as
design authority, implementation plans, backlog and feature specs,
the memory bank, ADRs, roadmaps, execution patterns, and review and
retrospective records.

**Q2 — Otherwise: is it a project or domain deliverable, technical
reference, external source, specification, or generated project
documentation?** YES → the artifacts tree (`docs/`). This
is product and API documentation, project/domain technical
specifications and reference material, vendor and third-party
specifications, external proposals, research sources, PDFs,
diagrams, datasets, and published sites and generated views. A
derived view of a control-plane document belongs here too, and must
name the `llm/` document it projects.

**Otherwise — do not invent a location.** Use the existing structure
the artifact plainly belongs to (`src/`, `tests/`, `.github/`), or
escalate to the Repository Steward.

If the answer to Q1 is unclear, treat the artifact as control plane.
Misfiling a source of truth as an artifact is the failure this rule
exists to prevent; the reverse is cheap to correct.

### Canonical destinations

| Content | Destination |
|---|---|
| Governance policy, the delta, patterns | `llm/governance/` |
| Architecture Decision Records | `llm/governance/adr/` |
| Sprint plans and sprint history | `llm/sprints/` |
| Feature specs and backlog | `llm/features/` |
| Memory bank | `llm/memory_bank/` |
| Product/domain docs, external material, published views | `docs/` |

ADRs are control plane: an ADR *is* the decision, not a report of
one. A published ADR index may be generated into the artifacts tree
as a derived view.

This repo declares only the paths it uses. An absent slot is not a
violation; an undeclared path is. If a document needs a home that is
not listed above, do not invent a path: use the existing structure it
plainly belongs to, or escalate to the Repository Steward.

### Tool-contract paths

Some paths are fixed by a tool or a platform rather than chosen by
this project. They sit outside both planes and are exempt. The class
is closed:

- `.github/` — workflows, issue templates, PR templates.
- `.claude/` — Claude Code's own configuration: `settings.json`,
  `skills/`, `agents/`, `commands/`.
- `.claude-plugin/` — the marketplace manifest.
- The plugin payload root — whatever directory a marketplace
  `source` field points at.
- Root-convention files: `README.md`, `CHANGELOG.md`, `VERSION`,
  `CONTRIBUTING.md`, `LICENSE`, `CLAUDE.md`, `AGENTS.md`.

The exemption covers **location only**. A tool default is never
design authority. Where a tool writes control-plane content into the
artifacts tree, override the tool here and relocate the output.

### Output-location preferences (these override tool defaults)

These are the repository owner's standing **user preferences for
spec and plan location**. They take precedence over any skill's,
plugin's, or tool's default output path.

**Design specs and brainstorming output.** This repo declares no
spec directory in `llm/governance/governance-delta.md`
§Repository Layout, so do not invent one: follow §Canonical
destinations above — write under the declared `llm/` path the
document plainly belongs to, or escalate to the Repository Steward.
**Never** write to `docs/superpowers/specs/`, and never create a
`docs/superpowers/` directory.

**Implementation plans.** This repo declares no plans directory
either. Same rule: follow §Canonical destinations above — write
under the declared `llm/` path the plan plainly belongs to, or
escalate to the Repository Steward. **Never** write to
`docs/superpowers/plans/`, and never create a `docs/superpowers/`
directory.

This applies to the `obra/superpowers` skills — `brainstorming`,
`writing-plans`, and anything downstream of them — and to any other
tool with a hardcoded documentation path. If a skill instructs you
to write a spec or a plan somewhere else, this preference wins:
create the document under `llm/` instead, and do not mirror or copy
it into the artifacts tree.
<!-- END agentic-governance: repository layout -->
