# Feature: Fix Extraction Dependency Conflict (remove unused `denario`) + Honest Import Errors

**Status:** IMPLEMENTED
**Date:** 2026-07-14
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SM-4

## Problem

Entity extraction produced **zero entities** in CI and the deployed ingest Job,
and the failure was disguised as a missing package. There were two defects — but
the deep root cause turned out to be a dependency conflict, not merely loose pins.

### Defect 1 — a masking `except` block
Both `_get_instructor_client` methods (`llm_client.py:194` OpenAI, `:312`
Anthropic) caught *all* `ImportError` and re-raised
`LLMError("instructor package not installed. Install with: pip install
instructor")`. So an *installed-but-broken* `instructor` reported as *not
installed*, sending every debugger down the wrong path.

### Defect 2 — the real root cause: `denario` transitively pins `openai==1.99.9`
`packages/core/pyproject.toml` (and the root `pyproject.toml`) declared
`denario>=1.0.0`. **`denario` is imported nowhere in the entire `packages/`
codebase** — it was dead-weight in the dependency list. But it is not harmless:

```
denario 1.0.1
  → cmbagent >=0.0.1.post63
    → cmbagent-autogen >=0.0.91.post11
      → openai == 1.99.9      (an EXACT pin)
```

So any environment with `denario` installed is forced onto `openai 1.99.9`. The
only `instructor` releases that accept `openai 1.99.9` are `< 1.14`, and
`instructor 1.12.0` (what pip resolved in CI/Docker) **fails to `import
instructor`** against that openai. Meanwhile `instructor >= 1.14` *requires*
`openai >= 2.0`. Net: **with `denario` present, extraction could never get a
working instructor** — the "broken" combo was the *only* resolvable one, not bad
luck.

This also explains a red herring: forcing `instructor>=1.14` + `openai>=2.0`
while keeping `denario` made pip's resolver hit `resolution-too-deep` (it
backtracked forever across the huge cmbagent/autogen tree trying to satisfy the
unsatisfiable `openai>=2.0` vs `openai==1.99.9`). `uv` diagnosed it instantly as
a hard conflict.

User-visible effect: ingestion ran but extracted **zero entities**, with logs
blaming a package that was installed. This blocked the goal of running a larger
ingestion for human node review.

## Goals

- **Remove the conflict at its source:** drop the unused `denario` dependency from
  `packages/core/pyproject.toml` and the root `pyproject.toml`. This dissolves the
  `openai==1.99.9` pin and the entire cmbagent/autogen subtree.
- **Adopt a working instructor floor:** `instructor>=1.14` + explicit `openai>=2.0`
  (now satisfiable). Verified: a clean resolve yields `instructor 1.15.4` /
  `openai 2.46.0` and `instructor.from_openai(...)` constructs.
- **Honest error messages:** a genuinely-absent package still says "not installed";
  an installed-but-broken import surfaces the *real* error.
- **A regression guard:** a hermetic unit test proves `import instructor` +
  `instructor.from_openai(...)` succeed in the resolved env.
- **End-to-end proof:** a smoke ingestion extracts **> 0 entities**.

## Non-Goals

- **Re-integrating `denario`.** The project's docs describe denario as the core
  paper-generation library, but the code never imports it. If/when denario is
  actually wired in, it should be an **optional extra** (isolated install), never a
  hard dep of the extraction path — otherwise it re-pins `openai==1.99.9`. Tracked
  as a note in the backlog; not this feature's job.
- **A `uv.lock` / adopting uv in CI.** With `denario` gone, pip resolves core in
  <1s — no lock needed. (`uv` was used here only as a *diagnostic* to expose the
  hard conflict pip hid behind `resolution-too-deep`.) A whole-graph lock stays
  deferred (SM-4b) and is now low priority.
- **Fixing test-tree lint debt (SM-5)** or changing extraction logic/prompts/schema.

## User Stories

- As an operator running the ingest Job, I want extraction dependencies to resolve
  a working `instructor`/`openai`, so a green CI means a working deployed Job.
- As a developer debugging an extraction failure, I want the error to name the real
  cause, so I don't chase a phantom "not installed" package.

## Design Approach

Three changes; no new toolchain.

### 1. Remove the unused `denario` dependency (the fix)
Delete `denario>=1.0.0` from both `packages/core/pyproject.toml` and root
`pyproject.toml`. Verified repo-wide that no Python module imports `denario`
(`grep -rn 'import denario\|from denario' packages/` → empty). This removes the
transitive `openai==1.99.9` pin and the cmbagent/autogen tree entirely.

### 2. Adopt a working instructor/openai floor
`instructor>=1.14` (bans the non-importing `<1.14` line) + explicit `openai>=2.0`
(instructor 1.14 requires it anyway; pinned directly for clarity). Root
`openai>=1.0.0` → `openai>=2.0` to keep the combined `.[dev]` + core install
consistent.

### 3. Distinguish "not installed" from "installed but broken"
Rework both `_get_instructor_client` methods so the `except` chain separates the
two cases (`ModuleNotFoundError` before the broader `ImportError`) and never masks
the real error:

```python
try:
    import instructor
except ModuleNotFoundError as e:            # genuinely absent
    raise LLMError("instructor package not installed. Install with: pip install instructor") from e
except ImportError as e:                    # present, but its import chain broke
    raise LLMError(f"instructor is installed but failed to import — likely a dependency version conflict: {e}") from e
```

