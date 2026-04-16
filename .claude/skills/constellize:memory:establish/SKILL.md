---
name: constellize:memory:establish
description: Initialize memory bank for a new project by reading the codebase and creating the 5 core knowledge files
---

# Establish Memory Bank

Initialize the `llm/memory_bank/` directory with 5 core knowledge files and the `llm/features/` directory.

## Step 1: Gather Context

Read the codebase before asking any questions:

1. Read `README.md`, `package.json`, `Cargo.toml`, `pyproject.toml`, or equivalent project manifest.
2. Scan the top-level directory structure with `ls` and key subdirectories.
3. Read configuration files (`.env.example`, `docker-compose.yml`, CI configs, etc.).
4. Read 3-5 representative source files to understand language, patterns, and style.
5. Check `git log --oneline -20` for recent activity and project trajectory.

## Step 2: Ask Targeted Questions

After reading the codebase, ask the user **only** what cannot be inferred from code:

- What problem does this project solve and for whom?
- Are there architectural decisions not visible in code (e.g., why this DB, why this framework)?
- What is currently being worked on or planned next?

Limit to 3-5 questions. Skip any question the code already answers.

## Step 3: Create Directory Structure

```
llm/
├── memory_bank/
│   ├── projectbrief.md
│   ├── techContext.md
│   ├── systemPatterns.md
│   ├── activeContext.md
│   └── progress.md
└── features/
```

Create both `llm/memory_bank/` and `llm/features/` directories.

## Step 4: Write the 5 Core Files

### projectbrief.md
- Project name, one-line description, and purpose
- Target users and the problem being solved
- Scope boundaries: what is in scope and explicitly out of scope
- Key constraints (performance, compliance, platform, etc.)

### techContext.md
- Languages, frameworks, and their versions
- Development setup steps (install, build, run, test)
- Key dependencies and why they were chosen
- Infrastructure and deployment (hosting, CI/CD, databases)
- Technical constraints and limitations

### systemPatterns.md
- High-level architecture (monolith, microservices, serverless, etc.)
- Directory/module structure and what lives where
- Key design patterns in use (with file path examples)
- Data flow for the primary use case
- Naming conventions and code style rules observed in the codebase

### activeContext.md
- Current work focus (from git log and user input)
- Recent significant changes
- Open decisions or unresolved questions
- Immediate next steps
- Date stamp: `Last updated: YYYY-MM-DD`

### progress.md
- What is built and working today
- What remains to be built
- Known bugs or tech debt
- Key milestones reached (with dates if available)
- Date stamp: `Last updated: YYYY-MM-DD`

## Step 5: Quality Check

Before finishing, verify every file against this checklist:

- [ ] No placeholder text ("TBD", "TODO", "fill in later")
- [ ] projectbrief.md states scope boundaries and constraints
- [ ] techContext.md includes concrete versions and setup commands
- [ ] systemPatterns.md references actual file paths from the codebase
- [ ] activeContext.md and progress.md have date stamps
- [ ] All architectural decisions include brief rationale
- [ ] Content is specific to this project, not generic boilerplate

If any check fails, fix it before reporting completion.

## Step 6: Report

Summarize what was created and flag any gaps where information was unavailable or uncertain. List the files written with their line counts.
