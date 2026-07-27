# Feature: DOI/Identifier-Targeted Ingestion

**Status:** NEEDS SPEC
**Date:** 2026-07-27
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

## Acceptance Criteria (draft)

- **AC-1:** `--dois` (and/or `--dois-file`) ingests exactly the listed papers;
  unresolvable identifiers are **reported**, not silently skipped.
- **AC-2:** `CITES` edges among the ingested set populate from each paper's
  reference list (the intra-set chain is reproducible).
- **AC-3:** `--query` and `--dois` are mutually exclusive, with a clear error when
  both (or neither) are supplied.
- **AC-4:** Cloud Run Job env equivalent (e.g. `INGEST_DOIS`) mirrors the CLI flag.

## Notes / Motivation

Surfaced 2026-07-27 while aligning the first staging validation run with the
ground-truth corpus. Directly unblocks `docs/ground-truth/` (owner: Victoria) and
any future "run the importer on a fixed answer-key set" evaluation. Independent of
the extraction-pipeline fixes (SM-6/7/8/8b) that made entities flow.

**ID note:** filed as SM-10 — SM-7 (extraction-rate-limit-resilience), SM-8
(b3-linker-mention-statement-fix), SM-8b (taxonomy seed), and SM-9 (swallowed
programming-error follow-up) are already claimed by existing specs/notes even
though the BACKLOG memory-sync has not yet added rows for them.
