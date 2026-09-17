# Active Context

Last updated: 2026-09-17

> Newest entries at the top. History through 2026-07 lives in
> [`archive/activeContext-through-2026-07.md`](archive/activeContext-through-2026-07.md)
> (moved there by a `memory:revise` on 2026-09-17). Keep this file under ~200
> lines — archive again rather than letting it sprawl.

## Current State (2026-09-17)

**The CI smoke gate passes end to end for the first time.** `Smoke Test — Ingest`
had never once had a green run since it was added — not a regression, a gate
that never worked. Three independent defects, each hiding the next:

1. **`import fitz` printed a deprecation warning to stdout** (#52). The workflow
   redirects `agentic-kg ingest --json > ingest_result.json`, so the warning
   became line 1 and `json.load` died at char 0. **The six graph-shape
   assertions had never executed at all** — every prior fix attempt (SM-8b's
   taxonomy seeding, SM-8's B3-linker fix) was aimed at checks that were not
   running. Fixed by importing `pymupdf` (pin `>=1.24.3`).
2. **The topic assertion queried `BELONGS_TO` for Paper→Topic** (#52), which
   does not exist — that edge is `RESEARCHES`. Could never have passed.
3. **Retries bypassed the S2 rate limiter** (#56). `_make_request` acquired one
   token then handed the call to `retry_with_backoff(max_retries=3)`, so a
   single call could fire four requests back-to-back — a 4x overshoot of S2's
   1 RPS cumulative ceiling. The acquire now lives inside the retried function.

Plus a credential: `SEMANTIC_SCHOLAR_API_KEY` is set. Unauthenticated S2 draws
on a pool shared with every other anonymous caller, so `populate_citations`
failed for every paper and `CITES` landed 0 edges.

Assertion progress: **0 ever executed → 4/6 → 5/6 → 6/6**, `cites=19`.

**Governance is at agentic-governance v0.9.0** (#59–#64, 2026-09). Since the
v0.5 migration: canon location declared, `--layout` assertion added to CI, the
Plans slot declared (`llm/plans/`), and an artifact-routing rule installed in
`CLAUDE.md` + `AGENTS.md`. Control plane is `llm/`, data plane is `docs/`.

**Tests:** ~2207 passing (SEG-1 added +118).

### Open / in flight

- **#66 (open)** — `purge_paper_extraction` named relationships that do not
  exist in *both* topic-edge deletions: Paper→Topic matched `BELONGS_TO`
  (should be `RESEARCHES`) and ProblemConcept→Topic matched `HAS_TOPIC`, which
  is written nowhere in the codebase. Neither could ever match, so
  re-ingestion never purged topic edges and they accumulated one generation per
  run. The purge *is* live (`ingestion.py:572`), so this affected real data.
  Also repoints `.gcloudignore` at `llm/`.
- **#53 merged** (`d5d41fa`) — ground-truth Output B, paper 1 of 8. Victoria is
  continuing on the remaining 7.
- **Known-stale:** `techContext.md`'s setup block says `uv sync`, and a
  2026-09-07 note claims uv "was deliberately not adopted (SM-4)". Both are
  wrong as written — `uv` is installed and
  `uv run --with-editable ./packages/core --with pytest` runs the suite
  locally. Corrected in `techContext.md` on 2026-09-17.
- **No `.gitattributes`, `core.autocrlf` unset** — untouched files show as
  fully modified from CRLF/LF churn on Windows/WSL checkouts. Worth `* text=auto`.

### Carried follow-ups

- Promote `Governance Checks` to a *required* check (several green runs now).
- Triage the failing `cleanup-preview` GHA job.
- `deploy-pipeline-fix` PR-2 (Terraform lifecycle guardrail + AC-8 lint) and
  PR-3 (version pinning).
- Scrub the staging Neo4j browser endpoint from `docs/status/service-inventory`.
- If the nightly flakes on `CITES`, drop `SEMANTIC_SCHOLAR_RATE_LIMIT` to 0.9 —
  the circuit breaker still opened once even with correct pacing, so 1 RPS is
  tight for this workload.

---

## SEG-1 VERIFIED (2026-09-07)

Spec → IMPLEMENTED, all 15 ACs met, **+118 tests** (2089 → 2207 passing; the 16 remaining failures are **pre-existing on clean `HEAD`** — verified by stashing — in `test_assert_deploy_parity`, `test_site_structure`, `test_smoke_assert`, `test_smoke_workflow_structure`, `test_pdf_extractor`, `test_pipeline`).

**Files created**
- `scripts/measure_segmentation.py` — per-paper before/after table vs `scripts/seg_baseline.json`, non-zero on regression, `--update-baseline` for the deliberate re-baseline. 100% covered.
- `scripts/seg_baseline.json` — post-SEG-1 chars + `(type, title)` sequence per paper.
- `packages/core/tests/test_measure_segmentation.py` (36 tests) — the comparison logic, PDF-free so it runs in CI.
- `packages/core/tests/extraction/fixtures/segmenter/ieee_roman_excerpt.txt` + `README.md` — 125 lines of **real** PyMuPDF output (running headers, ligatures, `A.`/`B.` subsections intact).

**Files modified**
- `extraction/section_segmenter.py` — `_ROMAN` + `_ENUMERATOR`; strip in `_classify_heading`; DEBUG-log rejected candidates in `_find_headings`; prefix group deleted from all 34 literals + the duplicate intro pattern. `segment_with_abstract` untouched (SEG-2's call).
- `ingestion.py` — `_found_section_types` / `_missing_wanted_sections` + one WARN in the per-paper loop.
- 3 test files, `docs/ground-truth/segmenter-findings.md`, `llm/features/BACKLOG.md`.

**Measured, reproduced against the corpus:** `fact_completion` 0 → **25,586**; `empire` 12,405 → **17,953** (gains `introduction` titled `I. INTRODUCTION`); six papers byte-identical *including* their section sequences. Verified honestly by baselining from `git show HEAD:...section_segmenter.py` first, then diffing the new code against it — not by asserting the spec's numbers.

**Decisions that changed during implementation**
1. **`_found_section_types` treats an empty-content section as not found** (Phase 5 review). Otherwise it disagrees with `_build_extractor_section_text`, and an empty `abstract` section would silence the AC-13 warning on a document contributing nothing.
2. **Fixed a latent bug in my own harness**: `path: Path = BASELINE_PATH` as a default arg binds at import, so a re-pointed `BASELINE_PATH` was silently ignored. It also caused a test run to overwrite the real baseline — caught by the two `committed_baseline` tests, which is precisely why they exist. Now resolved at call time.
3. **Corrected a misleading comment**: `count=1` is not what stops `II.B. Results`; the `^` anchor without `re.MULTILINE` already makes a second match unreachable.

**Tests are mutation-verified.** Re-introducing each rejected variant fails the suite: `[A-Z]` → 4 failures (incl. the document-level test that encodes the measured 13,137-char loss), hierarchical `4.4.` → 3, optional separator → 7.

**Coverage:** `section_segmenter.py` 97% — the 4 uncovered lines are `segment_with_abstract`'s dead `if abstract_match:` branch, i.e. the SEG-2 defect itself, deliberately left unexercised per AC-12. `ingestion.py` 99% (the 3 uncovered are the pre-existing `_paper_has_footprint` Cypher helper). `measure_segmentation.py` 100%. Ruff clean on `packages/core/src` and on every new file.

**Environment note:** the repo had **no working test venv** (`.venv` absent, `.venv-gt` has no pytest). Created `.venv` from pyenv 3.12.9 with `-e ./packages/core -e ".[dev]"`; it is gitignored. Note the Constellize implement skill's commands assume `uv run pytest` / `uv run crew`, neither of which exists here — `uv` was deliberately not adopted (SM-4) and the CLI is `agentic-kg`.

**Verify gates — all four PASS.**

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS | 229 tests across the feature's suites; **100% line coverage** on `section_segmenter.py`, `ingestion.py` and `measure_segmentation.py` (542 stmts, 0 missed). |
| 2. Health Check | PASS | No bare excepts; narrow exception types deliberately let programming errors propagate (the SM-9 lesson) while data errors are reported per-paper; exit codes distinguish setup (2) from regression (1); `--update-baseline` refuses to bake an incomplete run. |
| 3. Deployment | PASS | Zero new dependencies; script is cwd-independent (verified by running it from `C:\`); no hardcoded secrets or absolute paths; `agentic-kg` entry point intact. |
| 4. Maintainability | PASS | Ruff clean on `packages/core/src` (the actual CI gate) and on every file this feature created or modified. Follows `smoke_assert.py`'s script shape and pragma convention. |

**Fixes applied during verification (3):**
1. Closed the last 4 uncovered lines — `segment_with_abstract`'s dead branch — with two tests instead of a pragma. They pin the SEG-2 defect exactly: the branch is reachable *only* when the abstract label sits at offset 0, which no real PDF satisfies. Useful input for SEG-2's fix-or-delete call.
2. Covered the pre-existing `_paper_has_footprint` helper (3 lines, untested anywhere before) to reach 100% on `ingestion.py`.
3. Gate 2 caught an unvalidated external input: a hand-edited baseline of the wrong shape raised a bare `AttributeError`. `_load_baseline` now validates and names the offending slugs.

**Pre-existing issues found but deliberately not fixed** (all verified present on clean `HEAD`):
- **16 suite failures + an `agentic-kg ingest --help` traceback are Windows-host problems in test/CLI code**, not product defects: cp1252 decode of em dashes (`cli.py:444` has `↔`; `test_smoke_workflow_structure` compares against a workflow name containing one), POSIX path assumptions (`'/path/to/paper.pdf'` vs `\path	o\paper.pdf`), and `bash` receiving an unconverted Windows path (9 of the 16, in `test_assert_deploy_parity`). All green on the Linux CI runner. **Worth a backlog item** — this is now the third distinct instance and it makes local development on Windows noisy.
- 25 ruff findings in `packages/api/src`, which no CI gate lints (`code-review.yml` / `deploy-*.yml` all lint `packages/core/src` only).

**Next:** spec **SEG-3** (run-in abstracts). Neither IEEE paper has an abstract yet, and neither has a methods section (SEG-5). **SEG-11 should be scheduled with SEG-5** — SEG-5's cheap fix (relax the method/experiment anchors) widens the phantom-heading surface.

---

## SEG-1 SPECIFIED (2026-09-06) — segmenter work begins

Branch `docs/ground-truth-cskg-reconciled`. Spec: `llm/features/seg1-roman-numeral-headings.md` (SPECIFIED, 15 ACs). First of the SEG-* segmenter defects to get a spec; **SEG-3 (run-in abstracts) is the agreed next one.**

**What the specification established by measurement**, not argument — the real `SectionSegmenter` was run against all 8 PDFs in `ground-truth-papers/` via `.venv-gt`, comparing four prefix variants:

| Paper | current | Roman + Arabic | Roman + `[A-Z]` |
|---|---:|---:|---:|
| `fact_completion` | 0 | **25,586** | 12,449 |
| `empire` | 12,405 | **17,953** | 17,953 |
| other 6 | — | byte-identical | byte-identical |

Four decisions, each with a number behind it:
1. **No `[A-Z]` prefix** — the backlog's own proposal. IEEE letters mark *subsections*, so `B. RESULTS AND DISCUSSION` (a child of `IV. EVALUATION`) gets promoted to top level, truncating `experiments` 3,045w → 1,032w and diverting the tail into a discarded type: **−13,137 chars**.
2. **Strip the enumerator in `_classify_heading`; delete the prefix group from all 34 literals.** One mechanism, so SEG-3/4/5 vocabulary inherits numbering. Blast radius covered by a new 14-row table-integrity test that also brings 4 previously untested section types (`background`, `discussion`, `acknowledgments`, `appendix`) under test.
3. **Flat enumerators only** — hierarchical `4.4.` stripping costs `hypothesis_generation` **5,585 chars** via the identical promote-the-child failure.
4. **Verification in 3 artifacts** — `scripts/measure_segmentation.py` + `seg_baseline.json` (local; `ground-truth-papers/` is gitignored at `.gitignore:75`, so no PDF test can run in CI), a ~150-line *real* PyMuPDF excerpt fixture with running headers and ligatures intact, and PDF-free unit tests. Baseline is JSON with `--update-baseline`, because SEG-3/4/5/7 will each legitimately move those numbers and a hand-edited guard gets commented out.

**Two findings that outlived the spec:**
- **The findings doc's "silent" framing is stale.** SM-1 moved `_build_extractor_section_text` ahead of `MIN_USABLE_CHARS` (`ingestion.py:291-296`), so `fact_completion` is logged `PDF source too thin: 0 chars`, counted `failed_thin`, and **skipped** (`:553-565`). The cost is a *dropped* paper worth 32 of 53 chain entities. What is still silent is the **partial** case — `cskg` clears the gate at 8,917 chars with no abstract and no methods and logs `INFO Full text acquired`. Spec AC-13 adds a WARN naming the missing types; AC-14 adds a DEBUG line per rejected heading candidate.
- **A new root cause, not among the findings doc's seven → SEG-11.** `_classify_heading("MODEL") == methods`: bare `model`/`method`/`approach`/`technique` patterns match single-word body and table lines. 5 phantom `methods` sections in `llm_ontology_gen` (a table column header), 4 in `kg_validation_hitl`, 2 in `kg_construction_survey`, 1 in `empire`. **This is why `empire` appeared to have a methods section at all** — its only `methods` block is a phantom `Method` line, so SEG-1's +5,548 is entirely intro/background and the paper still has no true methods section. Spec AC-15 files it as SEG-11 (not SEG-8 — taken) and notes the `SEG-n ↔ cause (n)` convention stops holding past (7).

**Next:** `/constellize-feature-implement seg1-roman-numeral-headings`, then spec SEG-3.

---
