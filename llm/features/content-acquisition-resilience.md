# Feature: PDF Acquisition Reliability (source selection + fetch hardening)

**Status:** VERIFIED
**Date:** 2026-07-14
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SM-1

## Problem

Papers reach the entity extractors with **no usable full text**, so extraction is
skipped and the graph gets **zero entities** — the top remaining blocker to the
"run a larger ingestion and review nodes" goal now that SM-4 unblocked the
`instructor` import.

Observed in the CI smoke-ingest (`Ingest + Assert`, on the SM-4 merge):

```
Stage 1: PDF Extraction - Failed: Error downloading PDF: Server disconnected without sending a response.
Concept extraction skipped: no input sections
...
papers=3, concepts=0, models=0, methods=0, cites=0
```

The **root cause is source selection, not flakiness.** The failing paper
("Benchmarking LLMs in RAG") is on arXiv (`arXiv:2309.01431`), and the normalizer
*already captures the arXiv ID* (`external_ids["arxiv"]`,
`normalizer.py:179-180`). But `pdf_url` is set **only** from Semantic Scholar's
`openAccessPdf.url` (`normalizer.py:201-204`) — here the publisher URL
`ojs.aaai.org/...`, which drops GitHub Actions' datacenter IPs ("Server
disconnected"). The reliable, CI-friendly arXiv PDF for the *same paper* is never
tried. Confirmed: that AAAI URL downloads fine from a normal IP, so this is
IP/host-level blocking a retry can't fix — but arXiv would have worked.

Compounding gaps:
- **PDF fetch sends no request headers** (`pdf_extractor.py:147` bare
  `client.get(url)`) — no `User-Agent`/`Accept`; some hosts reject this.
- **PDF fetch has no retry** on genuinely-transient `httpx.RequestError`.
- **Semantic Scholar `429` gives up**, silently dropping papers under load.

**Design decision (explicit):** if the full paper text cannot be acquired, the
paper's extraction **fails loudly** — there is **no abstract fallback**. We do not
manufacture low-value abstract-only nodes to make a run look successful.

## Goals

- **Reliable full-text acquisition:** try the **published/authoritative source
  first** (`openAccessPdf`), then the **arXiv PDF as a reliability fallback** when
  the published copy is unreachable. The smoke papers still extract full text (the
  blocked publisher URL falls through to arXiv).
- **Fetch hardening:** send browser-like request headers; retry transient
  `httpx.RequestError` with bounded backoff (not permanent 404s).
- **No silently-dropped papers under rate limits:** retry Semantic Scholar `429`
  with bounded backoff (hard cap); on exhaustion, count the paper, don't crash.
- **Fail loudly, don't fake success:** a paper with no acquirable full text is
  recorded as an extraction failure and surfaced in run metrics — never a silent
  0-entity "success."

## Non-Goals

- **Abstract fallback / any non-full-text extraction.** Explicitly rejected: full
  text or the paper fails. (Supersedes an earlier draft that proposed abstract
  fallback + `text_source` provenance — both removed.)
- **OCR of scanned PDFs.** A scanned/garbage PDF is an acquisition failure, not an
  OCR task.
- **New paper sources / a proxy to defeat publisher IP blocks.** Scope is *choosing
  the reachable source we already have* (arXiv) and hardening the fetch — not
  scraping paywalled hosts.
- **Improving extractor quality on good full text.** This is about *getting* full
  text to the extractors.

## User Stories

- As an operator running a larger ingestion, I want the pipeline to fetch the
  reliable arXiv PDF when it exists, so papers actually produce entities instead of
  failing on a CI-hostile publisher URL.
- As an operator, I want papers that truly can't be fetched to fail loudly with a
  clear reason and show up in a run summary, so failures aren't hidden as empty nodes.

## Design Approach

