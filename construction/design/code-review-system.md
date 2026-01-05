# Code Review System Design

**Created:** 2025-01-04
**Status:** Draft
**ADRs:** ADR-007 (Documentation Structure)
**Implementation:** `.claude/agents/code-review/*.md` (Claude Code Sub-Agents)

---

## Overview

A comprehensive multi-agent code review system that provides thorough analysis through specialized reviewer agents, with a multi-pass fix workflow for optimal solutions. The system integrates into both development (testing phase) and PR workflows.

## Architecture

```
                           ┌─────────────────────────┐
                           │   code-review-agent     │
                           │     (Orchestrator)      │
                           └───────────┬─────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
┌───────────────┐            ┌─────────────────┐            ┌─────────────────┐
│  Pre-Check    │            │  Specialist     │            │  Action         │
│  Phase        │            │  Review Phase   │            │  Phase          │
└───────────────┘            └─────────────────┘            └─────────────────┘
        │                              │                              │
        ▼                              ▼                              ▼
┌───────────────┐     ┌────────────────────────────────┐   ┌─────────────────┐
│ Automated     │     │ 6 Specialist Agents (parallel) │   │ 3 Action Agents │
│ Checks        │     ├────────────────────────────────┤   │ (sequential)    │
└───────────────┘     │ • security-reviewer            │   ├─────────────────┤
                      │ • quality-reviewer             │   │ • review-reader │
                      │ • performance-reviewer         │   │ • review-suggester│
                      │ • architecture-reviewer        │   │ • review-applier │
                      │ • test-reviewer                │   └─────────────────┘
                      │ • docs-reviewer                │
                      └────────────────────────────────┘
```

---

## Agent Inventory

### Tier 1: Orchestrator

| Agent | File | Purpose | Tools |
|-------|------|---------|-------|
| `code-review-agent` | `code-review-agent.md` | Orchestrates review, dispatches to specialists, aggregates results | Read, Glob, Grep, Bash |

### Tier 2: Specialist Reviewers (Read-Only)

| Agent | File | Focus | Tools |
|-------|------|-------|-------|
| `security-reviewer` | `security-reviewer.md` | Security vulnerabilities | Read, Grep, Glob |
| `quality-reviewer` | `quality-reviewer.md` | Code quality and style | Read, Grep, Glob |
| `performance-reviewer` | `performance-reviewer.md` | Performance issues | Read, Grep, Glob |
| `architecture-reviewer` | `architecture-reviewer.md` | Design and structure | Read, Grep, Glob |
| `test-reviewer` | `test-reviewer.md` | Test coverage and quality | Read, Grep, Glob, Bash |
| `docs-reviewer` | `docs-reviewer.md` | Documentation completeness | Read, Grep, Glob |

### Tier 3: Action Agents

| Agent | File | Purpose | Tools |
|-------|------|---------|-------|
| `review-reader` | `review-reader.md` | Deep analysis of specific issues | Read, Grep, Glob |
| `review-suggester` | `review-suggester.md` | Multi-pass fix generation | Read, Grep, Glob |
| `review-applier` | `review-applier.md` | Apply approved fixes | Read, Write, Edit, Bash |
| `test-generator` | `test-generator.md` | Generate pytest test suites | Read, Write, Glob, Grep |

---

## Workflow Stages

### Stage 1: Pre-Check (Automated)

**Purpose:** Fast automated checks to catch obvious issues before deep review.

**Checks:**
1. **Secrets Detection**
   - API keys (patterns: `sk-`, `api_key=`, `token=`)
   - Passwords in code
   - Private keys

2. **Debug Code**
   - `console.log`, `print()`, `debugger`
   - `TODO`, `FIXME`, `HACK`, `XXX` comments

3. **File Hygiene**
   - Large files (>1MB)
   - Binary files in source
   - Merge conflict markers

**Output:** Pre-check report with blockers and warnings

### Stage 2: Specialist Review (Parallel)