### Verification performed (evidence)
- `uv pip compile packages/core/pyproject.toml --python-version 3.12`:
  **0.8s clean resolve** → instructor 1.15.4, openai 2.46.0, pydantic 2.13.4
  (was `resolution-too-deep` / hard conflict with denario present).
- Combined root `.[dev]` + core resolve (mirrors the CI test job): clean, no
  denario/cmbagent.
- Fresh clean-resolve venv (py3.12): `instructor.from_openai(...)` OK; the
  extraction test files pass (32 passed).

## Sample Implementation

```python
# packages/core/src/agentic_kg/extraction/llm_client.py
# Both _get_instructor_client methods (OpenAIClient ~189, AnthropicClient ~307);
# only the from_openai / from_anthropic call differs.

def _get_instructor_client(self):
    if self._instructor_client is None:
        try:
            import instructor
        except ModuleNotFoundError as e:
            raise LLMError(
                "instructor package not installed. Install with: pip install instructor"
            ) from e
        except ImportError as e:
            raise LLMError(
                f"instructor is installed but failed to import — likely a "
                f"dependency version conflict: {e}"
            ) from e
        client = self._get_client()
        self._instructor_client = instructor.from_openai(client)  # from_anthropic in the Anthropic client
    return self._instructor_client
```

```toml
# packages/core/pyproject.toml (and root pyproject.toml) — the dependency fix
# - REMOVE: "denario>=1.0.0"   # unused; transitively pins openai==1.99.9
# + "instructor>=1.14"
# + "openai>=2.0"
```

## Edge Cases & Error Handling

### instructor genuinely not installed
- **Behavior**: `ModuleNotFoundError` → `LLMError(... not installed ...)`.
- **Test**: `test_module_not_found_reports_not_installed`.

### instructor installed but import raises (the SM-4 symptom)
- **Behavior**: non-ModuleNotFound `ImportError` → `LLMError(... failed to import
  ... version conflict: <real error>)`, traceback chained via `from e`.
- **Test**: `test_broken_import_reports_version_conflict`.

### A future dep reintroduces the openai==1.99.9 pin (e.g. denario re-added)
- **Behavior**: a clean resolve conflicts (or the hermetic guard fails on a broken
  import). The fix is to keep denario/cmbagent off the extraction path.
- **Test**: `test_instructor_imports_and_constructs_in_resolved_env` (guard) +
  CI's own resolve step.

## Acceptance Criteria

### AC-1: Unused `denario` removed; extraction deps resolve cleanly
- **Given** `packages/core/pyproject.toml` and root `pyproject.toml`
- **When** dependencies are resolved
- **Then** `denario` is absent from both, `instructor>=1.14` + `openai>=2.0` are
  present, and a clean resolve succeeds with no conflict / no `resolution-too-deep`

### AC-2: Hermetic import test guards the resolved env
- **Given** the resolved environment
- **When** the `packages/core` unit suite runs
- **Then** `test_instructor_imports_and_constructs_in_resolved_env` passes

### AC-3: "Not installed" vs "broken import" distinguished (both clients)
- **Given** the reworked `_get_instructor_client` in `OpenAIClient` and
  `AnthropicClient`
- **When** `import instructor` raises `ModuleNotFoundError` vs a plain `ImportError`
- **Then** the two distinct `LLMError` messages are produced; the broken-import
  message contains the original error text, chained via `from e`

### AC-4: Smoke ingestion extracts entities
- **Given** the resolved ingest environment with a valid OpenAI key
- **When** a smoke ingestion of a known paper runs
- **Then** it completes and writes **> 0 entities** (manual/e2e verification)

## Technical Notes

- **Affected components**:
  - `packages/core/pyproject.toml` (remove denario; `instructor>=1.14`,
    `openai>=2.0`)
  - root `pyproject.toml` (remove denario; `openai>=2.0`)
  - `packages/core/src/agentic_kg/extraction/llm_client.py` (two `except` blocks)
  - New `packages/core/tests/extraction/test_instructor_import.py`
- **Diagnostic tool**: `uv` exposed the hard `openai==1.99.9` conflict that pip
  masked as `resolution-too-deep`. uv is not added to the project — only used to
  diagnose.
- **This change touches `packages/core`**, so merging triggers the first real
  `build` + `deploy-staging` on the now-green pipeline — also the first exercise of
  deploy-pipeline-fix AC-6 (SHA parity).

## Dependencies

- **deploy-pipeline-fix** (AC-5 green) — this rides on the repaired pipeline.

## Open Questions

- **Does anything operationally need `denario`?** Verified no code imports it. If a
  future feature needs denario's paper-generation, wire it as an **optional extra**
  in an isolated environment/service so it can't re-pin `openai` on the extraction
  path.
- **Is AC-4 automated or manual?** No smoke-ingest workflow exists yet; AC-4 is a
  documented manual run for now.

## Review Log

| # | Persona | Question | Resolution |
|---|---------|----------|------------|
| 1 | Tech Lead | Root vs core lock boundary for a `uv.lock`? | Moot — no lock needed once the real conflict was found. |
| 2 | QA | Does the guard test validate the shipped graph, not just the pip test env? | With denario gone, CI and the Job resolve the same (now-trivial) graph; the standard unit-suite guard suffices. |
| 3 | Tech Lead | Is `uv`/lock justified, or just a too-low floor? | Neither — **empirical finding during implementation:** `denario` (unused) transitively pins `openai==1.99.9`, making a working instructor impossible and blowing up pip's resolver. Fix = remove denario. Spec rewritten from "floors-first" to "remove the conflicting unused dep." `uv` kept only as a diagnostic. |
