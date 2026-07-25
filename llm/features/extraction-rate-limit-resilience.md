# Feature: Extraction Rate-Limit Resilience (SM-6)

**Status:** VERIFIED
**Date:** 2026-07-25
**Author:** Feature Architect (AI-assisted)

## Problem

Entity extraction runs five extractors (problem, topic, concept, model, method) concurrently
per paper via `asyncio.gather` (`extraction/pipeline.py:extract_all_entities`). Each extractor
sends the **full paper text** to OpenAI. For a full-text paper that is 50–100k tokens, five
simultaneous calls burst well past the account's tokens-per-minute (TPM) ceiling —
`gpt-4-turbo` is capped at **30,000 TPM**. OpenAI responds `429 rate_limit_exceeded`, and the
current `@retry` on `LLMClient.extract` cannot help: the workload is *structurally* over budget
(retrying the same five oversized calls hits the same wall), and the retry uses a blind
exponential backoff that ignores the server's `Retry-After` hint.

Concrete evidence from the last production smoke run (post-SM-1): 6 papers ingested, 2 acquired
full text (`pdf_ok=2`), but **both full-text papers got 429'd → 0 entities extracted**. The
smoke-ingest CI job (`Ingest + Assert`) stays red because entities never flow, even though
acquisition is now healthy.

The root cause is two-fold:
1. **No proactive throttle.** Nothing paces the five concurrent calls against a TPM budget.
2. **Wrong model for the workload at the default tier.** `gpt-4-turbo` @ 30k TPM cannot absorb
   a single full paper × 5 extractors, let alone concurrent papers.

### What the throttle does and does not fix

Be explicit about the arithmetic so nobody expects the default config to suddenly produce
entities. An 80k-token paper × 5 extractors is ≈ 250k+ estimated tokens. At `gpt-4-turbo`'s
30k TPM the bucket needs ~500s (8+ min) of accrual per paper before the calls can even fire —
so **the throttle alone never makes `gpt-4-turbo` viable for full papers.**

- **Throttle = correctness safety-net.** It ensures we never fire a call we know can't fit,
  eliminating 429 storms and wasted API attempts, and — critically — it keeps *any* tier under
  its ceiling once concurrent papers are in flight. It is NOT a throughput solution at the
  default tier.
- **Model/tier flip = the throughput lever.** Moving to a higher-TPM model (e.g. `gpt-4o` at a
  450k-TPM tier) is what actually lets full-paper extraction complete. The throttle then keeps
  that larger budget from being blown by concurrency.

Reducing extractor input (chunking/summarizing full papers) is deliberately out of scope — see
Non-Goals; that is a separate, larger feature.

## Goals

- Add a **proactive TPM throttle**: a shared token-bucket sized to a configurable budget
  (`OPENAI_TPM`, default 30000) that every extraction LLM call reserves against *before* firing,
  so five concurrent extractors serialize under the budget instead of all 429-ing.
- **Honor `Retry-After`** on the reactive path: when a 429 still occurs, wait the duration the
  server asks for instead of a blind exponential backoff.
- Make the **extraction model configurable** via `OPENAI_EXTRACTION_MODEL` (default unchanged:
  `gpt-4-turbo`), so an operator can pull the real throughput lever (a higher-TPM tier/model)
  without a code change.
- **Flip CI's smoke-ingest** to a higher-TPM model via the env knob so the `Ingest + Assert`
  job proves entities > 0 end-to-end. Production default stays `gpt-4-turbo`.
- Ship a **documented operator runbook** for the same lever in production.

## Non-Goals

- **Not** reducing the input sent to extractors (no chunking/summarizing the paper text). Q1
  chose throttle + model/tier lever, keeping full-paper input to all five extractors unchanged.
- **Not** changing the production default model. Q2: the default stays `gpt-4-turbo`; the
  operator flips the env knob deliberately. Smoke stays red in production until they do.
- **Not** an exact tokenizer. Q3 chose a heuristic estimate (`len(text)/4 + max_tokens`); no
  `tiktoken` dependency.
- **Not** per-provider throttling for Anthropic in this feature (OpenAI is where the ceiling
  bites today); the design leaves room but only wires the OpenAI path.

## User Stories