**Purpose:** Deep domain-specific analysis by 6 specialist agents running in parallel.

#### security-reviewer
- Input validation and sanitization
- Authentication/authorization checks
- SQL/NoSQL injection vectors
- XSS, CSRF vulnerabilities
- Cryptographic practices
- Sensitive data exposure
- Dependency vulnerabilities

#### quality-reviewer
- Naming conventions
- Function/method complexity (cyclomatic < 10)
- Code duplication (DRY)
- Error handling completeness
- Resource management (open/close)
- Magic numbers/strings
- Dead code detection

#### performance-reviewer
- Algorithm complexity (Big-O)
- N+1 query patterns
- Memory leaks
- Inefficient loops
- Missing caching opportunities
- Resource-intensive operations
- Async/await misuse

#### architecture-reviewer
- SOLID principles compliance
- Layer separation (MVC, etc.)
- Coupling and cohesion
- Design pattern appropriateness
- API design consistency
- Dependency direction
- Module boundaries

#### test-reviewer
- Test coverage gaps
- Missing edge cases
- Test quality (assertions, mocking)
- Integration vs unit balance
- Test naming and organization
- Flaky test indicators
- Missing negative tests

#### docs-reviewer
- Missing docstrings
- Outdated comments
- README completeness
- API documentation
- Type hints/annotations
- Example code
- Changelog updates

### Stage 3: Aggregation & Report

**Purpose:** Combine specialist findings into unified report.

**Actions:**
1. Collect all specialist reports
2. Deduplicate overlapping findings
3. Assign severity (Critical/Major/Minor)
4. Generate action checklist
5. Calculate scores per category
6. Produce structured markdown report

### Stage 4: Fix Workflow

**Purpose:** Multi-pass fix generation for optimal solutions.

**Workflow per issue:**
```
Issue Selected
     │
     ▼
┌─────────────────┐
│ review-reader   │ ──► Deep analysis of issue context
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ review-suggester│
│   Pass 1        │ ──► Generate initial fix proposal
│   Pass 2        │ ──► Review for side effects, edge cases
│   Pass 3        │ ──► Optimize and simplify
└────────┬────────┘
         │
         ▼
   Human Approval
         │
         ▼
┌─────────────────┐
│ review-applier  │ ──► Apply approved fix
└────────┬────────┘
         │
         ▼
   Re-run Review (verify fix)
```

---

## Output Format

### Structured Report Template

```markdown
# Code Review Report

**Generated:** {timestamp}
**Branch:** {branch}
**Files Changed:** {count}
**Review Status:** PASS | NEEDS_WORK | BLOCKED

---

## Executive Summary

| Category | Critical | Major | Minor | Score |
|----------|----------|-------|-------|-------|
| Security | 0 | 0 | 0 | 10/10 |
| Quality | 0 | 0 | 0 | 10/10 |
| Performance | 0 | 0 | 0 | 10/10 |
| Architecture | 0 | 0 | 0 | 10/10 |
| Testing | 0 | 0 | 0 | 10/10 |
| Documentation | 0 | 0 | 0 | 10/10 |
| **Overall** | **0** | **0** | **0** | **10/10** |

---

## Pre-Check Results

### Blockers
- [ ] {file}:{line} - {description}

### Warnings
- [ ] {file}:{line} - {description}

---

## Critical Issues

### [CRIT-001] {Title}
**File:** {path}:{line}
**Reviewer:** {agent}
**Description:** {description}
**Impact:** {impact}
**Suggested Fix:** {fix}
**Status:** [ ] Pending | [ ] In Progress | [ ] Fixed | [ ] Won't Fix

---

## Major Issues
{same format}

---

## Minor Issues
- [ ] {file}:{line} - {description} ({reviewer})

---

## Positive Highlights
- {positive observation}

---

## Action Checklist

### Must Fix Before Merge
- [ ] {issue-id}: {description}

### Should Fix
- [ ] {issue-id}: {description}

### Nice to Have
- [ ] {issue-id}: {description}

---

## Fix Tracking

| Issue | Assignee | Status | Fix Applied |
|-------|----------|--------|-------------|
| {id} | - | Pending | No |
```

