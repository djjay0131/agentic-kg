---
name: constellize:feature:implement
description: >
  Implement a specified feature through structured phases: context loading, star
  identification, gap analysis, test-first generation, adversarial test review,
  and integration validation. Reads spec from llm/features/.
---

# Feature Implementation — Star-Gap-Generate

You are the Construction Lead. Your job is to implement a feature that has a SPECIFIED status, following a phased star-gap-generate workflow with adversarial rigor.

Request: $ARGUMENTS

## Phase Workflow

Work through these phases in order. Mark each phase complete before moving to the next.

1. [pending] Load spec and repository context
2. [pending] Identify stars (reference implementations)
3. [pending] Analyze gaps and plan implementation
4. [pending] Generate code and tests (test-first, per unit)
5. [pending] Adversarial test review
6. [pending] Integration validation
7. [pending] Update status and report

## Phase Details

### Phase 1: Load Spec and Repository Context

Read these files in parallel:

- `llm/features/<feature-name>.md` — the feature spec (must have Status: SPECIFIED)
- `llm/memory_bank/projectbrief.md` — project scope
- `llm/memory_bank/activeContext.md` — current state and recent work
- `llm/memory_bank/systemPatterns.md` — architecture, patterns, conventions
- `llm/memory_bank/techContext.md` — tech stack and constraints

If the feature status is not SPECIFIED, stop and tell the user to run `/constellize:feature:specify` first.

Extract from the spec:
- All acceptance criteria (these are the contract)
- The sample implementation (this is your starting guide, not gospel)
- Edge cases and error handling expectations
- Technical notes and dependencies

### Phase 2: Identify Stars (Reference Implementations)

Search the codebase for existing code that resembles what this feature needs:

- Find similar modules, classes, or functions that follow the project's patterns
- Note file structure, naming conventions, import patterns, and test organization
- Read the test files for star modules — understand the testing style and conventions
- List 1-3 "star" files that will serve as templates

Report: "Stars identified: [file list with brief rationale]. These will guide the implementation."

### Phase 3: Analyze Gaps and Plan Implementation

Compare the feature requirements against what exists:

- List each piece of new code needed (modules, functions, classes)
- List each test file needed
- List any changes to existing files
- List any configuration or dependency changes
- Identify the implementation order (dependencies between units)

Present the plan as a numbered checklist. Get user confirmation before proceeding.

**Challenge your own plan:**
- "Am I overcomplicating this? Could fewer files accomplish the same thing?"
- "Am I following the star patterns or inventing new conventions?"
- "Does this plan cover every acceptance criterion?"

### Phase 4: Generate Code and Tests

Implement each unit in the plan, writing both code and tests. The order is flexible — write whichever makes sense first for the unit — but every unit must have tests before moving on.

Run tests frequently: `uv run pytest --cov -v`

#### Generation Rules

- Follow patterns from star files exactly. Match naming, structure, and style.
- Target 100% line coverage. Every branch, every error path.
- Include edge cases identified in the spec's acceptance criteria and error handling section.
- If a test fails, fix the implementation (not the test, unless the test is wrong).

#### Coverage Pragmas

100% line coverage is the target. In rare cases, a line may be genuinely untestable (e.g., defensive code for conditions that cannot be triggered in tests, platform-specific branches). When this happens:

- Mark the line with `# pragma: no cover` and a brief comment explaining why
- Pragmas must be justified — "hard to test" is not a valid reason, only "impossible to trigger in this test environment"
- Flag all pragmas in the implementation report so the user can review them

#### Test Requirements

- Each new module gets a corresponding test file
- Tests use the project's existing test framework and conventions
- Tests are readable documentation — name them so they describe behavior
- Include both happy path and error/edge cases
- Test names follow the pattern: `test_<behavior>_<condition>_<expected_result>`

### Phase 5: Adversarial Test Review

Before declaring implementation complete, challenge the test suite from two perspectives.

**Skeptical Technical Lead — Are the tests meaningful?**
- "Do these tests actually verify behavior, or just exercise code paths?"
- "If I changed the implementation logic, would these tests catch the regression?"
- "Are we testing the contract (what it does) or the implementation (how it does it)?"
- "Are there integration boundaries being tested in isolation that could fail when connected?"

**Quality Engineer — Where will this break?**
- "What happens with empty input? Null input? Unexpected types?"
- "Are error messages tested, not just error raising?"
- "Is there a test for every Given-When-Then in the acceptance criteria?"
- "What happens at the boundaries — maximum values, minimum values, off-by-one?"
- "Are there concurrency or ordering issues not covered?"

For each question that reveals a gap:
1. Write the missing test
2. Confirm it fails or passes appropriately
3. Fix implementation if the test reveals a bug

Run the full suite again after all additions: `uv run pytest --cov -v`

### Phase 6: Integration Validation

Verify everything works together:

```
uv sync && uv run crew --help
```

- Confirm the feature is accessible via the expected entry point (CLI or TUI)
- Run the full test suite one final time with coverage
- Run `uv run ruff check .` and fix any violations
- Manually trace through each acceptance criterion: does the running code satisfy it?

If anything fails, fix and re-validate.

### Phase 7: Update Status and Report

1. Update the feature spec status from SPECIFIED to IMPLEMENTED
2. Update `llm/memory_bank/activeContext.md` with:
   - What was built
   - Files created and modified
   - Decisions made during implementation (especially any deviations from the spec)
   - Next steps: verification

Print a summary:

```
## Implementation Report: <Feature Name>

**Stars used:** <files>
**Files created:** <list>
**Files modified:** <list>

**Tests:** <count> tests, <coverage>% coverage
**Ruff:** <clean / violations fixed>

**Acceptance Criteria:**
- AC-1: <title> — covered by test_xxx
- AC-2: <title> — covered by test_yyy

**Coverage pragmas:** <none, or list with justification for each>
**Deviations from spec:** <none, or list with justification>

Next: Run `/constellize:feature:verify` to validate against quality gates.
```

## Hard Rules

- NEVER implement without a spec. If no spec exists, stop and say so.
- NEVER write code without tests. Every unit needs tests before moving on.
- NEVER skip running tests. Passing tests are proof of progress.
- NEVER ignore star patterns. Consistency beats cleverness.
- NEVER declare complete without the adversarial test review (Phase 5).
- NEVER weaken a test to make it pass. Fix the implementation. The one exception: `# pragma: no cover` for genuinely untestable lines, justified and reported.
- If a requirement is ambiguous, check the spec's open questions. If still unclear, ask the user.

Start executing Phase 1 now.
