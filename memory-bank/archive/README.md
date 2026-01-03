# Memory Bank Archive

This folder contains archived content from the memory-bank that is no longer immediately relevant but should be preserved for historical reference and future design work.

## Structure

```
archive/
├── progress/      # Archived milestone completions
├── decisions/     # Historical context and decisions
└── sessions/      # Session summaries (optional)
```

## Archive Policy

### progress/
Contains completed milestone documentation archived from `progress.md`.
- Archive when: Phase completes or quarterly
- Naming: `phase-{N}-{name}-{YYYY}-Q{N}.md` or `progress-{YYYY}-Q{N}.md`
- Content: Completed work sections with original dates and verification notes

### decisions/
Contains historical decisions archived from `activeContext.md`.
- Archive when: Decisions are > 30 days old or superseded
- Naming: `decisions-{YYYY}-{MM}.md`
- Content: Recent Decisions sections with rationale preserved

### sessions/
Contains session summaries for long-running work.
- Archive when: End of significant work sessions (optional)
- Naming: `session-{YYYY}-{MM}-{DD}.md`
- Content: What was accomplished, decisions made, next steps

## Retrieval

When resuming work on a feature or investigating historical decisions:
1. Check the relevant archive folder
2. Use dates/phase names to locate relevant content
3. Reference archived content in current docs if needed

## Managed By

This archive is managed by the **memory-agent**. Manual edits should follow the naming conventions above.
