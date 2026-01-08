# Data Acquisition Module

Unified paper acquisition from multiple sources for the Agentic Knowledge Graph.

## Overview

This module provides:
- **Semantic Scholar Client**: Paper metadata, citations, references, SPECTER2 embeddings
- **arXiv Client**: Open access papers and preprints with PDF downloads
- **OpenAlex Client**: Open access discovery and author/institution metadata
- **Unified Acquisition Layer**: Single interface for all sources
- **Caching**: PDF and metadata caching with TTL and LRU eviction
- **Rate Limiting**: Token bucket rate limiters for API compliance
- **KG Integration**: Sync acquired papers to Neo4j Knowledge Graph

## Quick Start

```python
from agentic_kg.data_acquisition import PaperAcquisitionLayer

# Create acquisition layer
acquisition = PaperAcquisitionLayer()

# Get paper by DOI
paper = acquisition.get_paper_metadata("10.1038/nature12373")

# Get paper by arXiv ID
paper = acquisition.get_paper_metadata("2301.12345")

# Download PDF
result = acquisition.get_pdf("2301.12345", Path("./papers"))

# Search across sources
results = acquisition.search("attention mechanism transformer", limit=10)
```

## API Clients

### Semantic Scholar Client

```python
from agentic_kg.data_acquisition import SemanticScholarClient

client = SemanticScholarClient()

# Search papers
papers = client.search_papers("transformer attention", limit=10)

# Get by DOI
paper = client.get_paper_by_doi("10.48550/arXiv.1706.03762")

# Get by arXiv ID
paper = client.get_paper_by_arxiv_id("1706.03762")

# Get citations and references
citations = client.get_citations(paper.paper_id, limit=100)
references = client.get_references(paper.paper_id, limit=100)

# Get SPECTER2 embedding (768 dims)
embedding = client.get_embedding(paper.paper_id)
```

### arXiv Client

```python
from agentic_kg.data_acquisition import ArxivClient
from pathlib import Path

client = ArxivClient()

# Get metadata
paper = client.get_metadata("2301.12345")

# Search arXiv
papers = client.search("cat:cs.AI AND all:transformer", max_results=10)

# Download PDF
pdf_path = client.download_pdf("2301.12345", Path("./papers"))

# Get PDF URL directly
url = client.get_pdf_url("2301.12345")
```

### OpenAlex Client

```python
from agentic_kg.data_acquisition import OpenAlexClient

client = OpenAlexClient()

# Get work by DOI
paper = client.get_work_by_doi("10.1038/nature12373")

# Search with filters
papers = client.search_works(
    "machine learning",
    filters={"publication_year": 2023, "is_oa": True},
    per_page=25
)

# Get author's papers
papers = client.get_works_by_author("A5023888391")
```

## Configuration

### Environment Variables

```bash
# Semantic Scholar
SEMANTIC_SCHOLAR_API_KEY=your_api_key  # Optional, enables higher rate limits

# OpenAlex (email for polite pool - higher rate limits)
OPENALEX_EMAIL=your@email.com

# Cache
PAPER_CACHE_DIR=/path/to/cache
PAPER_CACHE_MAX_SIZE_MB=1000
```

### Configuration Object

```python
from agentic_kg.config import get_config

config = get_config()

# Semantic Scholar settings
config.data_acquisition.semantic_scholar.api_key
config.data_acquisition.semantic_scholar.rate_limit  # requests/second

# arXiv settings
config.data_acquisition.arxiv.rate_limit  # Default: 3.0 req/sec

# OpenAlex settings
config.data_acquisition.openalex.email
config.data_acquisition.openalex.rate_limit  # Default: 10.0 req/sec

# Cache settings
config.data_acquisition.cache.cache_dir
config.data_acquisition.cache.max_size_mb
```

## Rate Limiting

Rate limits are automatically enforced:

| Source | Unauthenticated | Authenticated |
|--------|-----------------|---------------|
| Semantic Scholar | 1 req/sec | 10 req/sec |
| arXiv | 3 req/sec | N/A |
| OpenAlex | 10 req/sec | 100 req/sec (polite pool) |

Custom rate limiter usage:

```python
from agentic_kg.data_acquisition import TokenBucketRateLimiter

limiter = TokenBucketRateLimiter(requests_per_second=5.0, burst_size=10)

# Blocking acquire
limiter.acquire()  # Waits until token available

# Non-blocking
if limiter.try_acquire():
    make_request()
```

## Caching