- As an **operator running a large ingest**, I want extraction to pace itself under my TPM
  budget, so a batch completes without every full-text paper failing on 429.
- As an **operator**, I want to raise throughput by setting `OPENAI_EXTRACTION_MODEL=gpt-4o`
  (higher TPM tier) without editing code, so I can trade cost/latency for headroom on demand.
- As a **maintainer watching CI**, I want the smoke-ingest job to prove entities flow, so a
  regression in the extraction path fails the build instead of passing silently.

## Design Approach

Three cooperating pieces, all in `extraction/llm_client.py` plus one CI workflow edit.

### 1. Shared TPM token bucket (proactive)

Reuse the existing `TokenBucketRateLimiter` from `data_acquisition/rate_limiter.py`
(refill-rate + capacity, `acquire(tokens)` blocks until enough tokens have accrued). Create
**one process-global instance** for the OpenAI extraction path:

- `rate = OPENAI_TPM / 60.0` tokens/sec (default 30000/60 = 500 t/s)
- `capacity = OPENAI_TPM` (allow up to a one-minute burst)

Global (not per-paper) so that concurrent extractors *across* papers also serialize under the
one real account ceiling. Before each `create_with_completion`, estimate the call's token cost
and `await limiter.acquire(estimate)`. Five concurrent extractors each block here; the bucket
hands out budget over time, smoothing the burst below TPM.

### 2. Heuristic token estimate (Q3a)

```
estimate = (len(prompt) + len(system_prompt)) // 4   # ~4 chars/token for input
         + config.max_tokens                          # reserve the completion budget
```

Deliberately conservative-ish (reserves the full `max_tokens` even if the model returns less);
over-reserving throttles slightly harder, which is the safe direction for a rate limit.

### 3. Honor Retry-After (reactive safety net)

Keep the existing tenacity retry on `extract`, but:
- `LLMRateLimitError` carries a `retry_after: float | None`, parsed from the OpenAI error
  (the SDK surfaces a `Retry-After` header and/or a "try again in 1.798s" message).
- Replace the blind `wait_exponential` with a wait callable that returns `retry_after` when the
  raised error carries one, else falls back to exponential.

Proactive throttle keeps us under budget in the common case; the reactive path covers the tail
where our heuristic under-estimated actual usage.

### 4. Configurable model + CI flip

- `get_openai_client()` reads `OPENAI_EXTRACTION_MODEL` (default `gpt-4-turbo`).
- `.github/workflows/smoke-ingest.yml` sets `OPENAI_EXTRACTION_MODEL: gpt-4o` and a matching
  `OPENAI_TPM` for that tier, so CI proves entities > 0. Production default is untouched.

## Sample Implementation

```python
# extraction/llm_client.py
import os
from agentic_kg.data_acquisition.rate_limiter import TokenBucketRateLimiter

_TPM = int(os.getenv("OPENAI_TPM", "30000"))
# One shared bucket for the whole OpenAI extraction path (all extractors, all papers).
_tpm_limiter = TokenBucketRateLimiter(source="openai-tpm", rate=_TPM / 60.0, capacity=_TPM)


def _estimate_tokens(prompt: str, system: str | None, max_out: int) -> int:
    return (len(prompt) + len(system or "")) // 4 + max_out


class LLMRateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _rate_limit_wait(retry_state):
    exc = retry_state.outcome.exception()
    if isinstance(exc, LLMRateLimitError) and exc.retry_after:
        return exc.retry_after                     # obey the server's hint
    return wait_exponential(multiplier=1, min=1, max=60)(retry_state)


class OpenAIClient(BaseLLMClient):
    @retry(retry=retry_if_exception_type(LLMRateLimitError),
           stop=stop_after_attempt(5), wait=_rate_limit_wait)
    async def extract(self, prompt, response_model, system_prompt=None):
        est = _estimate_tokens(prompt, system_prompt, self.config.max_tokens)
        eta = _tpm_limiter.wait_estimate(est)      # cheap peek: seconds until est tokens free
        if eta > 5.0:                              # legible stall, not a silent hang
            logger.info(
                "TPM throttle: ~%.0fs wait for ~%d tokens (budget %d TPM). "
                "Raise throughput with OPENAI_EXTRACTION_MODEL=gpt-4o + matching OPENAI_TPM.",
                eta, est, _TPM,
            )
        waited = await _tpm_limiter.acquire(est)   # proactive: reserve before firing
        if waited:
            logger.info("TPM throttle: waited %.1fs for ~%d tokens", waited, est)
        try:
            resp, completion = await client.chat.completions.create_with_completion(...)
            return LLMResponse(...)
        except Exception as e:
            if "rate" in str(e).lower() and "limit" in str(e).lower():
                raise LLMRateLimitError(f"OpenAI rate limited: {e}",
                                        retry_after=_parse_retry_after(e)) from e
            raise


def get_openai_client(model: str | None = None) -> OpenAIClient:
    model = model or os.getenv("OPENAI_EXTRACTION_MODEL", "gpt-4-turbo")
    return OpenAIClient(LLMConfig(provider=LLMProvider.OPENAI, model=model))
```

