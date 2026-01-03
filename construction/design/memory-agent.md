# Memory Agent Design

**Created:** 2025-01-03
**Status:** Draft
**ADRs:** ADR-007 (Documentation Structure)

---

## Overview

The memory-agent is an administrative agent responsible for maintaining the memory-bank folder. It ensures documentation remains current, organized, and relevant while preserving historical context through archival.

## Responsibilities

### 1. Update Operations
Keep living documents (`activeContext.md`, `progress.md`, `phases.md`) current and accurate.

### 2. Archive Operations
Move stale content to `memory-bank/archive/` with proper timestamps and organization.

### 3. Organization Operations
Ensure cross-references are valid, remove duplicates, maintain consistency.

### 4. Validation Operations
Detect staleness, missing updates, and inconsistencies across files.

---

## Agent Capabilities

### Capability 1: Refresh Active Context

**Trigger:** End of work session or user command "update memory bank"

**Actions:**
1. Read current `activeContext.md`
2. Identify decisions older than 30 days or marked as resolved
3. Move historical decisions to `archive/decisions/decisions-{YYYY}-{MM}.md`
4. Update "Recent Decisions" to reflect only current decisions
5. Ensure "Open Questions" don't contain answered items
6. Update "Last Updated" timestamp

**Validation:**
- No duplicate decisions between active and archived
- All cross-references to other files are valid
- Open questions reflect actual unknowns

### Capability 2: Archive Completed Progress

**Trigger:** Phase completion or quarterly review

**Actions:**
1. Read current `progress.md`
2. Identify fully completed phases/milestones
3. Create archive file: `archive/progress/phase-{N}-{name}-{YYYY}-Q{N}.md`
4. Move completed sections to archive with original dates preserved
5. Update `progress.md` to reference archived content
6. Keep "In Progress" and "Remaining Work" sections current

**Validation:**
- Completed work has verification notes
- Archive preserves all original context
- No orphaned references in progress.md

### Capability 3: Synchronize Phase Status

**Trigger:** Milestone completion or design document changes

**Actions:**
1. Read `phases.md` registry
2. Check construction folder for design doc status
3. Check sprint docs for implementation status
4. Update phase registry table
5. Update phase details section
6. Verify ADR links are correct

**Validation:**
- Phase status matches actual state of design/sprint docs
- All design doc links resolve
- ADR references are valid

### Capability 4: Validate Cross-References

**Trigger:** On demand or as part of update cycle

**Actions:**
1. Extract all markdown links from memory-bank files
2. Verify each link target exists
3. Check for circular or broken references
4. Report invalid links for correction

**Output:**
```
Cross-Reference Report
----------------------
Valid links: 42
Broken links: 2
  - activeContext.md:108 → ../construction/design/missing.md
  - progress.md:45 → #nonexistent-section
```

### Capability 5: Detect Staleness

**Trigger:** Session start or on demand

**Actions:**
1. Check "Last Updated" dates on all files
2. Compare against recent git commits
3. Identify files that may be stale (>7 days without update during active development)
4. Check for inconsistencies:
   - `activeContext.md` references a phase not in `phases.md`
   - `progress.md` shows work complete but `phases.md` not updated
   - ADR referenced but not in `architecturalDecisions.md`

**Output:**
```
Staleness Report
----------------
Potentially stale files:
  - techContext.md (last updated 14 days ago)

Inconsistencies found:
  - activeContext.md references "Phase 1" as "In Progress"
    but phases.md shows "Design Complete"
```

---

## Agent Interface

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `update` | Full update cycle (refresh, validate, report) | `memory-agent update` |
| `archive` | Archive stale content | `memory-agent archive --type decisions` |
| `validate` | Run validation checks only | `memory-agent validate` |
| `status` | Show current memory-bank status | `memory-agent status` |
| `sync-phases` | Synchronize phases.md with construction/ | `memory-agent sync-phases` |

### Input

The agent reads from:
- All files in `memory-bank/`
- Design docs in `construction/design/`
- Sprint docs in `construction/sprints/`
- Git history (for staleness detection)

### Output

The agent produces:
- Updated memory-bank files
- Archive files with preserved history
- Status/validation reports (stdout or markdown)

---

## Implementation Approach

### Option A: Claude Code Slash Command
Implement as a Claude Code slash command that invokes the agent behavior.

```markdown
# .claude/commands/memory-agent.md

Update and maintain the memory-bank folder.

## Actions
1. Read all memory-bank files
2. Check for staleness and inconsistencies
3. Archive content older than 30 days from activeContext.md
4. Sync phases.md with construction folder state
5. Report any issues found
```