### 1. Candidate-source PDF selection (the root-cause fix)
Change PDF-URL resolution so it produces an **ordered list of candidate PDF URLs**,
not a single URL. **Published/authoritative source first, arXiv as the reliability
fallback** (per review — the published version is canonical for a research KG; the
arXiv preprint is only used when the published copy can't be fetched):
1. `openAccessPdf.url` (S2 — typically the published/venue copy)
2. arXiv PDF (`https://arxiv.org/pdf/{arxiv_id}`) when `external_ids["arxiv"]` exists
3. (future, SM-1b) Unpaywall / broader OA resolver for non-arXiv papers

The pipeline tries candidates in order until one yields usable full text.

**Latency guard (breadth before depth):** the preferred (published) source is often
the one that blocks CI IPs, so the loop must not spend the full backoff budget on it
before reaching the reachable arXiv copy. Each candidate gets **one quick attempt**
(transient retries minimal/off) on the *first* pass; heavy bounded backoff is a
last-resort *second* pass only if every candidate failed its quick attempt. This
keeps a blocked-publisher paper falling through to arXiv in ~1 RTT, not tens of
seconds.

### 2. Fetch hardening in `PDFExtractor.extract_from_url`
- Send headers: a browser-like `User-Agent` and `Accept: application/pdf,*/*`.
- Retry transient `httpx.RequestError` (connect/read/disconnect) with bounded
  exponential backoff (reuse `resilience.py`); do **not** retry 4xx
  `HTTPStatusError` (a 404 won't reappear).

### 3. Semantic Scholar 429 retry with bounded backoff
Treat `429` as retryable with `wait_exponential(max=…)` + `stop_after_attempt(…)`
(hard cap so ingestion degrades gracefully). On exhaustion, increment `dropped_429`
and continue the batch.

### 4. Fail-loud on no full text (no fallback)
If every candidate source fails to yield ≥ `MIN_USABLE_CHARS`, record an
`ExtractionFailure` (existing mechanism) with the reason and the URLs tried, log a
WARNING, and count the paper as `extraction_failed`. Extractors are **not** run on
partial/abstract text.

### 5. Per-run metrics — failures categorized by reason (QA #1)
At batch end, log a structured summary: totals, `%` of papers yielding ≥1 entity,
`pdf_ok`, `dropped_429`, and **failures broken down by reason** so systemic vs
genuine is visible at a glance:
- `failed_blocked` — a candidate host dropped/refused us (retries exhausted)
- `failed_404` — candidate URL returned 404
- `failed_thin` — a PDF downloaded but extracted `< MIN_USABLE_CHARS`
- `failed_no_pdf_source` — no arXiv ID and no `openAccessPdf` at all (genuinely
  unavailable / likely paywalled)

Each `ExtractionAcquisitionError` carries a `reason` enum so the counts aggregate
without log-scraping. A high `failed_blocked` signals "fix the fetcher"; a high
`failed_no_pdf_source` signals "these are genuinely unavailable."

## Sample Implementation

```python
# data_acquisition/normalizer.py — ORDERED candidate PDF URLs: PUBLISHED first, arXiv fallback
def candidate_pdf_urls(self) -> list[str]:
    urls = []
    if self.pdf_url:                                        # published/venue copy (authoritative)
        urls.append(self.pdf_url)
    arxiv_id = self.external_ids.get("arxiv")
    if arxiv_id:                                            # reliability fallback (CI-friendly)
        urls.append(f"https://arxiv.org/pdf/{arxiv_id}")
    return urls
```

```python
# extraction/pipeline.py — try candidates; NO abstract fallback (fail loud)
MIN_USABLE_CHARS = 250

async def acquire_full_text(self, paper) -> SegmentedDocument:
    tried = []
    for url in paper.candidate_pdf_urls():
        tried.append(url)
        try:
            extracted = await self.pdf_extractor.extract_from_url(
                url, timeout=self.config.pdf_timeout)      # headers + transient retry inside
            if len(extracted.full_text.strip()) >= MIN_USABLE_CHARS:
                logger.info(f"Full text via {url} ({extracted.total_chars} chars)")
                return self.section_segmenter.segment(extracted.full_text)
        except PDFExtractionError as e:
            logger.warning(f"PDF source failed ({url}): {e}")

    # No fallback. Fail this paper explicitly and loudly.
    raise ExtractionAcquisitionError(
        f"No usable full text for {paper.doi}; tried {tried}")
```

```python
# extraction/pdf_extractor.py — headers + bounded transient retry
_HEADERS = {"User-Agent": "agentic-kg/1.0 (+https://github.com/djjay0131/agentic-kg)",
            "Accept": "application/pdf,*/*"}

@retry(retry=retry_if_exception_type(httpx.RequestError),   # transient only
       stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def _get(self, client, url):
    return await client.get(url, headers=_HEADERS)
# httpx.HTTPStatusError (404) is NOT retried — raised straight to caller.
```

```python
# batch/importer — fail-loud accounting + metrics
logger.info("Ingest summary: papers=%d with_entities=%d (%.0f%%) "
            "pdf_ok=%d extraction_failed=%d dropped_429=%d",
            total, with_entities, 100*with_entities/max(total,1),
            pdf_ok, extraction_failed, dropped_429)
```

## Edge Cases & Error Handling

### Published URL blocked in CI, arXiv version exists
- **Scenario**: `openAccessPdf` = blocked publisher host; arXiv ID present.
- **Behavior**: published URL attempted first (quick), fails fast → falls through
  to arXiv PDF → full text acquired.
- **Test**: paper with both; assert published URL attempted first, then arXiv used.

### PDF fetch fails transiently ("Server disconnected")
- **Scenario**: `httpx.RequestError` mid-download.
- **Behavior**: retried with bounded backoff; on persistent failure, next candidate.
- **Test**: mock `RequestError` N-1 times then success → retried; always-fail → next candidate.

### PDF returns 404 (permanent)
- **Scenario**: candidate URL dead.
- **Behavior**: **not** retried; move to next candidate immediately.
- **Test**: mock `HTTPStatusError` 404 → single attempt, next candidate.

### No candidate yields usable text
- **Scenario**: no arXiv ID and publisher URL unreachable/paywalled.
- **Behavior**: `ExtractionAcquisitionError` raised → paper `extraction_failed`,
  WARNING with URLs tried; **no abstract fallback**; batch continues.
- **Test**: all candidates fail → assert failure recorded, counted, no entities, no crash.

### Semantic Scholar 429 storm
- **Scenario**: repeated 429 across a large run.
- **Behavior**: bounded retry per call; on exhaustion `dropped_429 += 1`, continue.
- **Test**: mock 429 for all attempts → capped attempts + counter increment, no escape.

## Acceptance Criteria

### AC-1: Published source tried first, arXiv fallback when unreachable (root-cause fix)
- **Given** a paper with an `openAccessPdf` (published) URL and an arXiv ID
- **When** the pipeline acquires text and the published URL is reachable
- **Then** it uses the published copy; **and** when the published URL is
  blocked/unreachable, it falls through to the arXiv PDF and extracts full text
  (≥ `MIN_USABLE_CHARS`) — the published source is always attempted first

### AC-2: Smoke-ingest goes green via full text (hard gate)
- **Given** the CI `Ingest + Assert` smoke test with arXiv-available papers
- **When** the run completes
- **Then** graph-shape checks pass (`concepts/models/methods ≥ 1`) because full text
  was acquired — **not** via any fallback

### AC-3: PDF fetch sends headers and retries transient errors only
- **Given** `extract_from_url`
- **When** a transient `httpx.RequestError` vs a 404 occurs, and any successful GET
- **Then** requests carry a `User-Agent`; transient errors retry with bounded
  backoff; 404 fails after one attempt

### AC-4: No abstract fallback — no-full-text fails loudly
- **Given** a paper with no acquirable full text
- **When** ingestion processes it
- **Then** it raises/records an `ExtractionAcquisitionError`, is counted as
  `extraction_failed`, produces **zero** entities, and the batch continues (no
  abstract-derived nodes are created)

### AC-5: Semantic Scholar 429 retried, then counted (not dropped silently)
- **Given** an S2 search/lookup returning 429
- **When** the client retries to the cap and still gets 429
- **Then** the paper is counted as `dropped_429` and the batch continues without crashing

### AC-6: Per-run coverage/failure metrics logged, failures categorized
- **Given** a completed ingest batch
- **When** it finishes
- **Then** a summary logs total, `%` with ≥1 entity, `pdf_ok`, `dropped_429`, and
  failures **broken down by reason** (`failed_blocked` / `failed_404` /
  `failed_thin` / `failed_no_pdf_source`), each `ExtractionAcquisitionError`
  carrying a `reason` enum

### AC-7 (forward-looking): Ground-truth diff as additional verification
- **Given** the human-curated ground-truth set (`docs/ground-truth/`) once it lands
- **When** the importer runs on those papers
- **Then** its output is diffed against the answer key as an accuracy check (ties
  into D-2). *Activates when the set is ready; not a blocker for this feature's DoD.*

## Technical Notes

- **Affected components**:
  - `data_acquisition/normalizer.py` — `candidate_pdf_urls()` (arXiv-first)
  - `extraction/pdf_extractor.py` — request headers + transient-only retry
  - `extraction/pipeline.py` — candidate-source loop + fail-loud (no fallback)
  - `data_acquisition/semantic_scholar.py` (+ aggregator) — 429 retry + `dropped_429`
  - `extraction/batch.py` / importer — metrics + `extraction_failed` accounting
- **Patterns to follow (stars)**: `data_acquisition/resilience.py` (backoff),
  `rate_limiter.py`, existing `ExtractionFailure` records, `SectionSegmenter`.
- **Config**: `MIN_USABLE_CHARS`, retry caps live in `ExtractionConfig` /
  acquisition config (externalized).
- **Data model change**: none required (no `text_source` — fallback removed).
  Optionally an `extraction_status` marker on Paper is an Open Question.

## Dependencies

- **SM-4** (merged) — extraction can import `instructor`; prerequisite for any entities.
- **`docs/ground-truth/`** curation (in progress) — AC-7 only.

## Verification (2026-07-14) — all four gates PASS

| Gate | Result | Notes |
|------|--------|-------|
| 1. Test Integrity | PASS | 137 SM-1 tests green; broad unit gate 1563 passed. SM-1 code fully covered (ingestion 99% — only pre-existing `_paper_has_footprint` uncovered; new pdf_extractor retry/exhaustion + normalizer `candidate_pdf_urls` fully covered). Whole-file 100% is not the project standard; remaining misses are pre-existing untouched code. |
| 2. Health Check | PASS | Categorized failures (no silent drops), no bare `except`, clear error messages, per-paper isolation keeps the batch alive, metadata/citations persist on acquisition failure. |
| 3. Deployment Readiness | PASS | Changed modules import cleanly; `agentic-kg --help` works; no new dependency (`tenacity` already declared); thresholds/headers externalized as module constants; no secrets/paths hardcoded. |
| 4. Maintainability | PASS | Source 100% ruff-clean; new test file clean. Remaining test-tree E501s are pre-existing SM-5 debt (deploy gate lints src only, by design). |

## Implementation Notes (2026-07-14)

- **Resolved open question — metadata/citation persistence:** a no-full-text paper
  **keeps its Paper metadata + `CITES` edges** (imported in Phase 1b *before*
  extraction) and is simply skipped for entity extraction. No stub/marker node
  is added; the failure is recorded in `IngestionResult.acquisition_failures`
  and `extraction_errors`. This is the lean option from the original question.
- **Deviations from spec sample:** the candidate loop lives in a new tested
  `ingestion._acquire_full_text` helper (not `pipeline.py`) — it sits with the
  paper/candidate/metrics state and is the maintainability down-payment on the
  `ingest_papers` god-loop (full refactor tracked as SM-1c).
- **Latency guard:** implemented as a small per-URL transient retry
  (`stop_after_attempt(3)`, `wait_exponential(max=4)`) rather than the full
  two-pass breadth-then-depth; the candidate loop provides breadth. Sufficient
  for AC-3; revisit only if large-run latency shows a problem.
- **AC-6 log line** is emitted but not unit-asserted (low value); the underlying
  counters (`pdf_ok`, `acquisition_failures`, `sources_rate_limited`) are asserted.

## Open Questions
- **arXiv PDF URL form / versioning** (`/pdf/{id}` vs `/pdf/{id}v1`) and abstract-page
  vs pdf endpoint — validate the exact reliable URL during implementation.
- **Exact retry caps** and the breadth-first vs depth (per-candidate quick attempt,
  then bounded backoff second pass) — tune from run metrics.
- **Non-arXiv tail (SM-1b):** papers with a blocked published URL and no arXiv ID
  will `failed_blocked` / `failed_no_pdf_source`. Broader OA resolution (Unpaywall:
  DOI → best OA PDF) is deferred to SM-1b; the categorized metrics make the size of
  this tail visible so we know whether SM-1b is worth it.

## Review Log

| # | Persona | Question | Resolution |
|---|---------|----------|------------|
| 1 | Tech Lead | Is "green via abstracts" fixing the problem or hiding it? Should abstract fallback be co-equal with PDF fetch? | **Reframed the whole feature.** Investigation found the real root cause: the normalizer has the arXiv ID but picks the CI-hostile publisher `openAccessPdf` URL. User decision: **no abstract fallback at all — full text or the paper fails.** Spec rewritten around candidate-source selection + fetch hardening; abstract fallback + `text_source` provenance removed. |
| 2 | QA | When `extraction_failed` climbs, can the operator tell systemic (fixable) from genuine (paywalled)? | **Yes — categorize failures by reason** (`failed_blocked` / `failed_404` / `failed_thin` / `failed_no_pdf_source`) via a `reason` enum on `ExtractionAcquisitionError`; aggregated in the run summary (Design §5, AC-6). |
| 3 | Tech Lead | arXiv-first covers only the arXiv subset; non-arXiv papers still fail. Include Unpaywall now? And preprint-vs-published concern? | User: **published source first, arXiv second** (published is authoritative for a research KG; preprint only when needed — resolves the preprint concern). Candidate order flipped to `[openAccessPdf, arXiv]` + a breadth-first latency guard so a blocked publisher falls through to arXiv fast. Broader OA (Unpaywall) for the non-arXiv tail deferred to **SM-1b**, sized by the categorized metrics. |