```yaml
# .github/workflows/smoke-ingest.yml
env:
  OPENAI_EXTRACTION_MODEL: gpt-4o     # higher-TPM tier so smoke produces entities
  OPENAI_TPM: "450000"                # match the tier; throttle isn't the bottleneck here
```

## Edge Cases & Error Handling

### Retry-After absent or unparseable on a 429
- **Scenario**: OpenAI returns a 429 but no delay can be parsed (absent, or a format the parser
  no longer recognizes).
- **Behavior**: `retry_after` is `None`; `_rate_limit_wait` falls back to exponential backoff —
  AND a **WARN is logged** ("429 with unparseable Retry-After; falling back to exponential — the
  parser may be stale"). A silent revert to blind backoff is the exact failure this feature
  exists to fix, so a parse-miss must be visible in logs, not hidden behind a green test.
- **Test**: parse helper returns `None` for a message with no delay; a caught 429 with no
  parseable delay emits the WARN; the wait callable returns an exponential value.

### Estimate exceeds bucket capacity
- **Scenario**: a single call's estimate is larger than `capacity` (e.g. a huge paper vs a low
  `OPENAI_TPM`).
- **Behavior**: `acquire` must still make progress (not deadlock). The bucket refills and grants
  the request once enough tokens accrue over time; capacity is a burst ceiling, not a hard cap
  on a single acquire.
- **Test**: `wait_estimate(tokens > capacity)` returns ~ `tokens/rate` (finite, non-zero);
  `acquire` completes, with the requested sleep duration captured via a monkeypatched sleep —
  no real waiting.

### Env knobs unset
- **Scenario**: neither `OPENAI_TPM` nor `OPENAI_EXTRACTION_MODEL` is set.
- **Behavior**: defaults `30000` and `gpt-4-turbo` — identical to today's production behavior.
- **Test**: with a clean env, model is `gpt-4-turbo` and the bucket rate is `500.0`.

### Malformed OPENAI_TPM
- **Scenario**: `OPENAI_TPM=banana`.
- **Behavior**: fail fast with a clear message at client construction (don't silently fall back
  to a default that hides a misconfiguration).
- **Test**: constructing the client with a non-integer TPM raises a `ValueError` naming the var.

### Concurrent acquire fairness
- **Scenario**: five extractors call `acquire` at once.
- **Behavior**: all five eventually proceed; aggregate consumption stays within `rate` over
  time. Ordering fairness is best-effort (not a hard guarantee).
- **Test**: gather five `acquire` calls whose estimates sum to > capacity; assert the summed
  sleep durations captured from a monkeypatched sleep ≈ (sum − capacity)/rate and all complete —
  no real elapsed-time measurement.

## Acceptance Criteria

### AC-1: Proactive throttle reserves estimated tokens before the call
- **Given** a shared TPM bucket and an extraction call with an estimated token cost
- **When** `extract` runs
- **Then** it calls `acquire(estimate)` before `create_with_completion`, and `estimate` equals
  `(len(prompt)+len(system))//4 + max_tokens`.

### AC-2: Throttle blocks when the TPM budget is exceeded
- **Given** a bucket whose tokens are depleted below the next call's estimate
- **When** a call requests more tokens than are currently available
- **Then** `acquire` waits for the deficit before returning — asserted via `wait_estimate`
  (pure math: `deficit/rate`) and by capturing the duration passed to a monkeypatched sleep,
  **not** by measuring real elapsed time.

