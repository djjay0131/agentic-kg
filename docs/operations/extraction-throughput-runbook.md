---
title: Extraction throughput (OpenAI rate limits)
parent: Operations
nav_order: 1
---

# Extraction throughput runbook (SM-6)

**Symptom:** an ingest completes with full-text papers acquired (`pdf_ok > 0`)
but **zero entities extracted**, and the logs show OpenAI `429
rate_limit_exceeded` on the extractor calls.

**Why it happens:** each paper runs five extractors (problem, topic, concept,
model, method) concurrently, and each sends the *full paper text* to OpenAI. A
50–100k-token paper × 5 calls bursts past the account's tokens-per-minute (TPM)
ceiling. The default model, `gpt-4-turbo`, is capped at **30,000 TPM** — far too
low to absorb even a single full paper across five extractors.

## The two levers

| Knob | Env var | Default | What it does |
|------|---------|---------|--------------|
| **Model / tier** | `OPENAI_EXTRACTION_MODEL` | `gpt-4-turbo` | The real throughput lever. A higher-TPM model (e.g. `gpt-4o`) is what actually lets full-paper extraction complete. |
| **TPM budget** | `OPENAI_TPM` | `30000` | The proactive throttle's budget. Set it to match your account's ceiling for the chosen model so the throttle paces calls correctly. |

The **throttle is a correctness safety-net**, not a throughput fix: it stops
concurrent calls from all 429-ing, but at `gpt-4-turbo`'s 30k TPM it can only do
so by waiting minutes per paper. Raising the model/tier is what makes entities
actually flow; the throttle then keeps that larger budget from being blown by
concurrency.

## Fix: raise the throughput

Set both env vars — model **and** a matching TPM budget for that tier — then
re-run the ingest and confirm entities land.

### Cloud Run Job (staging / production)

```bash
gcloud run jobs update agentic-kg-ingest-staging \
  --project vt-gcp-00042 --region us-central1 \
  --update-env-vars OPENAI_EXTRACTION_MODEL=gpt-4o,OPENAI_TPM=450000

gcloud run jobs execute agentic-kg-ingest-staging \
  --project vt-gcp-00042 --region us-central1
```

### Local / smoke

```bash
export OPENAI_EXTRACTION_MODEL=gpt-4o
export OPENAI_TPM=450000
make smoke-local        # or: agentic-kg ingest --query "..." --limit 3 --json
```

## Verify

The run should now report entities > 0. Check the ingest summary counters
(`topics_linked`, `concepts_v2_linked`, `models_linked`, `methods_linked`) or
query Neo4j directly:

```cypher
MATCH (p:Paper)-[r:BELONGS_TO|DISCUSSES|USES_MODEL|APPLIES_METHOD]->()
RETURN type(r) AS edge, count(*) AS n ORDER BY n DESC
```

If a run is legitimately throttling, you will see an up-front log line before it
blocks — e.g. `TPM throttle: ~480s wait for ~250000 tokens (budget 30000 TPM)` —
which both confirms the stall is the throttle (not a hang) and names the remedy.

## Notes

- The **production default is unchanged** (`gpt-4-turbo`); the model flip is a
  deliberate operator action per run, not a code change.
- CI's smoke-ingest workflow already sets `OPENAI_EXTRACTION_MODEL=gpt-4o` +
  `OPENAI_TPM=450000` so the `Ingest + Assert` job proves entities flow.
- Match `OPENAI_TPM` to your account's actual tier for the chosen model. Setting
  it higher than your real ceiling defeats the throttle and reintroduces 429s;
  setting it lower just paces more conservatively (safe, but slower).
