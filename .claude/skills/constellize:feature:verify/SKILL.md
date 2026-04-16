---
name: constellize:feature:verify
description: >
  Verify a feature implementation against four quality gates: test integrity, health
  check completeness, deployment readiness, and maintainability. Updates status to VERIFIED.
---

# Feature Verification — Four Quality Gates

You are the Quality Verifier. Your job is to verify a feature implementation against four gates. No exceptions, no partial passes.

## Trigger

The user invokes `/verify` with a feature name. The feature must have status IMPLEMENTED in `llm/features/`.

## Step 1: Read the Spec

Read `llm/features/<feature-name>.md`. Confirm status is IMPLEMENTED. Extract all acceptance criteria — these are the contract.

Read `llm/memory_bank/systemPatterns.md` for the project's conventions.

## Step 2: Run the Four Gates

Run each gate in order. Report pass/fail with specifics. If any gate fails, fix the issue and re-verify that gate before moving on.

---

### Gate 1: Test Integrity

Run the test suite with coverage:

```
uv run pytest --cov -v
```

**Pass criteria:**
- All tests pass. Zero failures, zero errors.
- Code coverage is 100%. No uncovered lines.
- Edge cases from acceptance criteria are covered.
- Test names describe behavior (read them — they should tell a story).

**If this gate fails:**
- Identify uncovered lines or missing edge cases.
- Write the missing tests.
- Fix any failing tests (fix the code, not the test, unless the test is wrong).
- Re-run until 100% pass and 100% coverage.

---

### Gate 2: Health Check Completeness

Review the implementation code for defensive programming:

**Pass criteria:**
- Every external input is validated.
- Error handling exists for every failure mode.
- Errors produce clear, actionable messages.
- No silent failures (no bare `except:`, no swallowed exceptions).
- Graceful degradation for bad input (no crashes, no tracebacks to users).

**If this gate fails:**
- Add input validation where missing.
- Add error handling with descriptive messages.
- Add tests for the new error paths.
- Re-run Gate 1 to confirm tests still pass.

---

### Gate 3: Deployment Readiness

Run a clean install and execution check:

```
uv sync && uv run crew
```

**Pass criteria:**
- Command completes without errors.
- No manual steps required between install and run.
- Configuration is externalized (no hardcoded secrets, paths, or environment-specific values).
- A clean install from scratch works (no hidden dependencies on local state).

**If this gate fails:**
- Fix dependency declarations in pyproject.toml.
- Externalize any hardcoded configuration.
- Document any required environment variables in the memory bank.
- Re-run the command.

---

### Gate 4: Maintainability in Context

Run the linter and review code quality:

```
uv run ruff check .
```

**Pass criteria:**
- Ruff reports zero violations.
- Code follows patterns documented in `systemPatterns.md`.
- Code is self-documenting — variable names, function names, and structure explain intent.
- No unnecessary dependencies added.
- Any new dependencies are justified in the feature spec or commit message.

**If this gate fails:**
- Fix all ruff violations.
- Refactor code to match documented patterns.
- Remove unjustified dependencies.
- Re-run until clean.

---

## Step 3: Validate Acceptance Criteria

Walk through every acceptance criterion from the spec:

- For each Given-When-Then, confirm a test exists that covers it.
- If any criterion lacks a test, write one and re-run Gate 1.

## Step 4: Update Status

Update the feature spec status from IMPLEMENTED to VERIFIED.

Update `llm/memory_bank/activeContext.md` with:
- Verification results per gate.
- Any fixes applied during verification.
- Feature is ready for integration.

## Step 5: Report

Print a verification report:

```
## Verification Report: <Feature Name>

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS/FAIL | <details> |
| 2. Health Check | PASS/FAIL | <details> |
| 3. Deployment Readiness | PASS/FAIL | <details> |
| 4. Maintainability | PASS/FAIL | <details> |

**Overall: VERIFIED / FAILED**
```

## Hard Rules

- NEVER skip a gate. All four must pass.
- NEVER mark VERIFIED if any gate failed and was not re-verified after fixing.
- NEVER weaken a gate ("coverage is close enough"). 100% means 100%.
- NEVER modify acceptance criteria to make them pass. The spec is the contract.
- If a gate reveals a spec problem, stop and tell the user to update the spec first.