### AC-3: Retry-After is parsed and honored
- **Given** an OpenAI 429 whose error carries a delay ("try again in 1.8s" or a header)
- **When** the retry wait is computed
- **Then** the wait equals the parsed `retry_after`; when no delay can be parsed, it falls back
  to exponential backoff **and logs a WARN** so a stale parser (OpenAI changed the error surface)
  is visible instead of silently reverting to the pre-feature behavior.

### AC-4: Extraction model is env-configurable, default unchanged
- **Given** `OPENAI_EXTRACTION_MODEL` is set (or unset)
- **When** `get_openai_client()` builds the client
- **Then** the model is the env value, or `gpt-4-turbo` when unset.

### AC-5: TPM budget is env-configurable, default 30000
- **Given** `OPENAI_TPM` is set (or unset)
- **When** the limiter is constructed
- **Then** its rate is `OPENAI_TPM/60` (default 500.0); a non-integer value raises `ValueError`.

### AC-6: CI smoke-ingest produces entities
- **Given** the smoke-ingest workflow sets a higher-TPM extraction model
- **When** `Ingest + Assert` runs against the full-text sample
- **Then** entities extracted > 0 and the job passes; the production default remains
  `gpt-4-turbo` (workflow-only override).

### AC-7: A long throttle wait is legible, not a silent hang
- **Given** a predicted throttle wait exceeding 5 seconds for the next call
- **When** `extract` is about to block on `acquire`
- **Then** it emits one up-front INFO log naming the ETA, the estimated tokens, the active TPM
  budget, and the model-flip remedy — *before* blocking; sub-threshold waits stay silent.
- **Test**: `wait_estimate` > 5 → the log is emitted before `acquire`; `wait_estimate` ≤ 5 →
  no up-front log.

### AC-8: Operator runbook documents the lever
- **Given** the feature is shipped
- **When** an operator needs more extraction throughput
- **Then** a runbook (in the memory bank / docs) states: set `OPENAI_EXTRACTION_MODEL=gpt-4o`
  (and matching `OPENAI_TPM`), run the ingest, verify entities > 0.

## Technical Notes

- **Affected components**: `extraction/llm_client.py` (throttle, Retry-After wait, env model),
  `.github/workflows/smoke-ingest.yml` (CI model flip), operator runbook doc.
- **Reused stars**: `data_acquisition/rate_limiter.py:TokenBucketRateLimiter.acquire`;
  the existing tenacity `@retry` on `LLMClient.extract`; `get_openai_client()`.
- **Small addition to the limiter**: a `wait_estimate(tokens) -> float` peek (seconds until
  `tokens` are available, 0 if immediately) so `extract` can log an ETA before blocking. Pure
  read of current token count / refill rate; no state mutation.
- **No new dependencies** (heuristic estimate, no `tiktoken`).
- **Config surface**: `OPENAI_TPM` (int, default 30000), `OPENAI_EXTRACTION_MODEL`
  (str, default `gpt-4-turbo`).
- **Concurrency**: one module-global bucket; `acquire` is async and safe under `asyncio.gather`.
- **HARD REQUIREMENT — no real-time sleeping in tests.** Throttle tests MUST assert on
  `wait_estimate` (pure math) and on the duration passed to a monkeypatched/mocked sleep. No test
  may `sleep` real seconds or assert on measured wall-clock elapsed time, and no "≈ real elapsed"
  tolerance is permitted. This keeps the suite fast and non-flaky on loaded CI runners — the very
  reliability this feature is meant to deliver.

## Dependencies

- SM-1 (content-acquisition-resilience, VERIFIED/merged) — full-text papers must reach the
  extractor for this throttle to matter.
- `TokenBucketRateLimiter` (data-acquisition, existing).

## Open Questions

- **Retry-After parsing surface**: prefer the OpenAI SDK's typed `RateLimitError.response`
  headers when available; fall back to regex on the message. Confirm the exact SDK shape during
  implementation.
- **Per-model TPM defaults**: should `OPENAI_TPM` auto-derive from a known model→TPM table when
  only the model is set? Deferred; explicit `OPENAI_TPM` for now (operator sets both).
- **Anthropic path**: same throttle likely applies to `get_anthropic_client`; out of scope here,
  candidate follow-up if Anthropic extraction hits limits.
