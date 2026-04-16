---
name: constellize:memory:update
description: Update memory bank files to reflect the current state of the project
---

# Update Memory Bank

Synchronize memory bank files with the actual state of the codebase and project.

## Determine Mode

Check if the user specified a mode. Default to **quick** if not specified.

- **Quick mode**: Update only `activeContext.md` and `progress.md`
- **Full mode**: Update all 5 memory bank files

The user can request full mode by saying "full update", "update all", or passing `--full`.

## Step 1: Read Current Memory Bank

Read all 5 files in `llm/memory_bank/`:
- `projectbrief.md`
- `techContext.md`
- `systemPatterns.md`
- `activeContext.md`
- `progress.md`

Note the `Last updated` date on activeContext.md and progress.md to understand how stale they are.

## Step 2: Detect Changes

Run these commands to understand what changed:

1. `git log --oneline -20` to see recent commits.
2. `git diff HEAD~10 --stat` to see which files changed (adjust range based on staleness).
3. `git diff HEAD~10 --name-only` to get the changed file list.
4. Read any new or significantly changed source files identified above.
5. Check for new dependencies (`git diff HEAD~10 -- package.json Cargo.toml pyproject.toml` or equivalent).

If the memory bank was updated today, narrow the git range. If it is weeks stale, widen it.

## Step 3: Identify Drift

Compare what the memory bank says against what the code shows. Look for:

- New files, modules, or features not mentioned in systemPatterns.md
- Changed dependencies or tooling not reflected in techContext.md
- Completed work still listed as "in progress" in progress.md
- Stale "next steps" in activeContext.md that have already been done or abandoned
- Architectural changes that contradict systemPatterns.md

## Step 4: Apply Updates

### Quick Mode
Update these two files:

**activeContext.md**:
- Set current work focus based on recent commits
- Update recent changes section
- Revise next steps
- Update the date stamp

**progress.md**:
- Move completed items from "remaining" to "done"
- Add newly discovered work items
- Update known issues
- Update the date stamp

### Full Mode
Update all files that have drifted. In addition to the quick mode updates:

**projectbrief.md**: Update only if scope or constraints changed.

**techContext.md**: Update dependency versions, new tools, changed setup steps.

**systemPatterns.md**: Update architecture, new patterns, new modules, changed file structure.

## Step 5: Verify

After writing updates, confirm:

- [ ] No placeholder text introduced
- [ ] Date stamps updated on modified files
- [ ] No contradictions between files (e.g., progress.md says done, activeContext.md says in progress)
- [ ] New patterns reference actual file paths
- [ ] Removed or renamed files are no longer referenced

## Step 6: Report

State which files were updated and summarize the key changes. If running in quick mode, flag any signs that a full update is needed (e.g., techContext.md mentions a dependency that no longer exists).
