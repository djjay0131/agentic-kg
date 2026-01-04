# Construction Agent Design

**Created:** 2025-01-04
**Status:** Implementation Started
**ADRs:** ADR-007 (Documentation Structure)
**Implementation:** `.claude/agents/construction-agent.md` (Claude Code Sub-Agent)

---

## Overview

The construction-agent is an administrative agent responsible for managing the `construction/` folder. It ensures design documents are complete before implementation, tracks sprint progress, and coordinates with the memory-agent for phase transitions.

## Core Principle

**No implementation without design.** The construction-agent enforces a design-first workflow where every feature must have a complete specification before code is written.

## Responsibilities

### 1. Design Document Management
Create and maintain design documents in `construction/design/` using the spec_builder workflow.

### 2. Sprint Management
Track tasks and progress in `construction/sprints/`, create new sprints, archive completed ones.

### 3. Requirements Tracking
Manage requirements and user stories in `construction/requirements/`.

### 4. Phase Coordination
Signal phase transitions to memory-agent by updating `memory-bank/phases.md`.

---

## Agent Capabilities

### Capability 1: Create Design Document

**Trigger:** `design <feature-name>` command

**Workflow:** Follow `spec_builder.md` phases:

1. **Analyze Request** - Gather repository context, search for similar patterns
2. **Research** - Look for existing solutions and best practices
3. **Define Problem** - State problem, verification criteria, risks
4. **Clarify** - Ask 2-5 clarifying questions (required: definition of done, verification)
5. **Sample Implementation** - Show core approach in 50-100 lines
6. **Draft Specification** - Problem statement, approach, verification, phases
7. **Generate Questions** - Skeptical Tech Lead + Quality Engineer perspectives
8. **Finalize** - Present spec with questions for user review

**Output:** `construction/design/<feature-name>.md`

### Capability 2: Update Design Document

**Trigger:** `update-design <feature-name>` command

**Actions:**
1. Read existing design document
2. Identify sections needing updates
3. Apply changes while preserving structure
4. Update status field
5. Add revision note with date

### Capability 3: Create Sprint

**Trigger:** `create-sprint <sprint-number> <goal>` command

**Actions:**
1. Read design documents for upcoming work
2. Break down into discrete tasks with acceptance criteria
3. Create `construction/sprints/sprint-{NN}-{name}.md`
4. Link to relevant design docs and ADRs
5. Add to phases.md sprint reference

**Sprint Structure:**
```markdown
# Sprint {NN}: {Name}

**Sprint Goal:** {goal}
**Start Date:** {date}
**Status:** Not Started | In Progress | Complete

**Prerequisites:** {prior sprints}

---

## Tasks

### Task 1: {name}
- [ ] Subtask a
- [ ] Subtask b

**Acceptance Criteria:**
- Criterion 1
- Criterion 2
```

### Capability 4: Update Sprint Status

**Trigger:** `update-sprint` or `sprint-status` command

**Actions:**
1. Read current sprint document
2. Update task checkboxes based on completed work
3. Calculate progress percentage
4. Update sprint status if all tasks complete
5. If complete, signal to memory-agent for archival

### Capability 5: Signal Design Complete

**Trigger:** When design document is finalized

**Actions:**
1. Verify design document has all required sections:
   - Problem statement
   - Solution approach
   - Verification criteria
   - Implementation phases
   - ADR references (if architectural decisions made)
2. Update design document status to "Complete"
3. Update `memory-bank/phases.md`:
   - Set phase status to "Design Complete"
   - Set "Implementation Ready" to "Yes"
4. Notify user that implementation can begin

### Capability 6: Validate Construction State

**Trigger:** `validate` command

**Actions:**
1. Check all design docs have required sections
2. Verify sprint tasks link to design docs
3. Ensure phases.md is in sync with actual documents
4. Report any orphaned or incomplete documents