### Option B: Python Script
Implement as a Python script that can be run standalone or invoked by other agents.

```python
# packages/agents/memory_agent/main.py
class MemoryAgent:
    def __init__(self, memory_bank_path: str):
        self.memory_bank = Path(memory_bank_path)

    def update(self) -> UpdateReport:
        """Full update cycle"""
        self.refresh_active_context()
        self.archive_completed_progress()
        self.sync_phases()
        return self.validate()
```

### Option C: LangGraph Agent
Implement as a LangGraph agent that can be orchestrated with other agents.

```python
from langgraph.graph import StateGraph

memory_workflow = StateGraph(MemoryState)
memory_workflow.add_node("read", read_memory_bank)
memory_workflow.add_node("validate", validate_consistency)
memory_workflow.add_node("archive", archive_stale_content)
memory_workflow.add_node("update", update_files)
memory_workflow.add_node("report", generate_report)
```

### Recommended: Hybrid Approach

1. **Slash Command** for interactive use during Claude Code sessions
2. **Python module** for programmatic access and testing
3. **LangGraph integration** for future agent orchestration

---

## State Schema

```python
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

class MemoryBankFile(BaseModel):
    path: str
    last_updated: datetime
    content_hash: str

class PhaseStatus(BaseModel):
    phase_number: int
    name: str
    status: str  # not_started, design_in_progress, design_complete, implementing, complete
    design_doc: Optional[str]
    sprint_doc: Optional[str]
    adrs: List[str]

class ValidationIssue(BaseModel):
    severity: str  # warning, error
    file: str
    line: Optional[int]
    message: str

class MemoryAgentState(BaseModel):
    files: List[MemoryBankFile]
    phases: List[PhaseStatus]
    issues: List[ValidationIssue]
    last_run: datetime
```

---

## Archive File Format

### decisions archive
```markdown
# Archived Decisions - December 2024

Archived from activeContext.md on 2025-01-03

---

## Decision 1: Project Scope Definition
- **Date:** 2025-12-18
- **Decision:** Focus on enhancing Denario for Agentic Knowledge Graphs
- **Status:** Implemented
- **Archived Because:** Decision is now established project direction

## Decision 2: GCP Deployment First
- **Date:** 2025-12-18
- **Decision:** Deploy base Denario to GCP before building extensions
- **Status:** Complete (Phase 0 done)
- **Archived Because:** Phase 0 infrastructure complete
```

### progress archive
```markdown
# Phase 0: Infrastructure - Completed Work

Archived from progress.md on 2025-01-03

---

## Memory Bank Initialization (2025-12-18)

**What:**
- Updated all memory-bank files for new Agentic KG project

**Impact:**
Foundation for systematic development with full context retention.

**Verification:**
All files updated with project-specific content aligned with reference paper.

## GCP Infrastructure Setup (2025-12-18)
...
```

---

## Integration with Construction-Agent

### Handoff Triggers

**Construction-Agent → Memory-Agent:**
- Design doc marked complete → memory-agent updates phases.md
- Sprint completed → memory-agent archives progress, updates phases.md

**Memory-Agent → Construction-Agent:**
- Phase ready for implementation → signal in phases.md
- Inconsistency detected → flag for construction-agent review

### Coordination Protocol

```
1. Construction-agent completes design doc
2. Construction-agent updates phases.md status to "Design Complete"
3. Memory-agent detects change on next run
4. Memory-agent validates cross-references
5. Memory-agent updates activeContext.md with new phase status
6. Construction-agent can begin implementation
```

---

## Success Criteria

1. [ ] Memory-bank files remain current (<7 days stale during active work)
2. [ ] Archived content preserves full context and dates
3. [ ] No broken cross-references between files
4. [ ] phases.md accurately reflects construction/ state
5. [ ] Agent can run without human intervention for routine updates
6. [ ] Human notified of issues requiring attention

---

## Open Questions

1. **Automation level:** Should agent auto-commit changes or require human review?
2. **Trigger mechanism:** Cron-like schedule, git hooks, or manual invocation?
3. **LLM usage:** Should validation use LLM for semantic checks or stay rule-based?

---

## Next Steps

1. Decide on implementation approach (slash command vs Python vs both)
2. Define construction-agent interface for coordination
3. Implement core capabilities
4. Test with current memory-bank state
5. Document usage in project README