---

## Integration Points

### Development Phase (Testing)

```bash
# Full review
@code-review-agent review

# Review specific files
@code-review-agent review src/models.py src/service.py

# Quick pre-check only
@code-review-agent precheck

# Get fix suggestion for specific issue
@review-suggester suggest CRIT-001

# Apply approved fix
@review-applier apply CRIT-001
```

### PR Workflow

```bash
# Triggered by CI on PR creation
@code-review-agent pr-review --post-comment

# Block merge if critical issues
# Exit code 1 if Critical issues exist
```

---

## Commands Reference

### code-review-agent (Orchestrator)

| Command | Description |
|---------|-------------|
| `review` | Full review cycle |
| `review <files>` | Review specific files |
| `precheck` | Pre-check phase only |
| `pr-review` | PR-focused review |
| `status` | Show last review status |

### review-suggester

| Command | Description |
|---------|-------------|
| `suggest <issue-id>` | Generate fix for specific issue |
| `suggest-all` | Generate fixes for all issues |

### review-applier

| Command | Description |
|---------|-------------|
| `apply <issue-id>` | Apply specific fix |
| `apply-all` | Apply all approved fixes |

### test-generator

| Command | Description |
|---------|-------------|
| `generate <file>` | Generate tests for a specific file |
| `generate-all <dir>` | Generate tests for all files in directory |
| `generate-from-review` | Generate tests for files flagged in review |
| `coverage-gaps <file>` | Analyze existing tests and identify gaps |

---

## File Structure

```
.claude/agents/
├── code-review/
│   ├── code-review-agent.md      # Orchestrator
│   ├── security-reviewer.md      # Specialist
│   ├── quality-reviewer.md       # Specialist
│   ├── performance-reviewer.md   # Specialist
│   ├── architecture-reviewer.md  # Specialist
│   ├── test-reviewer.md          # Specialist
│   ├── docs-reviewer.md          # Specialist
│   ├── review-reader.md          # Action
│   ├── review-suggester.md       # Action
│   ├── review-applier.md         # Action
│   └── test-generator.md         # Action (generates tests)
```

---

## Severity Classification

### Critical (Must Fix)
- Security vulnerabilities (injection, auth bypass)
- Data exposure risks
- Breaking changes without migration
- Crashes or data loss

### Major (Should Fix)
- Performance degradation
- Missing error handling
- Test coverage gaps for critical paths
- API contract violations

### Minor (Nice to Have)
- Style inconsistencies
- Documentation gaps
- Minor optimization opportunities
- Naming suggestions

---

## Success Criteria

1. [ ] All 10 agents implemented and functional
2. [ ] Pre-check catches secrets and debug code
3. [ ] Specialist agents run in parallel
4. [ ] Multi-pass suggester improves fix quality
5. [ ] Structured report generated consistently
6. [ ] Integration with development workflow
7. [ ] PR workflow blocks on critical issues

---

## Implementation Status

- [x] Design document created
- [x] code-review-agent (orchestrator)
- [x] security-reviewer
- [x] quality-reviewer
- [x] performance-reviewer
- [x] architecture-reviewer
- [x] test-reviewer
- [x] docs-reviewer
- [x] review-reader
- [x] review-suggester
- [x] review-applier
- [x] test-generator (new - generates pytest suites)
- [x] Test with sample code (Sprint 01 review)
- [ ] Document usage patterns
- [ ] Add automation hooks (Claude Code hooks, GitHub Actions)

---

## Next Steps

1. Create orchestrator agent (code-review-agent)
2. Create 6 specialist reviewer agents
3. Create 3 action agents
4. Test complete workflow
5. Integrate with PR workflow