**Output:**
```
Construction Validation Report
==============================
Design Documents: 3
  - memory-agent.md: Complete ✓
  - construction-agent.md: Draft (missing: sample implementation)
  - phase-1-knowledge-graph.md: Complete ✓

Sprints: 2
  - sprint-00: Complete ✓
  - sprint-01: Not Started, linked to phase-1 design ✓

Issues: 1
  - construction-agent.md missing sample implementation section
```

---

## Agent Interface

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `design` | Create new design using spec_builder | `@construction-agent design user-auth` |
| `update-design` | Update existing design doc | `@construction-agent update-design memory-agent` |
| `create-sprint` | Create new sprint from designs | `@construction-agent create-sprint 02 "Extraction Pipeline"` |
| `update-sprint` | Update current sprint progress | `@construction-agent update-sprint` |
| `sprint-status` | Show sprint progress summary | `@construction-agent sprint-status` |
| `validate` | Validate construction folder state | `@construction-agent validate` |
| `signal-complete` | Mark design complete, update phases.md | `@construction-agent signal-complete phase-1` |

### Input Sources

- Design documents in `construction/design/`
- Sprint documents in `construction/sprints/`
- Requirements in `construction/requirements/`
- Phase registry in `memory-bank/phases.md`
- ADRs in `memory-bank/architecturalDecisions.md`

### Output

- Design documents following spec_builder template
- Sprint documents with task breakdowns
- Updated phases.md for phase transitions
- Validation reports

---

## Integration with Memory-Agent

### Coordination Protocol

```
Construction-Agent                         Memory-Agent
       │                                        │
       │ 1. Creates/updates design doc          │
       │                                        │
       │ 2. Marks design "Complete"             │
       │                                        │
       │ 3. Updates phases.md                   │
       │    (Status → "Design Complete")        │
       │                                        │
       │ ─────── HANDOFF SIGNAL ──────────────► │
       │                                        │
       │                           4. Validates cross-refs
       │                           5. Updates activeContext.md
       │                           6. Archives if needed
       │                                        │
       │ ◄─────── READY SIGNAL ──────────────── │
       │                                        │
       │ 7. Creates sprint if not exists        │
       │ 8. Implementation begins               │
       │                                        │
       │ [During implementation]                │
       │ 9. Updates sprint progress             │
       │                                        │
       │ 10. Sprint complete                    │
       │     Updates phases.md                  │
       │     (Status → "Complete")              │
       │                                        │
       │ ─────── COMPLETION SIGNAL ───────────► │
       │                                        │
       │                          11. Archives progress
       │                          12. Updates phases.md
```

### Handoff Triggers

**Construction-Agent → Memory-Agent:**
- Design marked complete → memory-agent validates and updates context
- Sprint completed → memory-agent archives progress

**Memory-Agent → Construction-Agent:**
- Phase ready for implementation → construction-agent can create sprint
- Inconsistency detected → construction-agent reviews

---

## Design Document Template

Based on existing patterns, design documents should include:

```markdown
# {Feature} Design

**Created:** {date}
**Status:** Draft | In Review | Complete
**Related ADRs:** {list}

---

## Overview
{1-2 paragraph summary}

---

## Problem Statement
{What problem does this solve?}

---

## Solution Approach
{How will we solve it?}

---

## Verification Criteria
{How do we know it works?}

---

## Implementation Phases
{Breakdown of work}

---

## Technical Details
{Schemas, APIs, architecture}

---

## Risks and Mitigations
{What could go wrong?}

---

## Open Questions
{Unresolved items}
```

---

## Success Criteria

1. [ ] Every implementation has a design document
2. [ ] Design documents follow spec_builder workflow
3. [ ] Sprints link to design docs with clear acceptance criteria
4. [ ] Phase transitions are signaled to memory-agent
5. [ ] Construction folder stays organized and current

---

## Implementation Status

- [x] Design document created
- [ ] Sub-agent definition: `.claude/agents/construction-agent.md`
- [ ] Test with current construction state
- [ ] Document usage patterns

---

## Next Steps

1. Create sub-agent definition
2. Test design workflow with a sample feature
3. Test sprint creation workflow
4. Verify memory-agent coordination
