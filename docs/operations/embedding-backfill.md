---
title: Embedding backfill (semantic search recovery)
parent: Operations
nav_order: 91
---

# Embedding backfill

`POST /api/search` is hybrid: it embeds the query and asks Neo4j's vector
indexes (`concept_embedding_idx`, `problem_embedding_idx`). Ingestion degrades
gracefully when `OPENAI_API_KEY` is absent or an embeddings call fails: the node
is written **without** an `embedding`. Those nodes stay invisible to vector
search, and before the keyword fallback was added the API returned an empty
`results: []` for every query.

Search now falls back to a Cypher `CONTAINS` keyword match when vector search
yields nothing, so a populated graph is never reported as empty. The fallback is
a safety net, not a replacement for embeddings — vector search still ranks the
better results once the nodes are embedded.

## Check whether a backfill is needed

The API logs `Semantic search for '…' found 0 results` when it falls through,
and `Keyword search for '…' found N results` when the fallback supplied the
answer. Either line means the graph is missing embeddings for the query.

## Run the backfill

The command is on the core CLI (installed in the API and ingest Job images):

```bash
# Report only: how many nodes still need an embedding.
agentic-kg backfill-embeddings --dry-run

# Generate and store embeddings for up to 500 concepts + 500 legacy problems.
agentic-kg backfill-embeddings --limit 500

# Machine-readable output for a scheduled Job.
agentic-kg backfill-embeddings --limit 500 --json
```

The command:

- reads `ProblemConcept` nodes with no `embedding`, embeds
  `canonical_statement`, and stores the vector;
- reads legacy `Problem` nodes with no `embedding`, embeds `statement`, and
  stores the vector;
- records provider failures per node, continues, and exits non-zero if any node
  failed;
- never deletes or rewrites an existing embedding.

Re-run until `seen` is `0` (or add it as a step after the nightly ingest, where
`OPENAI_API_KEY` is already mounted). The embedding model is
`text-embedding-3-small` (1536 dimensions, matching both vector indexes); do not
change it without recreating the indexes.

## Verify

```bash
curl -fsS -X POST "$UI_URL/api/search" \
  -H 'content-type: application/json' \
  -d '{"query": "graph", "top_k": 5}' | jq '.total'
```

A non-zero total with `match_type: "semantic"` or `"hybrid"` means the vector
path is working again. `"keyword"` means the fallback answered because some
nodes still lack embeddings.
