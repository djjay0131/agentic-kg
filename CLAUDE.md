# CLAUDE.md - Project Context for Claude Code

## IMPORTANT: Design-First Workflow (Constellize, 2026-04-15)

**⚠️ CRITICAL: No implementation without a spec.**

The previous `construction-agent` / `memory-agent` sub-agents were superseded on 2026-04-15 by the **Constellize** methodology. Use the Constellize skills and personas instead:

**Feature workflow** (`.claude/skills/constellize:feature:*`):
- `/constellize:feature:specify` — build a feature spec (repo analysis → problem → interview → draft → review). Stores spec in `llm/features/`. Does NOT implement.
- `/constellize:feature:implement` — implement from spec (context load → Star-Gap → TDD → adversarial review → integration)
- `/constellize:feature:verify` — gate a feature against test integrity, health checks, deployment readiness, maintainability

**Memory workflow** (`.claude/skills/constellize:memory:*`):
- `/constellize:memory:establish` — initialize memory bank for a new project
- `/constellize:memory:update` — sync memory bank with current state (supports `--full`)
- `/constellize:memory:revise` — restructure when files grow unwieldy
- `/constellize:memory:recover` — audit and rewrite a neglected bank

**Personas** (`.claude/agents/`): `construction-lead`, `knowledge-steward`, `feature-architect`.

📁 **Locations:** Feature specs in `llm/features/` (master index: `BACKLOG.md`), memory bank in `llm/memory_bank/`. `construction/sprints/` preserves completed sprint history (still read by the GitHub Pages generator). Legacy `memory-bank/` and `construction/{design,requirements,backlog}` folders were deleted 2026-07-07 — content superseded by the above.

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
