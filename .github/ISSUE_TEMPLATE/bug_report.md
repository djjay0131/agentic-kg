---
name: Bug report
about: A defect in ingestion, extraction, the graph, the API, or deploy
title: "bug: "
labels: bug
---

## What happened

A clear description of the bug.

## Expected

What you expected instead.

## Repro

Steps / command / paper DOI that triggers it.

## Environment

- Where: local / CI / Cloud Run (staging)
- Component: ingestion / extraction / matching / agents / retrieval / deploy
- Relevant logs or `IngestionResult` / `ExtractionFailure` output:

## Provenance / data-integrity impact

Does this affect graph correctness, provenance (DOI + evidence span), or
`taxonomy_hash`? (See the delta's Domain Review Questions.)
