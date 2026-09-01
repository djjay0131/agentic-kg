# Feature: DOI/Identifier-Targeted Ingestion

**Status:** IMPLEMENTED
**Date:** 2026-07-27 (implemented 2026-07-28)
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** SM-10

## Problem

`agentic-kg ingest` only accepts `--query` (keyword search) + `--limit`. There is
no way to ingest a **specific, known set of papers by identifier** (DOI / Semantic
Scholar paperId / arXiv id). This blocks running the importer against a *curated*
corpus — notably the CS-KG citation-chain **ground-truth validation set** (8
papers, resolved to DOIs in `docs/ground-truth/README.md`), where the whole design
depends on ingesting *exactly those connected papers* so the output can be diffed
against a hand-built answer key and concept accumulation across the chain observed.

Today the only workarounds are fragile:
- **Per-title queries** — search may not return the intended paper as the top hit;
  noisy and not reproducible.
- **A broad domain query** — won't hit the exact set or guarantee the citation
  chain.
- **`extract --file paper.pdf`** — the single-paper path skips the
  search → import → `CITES` flow, so it can't populate intra-set citation edges.

## Goal

Ingest an explicit list of papers by identifier through the normal `ingest_papers`
pipeline (import → extract → integrate → `CITES`), so a curated corpus reproducibly
lands in the graph.

## Sketch

- `agentic-kg ingest --dois 10.x/y 10.a/b ...` and/or `--dois-file papers.txt`,
  **mutually exclusive** with `--query`.
- Resolve each identifier via the aggregator's existing S2/OpenAlex "get paper by
  DOI" path (Semantic Scholar `paper/DOI:<doi>` is already the source the importer
  uses for `CITES`), normalize, then run the standard per-paper loop unchanged.
- Existing flags (`--force-rewrite`, `--no-populate-citations`,
  `--no-extract-entities`, etc.) continue to apply.

## Acceptance Criteria

- **AC-1:** `--dois` (and/or `--dois-file`) ingests exactly the listed papers;
  unresolvable identifiers are **reported** in `search_errors` (`unresolved:<doi>`),
  not silently skipped. ✅
- **AC-2:** `CITES` edges among the ingested set populate from each paper's
  reference list (the intra-set chain is reproducible) — the DOI path reuses the
  standard per-paper loop, so `populate_citations` behaves identically to search.✅
- **AC-3:** `--query` and `--dois`/`--dois-file` are mutually exclusive, with a
  clear error when both (or neither) are supplied. ✅
- **AC-4 (deferred):** Cloud Run Job env equivalent (`INGEST_DOIS`) mirrors the CLI
  flag. Not needed for the ground-truth run (driven from the CLI); file if the Job
  ever needs DOI-targeting.

## Implementation (2026-07-28)

- `ingestion.py`: `ingest_papers` gains `dois: list[str] | None`; `query` is now
  optional. When `dois` is set, each is resolved via `aggregator.get_paper_by_doi`
  (catching `NotFoundError`/transient source errors per DOI → `unresolved:<doi>` in
  `search_errors`), assembled into a `SearchResult`, then the **unchanged** import
  → extract → integrate → CITES loop runs. `query` XOR `dois` enforced (fail-loud).
- `cli.py`: `ingest --dois <DOI…>` + `--dois-file <path>` (blank/`#` lines ignored),
  mutually exclusive with `--query`, threaded to `ingest_papers`.
- Tests: `TestDoiTargetedIngestion` (fetch-by-DOI-not-search, unresolved recorded,
  per-DOI source errors surfaced, neither-input fails loud) + CLI wiring verified.
  Full suite 2107 pass; src ruff-clean.

## Notes / Motivation

Surfaced 2026-07-27 while aligning the first staging validation run with the
ground-truth corpus. Directly unblocks `docs/ground-truth/` (owner: Victoria) and
any future "run the importer on a fixed answer-key set" evaluation. Independent of
the extraction-pipeline fixes (SM-6/7/8/8b) that made entities flow.

**ID note:** filed as SM-10 — SM-7 (extraction-rate-limit-resilience), SM-8
(b3-linker-mention-statement-fix), SM-8b (taxonomy seed), and SM-9 (swallowed
programming-error follow-up) are already claimed by existing specs/notes even
though the BACKLOG memory-sync has not yet added rows for them.
