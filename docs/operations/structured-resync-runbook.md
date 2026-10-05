---
title: Structured re-sync (@snapshot= locators)
parent: Operations
nav_order: 2
---

# Structured re-sync runbook

**Applies to:** any deployment that reads a structured source (a database table,
a warehouse view) through KGIS's `StructuredRecordReader` **on a schedule**.
It does **not** apply to a one-shot import.

---

## The operator check

> **If a source is read on a schedule and its locator ends in `@snapshot=`,
> it is misconfigured.**

```
sqlite://players@snapshot=snap_9f2c1b...   <-- misconfigured for a re-sync
sqlite://players                            <-- correct for a re-sync
```

The locator is visible on every candidate as
`candidate.source_coordinates.locator`, and on every canonical record as
`assertion.provenance.source_ref`. A single Cypher or ledger query for
`@snapshot=` answers the question for a whole deployment.

---

## Symptom

A scheduled re-sync completes successfully. Nothing errors, nothing warns, and
the run report looks normal. But the canonical graph keeps growing: the same
unchanged row acquires another `ACTIVE` record on every run, and every
downstream count — fact totals, per-entity assertion counts, corroboration —
climbs without any source data having changed.

Thirty daily re-syncs of **one unchanged row** produce **thirty** identical
`ACTIVE` records instead of one. That is a measured number, not an estimate:
`packages/core/tests/migration/ingestion/test_structured_locator.py` reproduces
both halves (30 with the default, 1 with the flag off) against the real reader
and the real KGCS planner.

## Why it happens

`StructuredRecordReader` stamps the snapshot version into its locator by
default:

```python
@property
def locator(self) -> str:
    base = self._provider.locator
    if self._include_snapshot:
        return f"{base}@snapshot={self._snap().version}"
    return base
```

From `agentic-kgcs@f68d1d7`, an `assertion_id` is minted from
`record_seed(fact_id, object, valid_period, evidence_refs, provenance)` — and
`provenance.source_ref` **is** that locator. So the snapshot token is part of
the record's identity.

The token is stable when it is *derived* (a digest of the ordered rows:
unchanged data → unchanged token). It is **not** stable when the provider is
given an explicit `snapshot_version` — a database transaction id, an LSN, a
watermark — which is exactly what a production re-sync uses. There the token
moves every run whether or not the data did:

| Run | Locator | Minted `assertion_id` |
|-----|---------|-----------------------|
| 1 | `sqlite://players@snapshot=txn_1` | `as_AAAA…` |
| 2 | `sqlite://players@snapshot=txn_2` | `as_BBBB…` |
| 30 | `sqlite://players@snapshot=txn_30` | `as_ZZZZ…` |

Thirty different ids for one fact. Because each id is genuinely new, the
`assertion_absent` guard that normally refuses a replay does not fire — from the
executor's point of view these are thirty distinct records, and it is right.

## The fix

Set `include_snapshot_in_locator=False`.

```python
from agentic_kg.migration.ingestion import structured_reader

reader = structured_reader(provider)          # flag pinned off
```

`structured_reader()` and `structured_config()` in
`agentic_kg.migration.ingestion.structured` are the only places this repository
builds a structured reader. Both pin the flag off and **raise**
`SnapshotLocatorRefused` if a call site asks for `True`, so the safe value
cannot be undone by a keyword argument — only by editing that module, which is
a reviewable change. An AST check fails the build if any other module under
`migration/ingestion/` constructs a `StructuredRecordReader` or
`StructuredSyncConfig` directly.

Turning it off does **not** merge things that genuinely differ: two different
sources asserting different values for the same subject still mint two records
(`test_two_genuine_sources_still_mint_different_records_when_guarded`).

## What to do about records already written

The flag is a **going-forward** fix. It does not retract duplicates a previous
misconfiguration already committed. Two remedies, both from
`kgcs/records.py`:

1. **Supersede** the duplicates, keeping one record current
   (`RETRACT_ASSERTION` → `SUPERSEDED`). History is preserved and each retired
   record stays queryable at its own epoch.
2. **Re-mint** the surviving record's id from a snapshot-free seed with
   `kgcs.records.backfill_record_id`, so a future re-sync replays onto it
   instead of minting yet another.

Do **not** delete. A deletion loses the record of what was written and by which
run; a supersession does not.

## Current status in this repository

Today's shadow-ingestion path does **not** use the structured reader — it reads
committed importer-output files and builds `SourceCoordinates` by hand. No
locator this repository emits carries `@snapshot=`; that is measured over all
252 candidates of a shadow run by
`test_no_shadow_candidate_carries_a_snapshot_locator`, not assumed.

The prerequisite is enforced now so that it is already in place on the day the
structured arm is pointed at a real source.

## Related

- `packages/core/src/agentic_kg/migration/ingestion/structured.py` — the guard
- `packages/core/tests/migration/ingestion/test_structured_locator.py` — the measurement
- `kgis/structured/reader.py` (upstream) — where the default lives
- `kgcs/records.py` (upstream) — `record_seed`, `backfill_record_id`
