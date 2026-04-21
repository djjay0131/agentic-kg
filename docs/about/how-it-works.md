---
title: How it works
parent: About
nav_order: 3
---

# How it works

<!-- TODO(phase-b): walk a single paper through the full ingestion pipeline with example extracted fields and resulting graph edges. -->

The ingestion pipeline has four phases:

1. **Search & Import** — fan out to OpenAlex / arXiv / Semantic Scholar; store `Paper` + `Author` nodes.
2. **Extraction** — PyMuPDF → section segmenter → LLM extraction of problem statements from "Limitations" / "Future Work" sections.
3. **Integration** — embed the extracted `ProblemMention`, vector-match it against existing `ProblemConcept` nodes, route by confidence (auto-link / agent review / human queue).
4. **Sanity Checks** — five automated integrity checks against the live graph.

Extracted concepts become part of a growing semantic spine that later stages (community detection, RAG retrieval) operate over.
