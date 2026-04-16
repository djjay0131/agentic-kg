---
name: constellize:memory:revise
description: Restructure memory bank content when files have grown unwieldy or information is misplaced
---

# Revise Memory Bank Structure

Reorganize memory bank files so content lives in the right place, files stay focused, and nothing is duplicated.

## Step 1: Audit Current State

Read all files in `llm/memory_bank/`. For each file, note:

- Line count (flag any file over 150 lines as too long)
- Content that belongs in a different file
- Duplicated information across files
- Sections that have grown into walls of text
- Outdated content that should be removed entirely

## Step 2: Classify Issues

Categorize every problem found into one of these types:

1. **Misplaced content** - Information in the wrong file
2. **Overgrown file** - File exceeds 150 lines or has bloated sections
3. **Duplication** - Same information in multiple files
4. **Stale content** - Outdated information that no longer applies
5. **Missing structure** - Content that lacks headings or organization

Present the findings to the user as a numbered list before making changes.

## Step 3: Apply Reorganization

Follow these placement rules:

| Content Type | Belongs In |
|---|---|
| Project goals, scope, constraints | projectbrief.md |
| Languages, frameworks, dependencies, setup | techContext.md |
| Architecture, patterns, directory structure | systemPatterns.md |
| Current focus, recent changes, next steps | activeContext.md |
| Completed work, remaining work, known issues | progress.md |

For each issue:

**Misplaced content**: Move the content to the correct file. Remove it from the source file. Do not leave a "see X file" cross-reference unless truly needed.

**Overgrown files**: Extract detailed subsections into files under `llm/memory_bank/details/`. Keep a one-line summary with a file reference in the parent file. Example:
```
## API Design
See [details/api-design.md](details/api-design.md) for full specification.
```

**Duplication**: Keep the content in the file where it fits best per the table above. Remove it from all other files.

**Stale content**: Delete it. Do not comment it out or move it to an archive.

**Missing structure**: Add clear markdown headings. Break long paragraphs into bullet points.

## Step 4: Verify Consistency

After all moves and edits:

- [ ] Every file is under 150 lines
- [ ] No content appears in more than one file
- [ ] No dangling references to moved or deleted content
- [ ] All files still have their required sections (see establish skill for file templates)
- [ ] Date stamps updated on all modified files
- [ ] Any new `details/` files are referenced from their parent

## Step 5: Report

List every change made: what moved where, what was deleted, what was split out. State the before and after line counts for each file.