### PDF Cache

```python
from agentic_kg.data_acquisition import get_paper_cache

cache = get_paper_cache()

# Store PDF
path = cache.store_pdf("10.1038/nature12373", pdf_bytes, SourceType.OPENALEX)

# Retrieve
if cache.has_pdf("10.1038/nature12373"):
    content = cache.get_pdf("10.1038/nature12373")

# Statistics
stats = cache.get_stats()
print(f"Hit rate: {stats['hit_rate']:.1%}")
```

### Metadata Cache

```python
from agentic_kg.data_acquisition import get_metadata_cache

cache = get_metadata_cache()

# Cache paper
cache.set_paper("10.1038/nature12373", paper_metadata)

# Retrieve (returns None if expired)
paper = cache.get_paper("10.1038/nature12373")

# Force fresh fetch
paper = cache.get_paper("10.1038/nature12373", bypass=True)  # Always None
```

## Knowledge Graph Integration

```python
from agentic_kg.data_acquisition import (
    PaperAcquisitionLayer,
    sync_paper_to_kg,
    sync_papers_batch,
)

# Acquire paper
acquisition = PaperAcquisitionLayer()
paper = acquisition.get_paper_metadata("10.1038/nature12373")

# Sync to Neo4j
result = sync_paper_to_kg(paper)
print(f"Created: {result.papers_created}, Updated: {result.papers_updated}")

# Batch sync
results = acquisition.search("transformers", limit=50)
batch_result = sync_papers_batch(results)
```

## CLI Tool

```bash
# Fetch by DOI
python scripts/fetch_paper.py 10.1038/nature12373

# Fetch by arXiv ID
python scripts/fetch_paper.py 2301.12345

# Search for papers
python scripts/fetch_paper.py --search "attention mechanism"

# Download PDF
python scripts/fetch_paper.py 2301.12345 --download --output ./papers

# JSON output
python scripts/fetch_paper.py 2301.12345 --json

# Sync to Knowledge Graph
python scripts/fetch_paper.py 2301.12345 --sync
```

## Identifier Types

The acquisition layer auto-detects identifier types:

| Type | Format Examples |
|------|-----------------|
| DOI | `10.1038/nature12373`, `doi:10.1038/...` |
| arXiv | `2301.12345`, `arxiv:2301.12345`, `cs.AI/0501001` |
| S2 ID | `649def34f8be52c8b66281af98ae884c09aef38b` |
| OpenAlex | `W2741809807` |
| URL | `https://arxiv.org/abs/2301.12345` |

## Data Models

### PaperMetadata

```python
class PaperMetadata(BaseModel):
    paper_id: str
    doi: Optional[str]
    arxiv_id: Optional[str]
    s2_id: Optional[str]
    openalex_id: Optional[str]
    title: str
    abstract: Optional[str]
    authors: list[AuthorRef]
    year: Optional[int]
    venue: Optional[str]
    pdf_url: Optional[str]
    is_open_access: bool
    source: SourceType
    citation_count: Optional[int]
    embedding: Optional[list[float]]  # SPECTER2 768-dim
    fields_of_study: list[str]
```

### AuthorRef

```python
class AuthorRef(BaseModel):
    name: str
    author_id: Optional[str]  # Source-specific ID
    orcid: Optional[str]
    affiliations: list[str]
```

## Error Handling

```python
from agentic_kg.data_acquisition import (
    NotFoundError,
    RateLimitError,
    SemanticScholarError,
    ArxivNotFoundError,
    OpenAlexNotFoundError,
)

try:
    paper = client.get_paper_by_doi("invalid_doi")
except NotFoundError:
    print("Paper not found")
except RateLimitError as e:
    print(f"Rate limited, retry after {e.retry_after}s")
```

## Architecture

```
data_acquisition/
├── __init__.py           # Public exports
├── models.py             # PaperMetadata, AuthorRef, Citation
├── semantic_scholar.py   # Semantic Scholar API client
├── arxiv.py              # arXiv API client
├── openalex.py           # OpenAlex API client
├── acquisition.py        # Unified acquisition layer
├── cache.py              # PDF caching with SQLite
├── metadata_cache.py     # TTL-based metadata cache
├── ratelimit.py          # Token bucket rate limiters
├── kg_sync.py            # Knowledge Graph integration
└── README.md             # This file
```

## Dependencies

- `httpx`: Async HTTP client
- `pydantic`: Data validation
- `neo4j`: Knowledge Graph (optional, for KG sync)
