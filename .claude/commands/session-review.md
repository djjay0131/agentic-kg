---
description: Run end-of-session review with memory and code review agents
---

Please perform an end-of-session review:

1. **Memory Agent Update**: Run the memory-agent with the `update` command to:
   - Refresh activeContext.md with current work
   - Sync phases.md with construction/ folder
   - Archive any stale decisions
   - Validate cross-references

2. **Code Review Precheck**: Run the code-review-agent with the `precheck` command to:
   - Check for secrets or API keys
   - Find debug code (console.log, print, etc.)
   - Identify TODOs and FIXMEs
   - Check for large files

3. **Construction Update**: Run the construction-agent with the `update-sprint` command to:
   - Update sprint task completion status
   - Calculate progress percentage

Report the combined findings from all three agents.
