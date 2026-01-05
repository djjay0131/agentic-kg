---
description: Run full code review on recent changes
---

Run a comprehensive code review using the code-review-agent with the `review` command.

1. Get the list of changed files using `git diff --name-only HEAD~1` or staged changes
2. Run pre-check phase for secrets, debug code, and TODOs
3. Dispatch to all 6 specialist reviewers in parallel:
   - security-reviewer
   - quality-reviewer
   - performance-reviewer
   - architecture-reviewer
   - test-reviewer
   - docs-reviewer
4. Aggregate findings by severity (Critical/Major/Minor)
5. Generate the full Code Review Report with scores

Focus on Python files in packages/core/src/ and any configuration files.
