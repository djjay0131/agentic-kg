---
name: constellize:memory:recover
description: Recover a neglected memory bank by auditing staleness and rewriting files to match actual project state
---

# Recover Memory Bank

Rebuild a memory bank that has fallen out of sync with the codebase. This is for projects where the memory bank exists but is significantly stale or inaccurate.

## Step 1: Assess Staleness

Read all files in `llm/memory_bank/`. For each file, record:

- The `Last updated` date (if present)
- A staleness rating: **current**, **stale** (weeks old), or **obsolete** (describes a different state of the project)

Run `git log --oneline -30` and `git log --since="$(cat llm/memory_bank/activeContext.md | grep -i 'last updated' | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')" --oneline` to see how many commits happened since the last update.

Report the staleness assessment before proceeding.

## Step 2: Read the Codebase

Build a fresh understanding of the project independent of the memory bank:

1. Read project manifests (`package.json`, `Cargo.toml`, `pyproject.toml`, etc.).
2. Scan the directory tree: `ls` at top level and key subdirectories.
3. Read configuration files and CI/CD configs.
4. Read 5-8 key source files, prioritizing:
   - Entry points (main, index, app)
   - Recently changed files (`git log --oneline -10 --name-only`)
   - Files in directories not mentioned in the memory bank
5. Run `git log --oneline -30` to understand recent trajectory.
6. Check for new dependencies or removed dependencies.

## Step 3: Diff Reality vs. Memory Bank

For each of the 5 files, list specific discrepancies:

- **projectbrief.md**: Has scope changed? Are constraints still accurate?
- **techContext.md**: Are dependencies, versions, and setup steps correct?
- **systemPatterns.md**: Does the architecture description match the actual code structure?
- **activeContext.md**: Does "current focus" reflect what is actually being worked on?
- **progress.md**: Are completed items marked done? Are "remaining" items still relevant?

## Step 4: Flag Unknowns

Identify anything that cannot be determined from code alone. Common examples:

- Business decisions or priorities
- Planned features not yet started
- Reasons behind non-obvious architectural choices
- External integration details (API keys, third-party contracts)
- Team conventions not enforced by tooling

Collect these into a list of questions for the user. Ask them all at once.

## Step 5: Rewrite Files

Rewrite each file that is **stale** or **obsolete** from scratch. Do not try to patch old content -- start fresh using the same structure defined in the establish skill:

- **projectbrief.md**: Project identity, scope, constraints
- **techContext.md**: Stack, setup, dependencies, infrastructure
- **systemPatterns.md**: Architecture, patterns, directory layout with real file paths
- **activeContext.md**: Current focus, recent changes, next steps, date stamp
- **progress.md**: Done, remaining, known issues, date stamp

For files rated **current**, make only targeted corrections.

## Step 6: Verify

- [ ] No placeholder text in any file
- [ ] All file paths referenced in systemPatterns.md actually exist
- [ ] techContext.md setup commands are runnable
- [ ] activeContext.md and progress.md have today's date
- [ ] No contradictions between files
- [ ] Unknowns are documented as questions, not guesses

## Step 7: Report

Summarize recovery results:
- Which files were rewritten vs. patched vs. left unchanged
- Key discrepancies found between old memory bank and actual codebase
- Outstanding questions the user needs to answer
- Recommend running `/update` regularly to prevent future drift
