# CLAUDE.md - Project Context for Claude Code

## IMPORTANT: Design-First Workflow (2026-02-05)

**⚠️ CRITICAL: No implementation without design!**

For **ANY new feature**, use the construction-agent BEFORE writing code:

```
@construction-agent design <feature-name>
```

The agent will guide you through a 9-phase specification workflow:
1. Analyze repository context
2. Research best practices
3. Define problem and verification
4. Ask clarifying questions (including "definition of done")
5. Create sample implementation
6. Draft specification
7. Generate critical questions
8. Present final spec

**After design approval:**
```
@construction-agent signal-complete <feature-name>
@construction-agent create-sprint <number> "<goal>"
```

**During implementation:**
```
@construction-agent update-sprint
```

**Available Commands:**
- `design <feature>` - Create design document
- `create-sprint <num> <goal>` - Create sprint from designs
- `update-sprint` - Update current sprint progress
- `validate` - Check construction folder consistency
- `signal-complete <phase>` - Mark design ready

📁 **Location:** Design docs in `construction/design/`, sprints in `construction/sprints/`

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
- memory-bank/activeContext.md - Current work phase
- memory-bank/techContext.md - Technical details
- memory-bank/progress.md - Task tracking

## Notes for Future Sessions
- Always read this file AND memory-bank/*.md on context reset
- Need to test DenarioApp fix before reopening PR
- Denario core still has arXiv_pdf scope bug to fix
