# Active Context

**Last Updated:** 2026-01-26

## Current Work Phase

**Phase 3: Information Extraction Pipeline - PLANNING**

Sprint 03 (Information Extraction Pipeline) planning is complete. Requirements and sprint documents created, ready to begin implementation.

## Immediate Next Steps

**Session Status (2026-01-26):**
- On branch: `master` (Sprint 02 merged)
- Sprint 03 planning complete
- Ready to create implementation branch

**Sprint 03 Planned (Ready for Implementation):**
- [ ] Task 1: PDF Text Extraction Module (PyMuPDF)
- [ ] Task 2: Section Segmentation (Heuristic + LLM)
- [ ] Task 3: LLM Client Wrapper (OpenAI/Anthropic)
- [ ] Task 4: Prompt Templates (versioned)
- [ ] Task 5: Extraction Schema Models
- [ ] Task 6: Problem Extractor Core
- [ ] Task 7: Relationship Extractor
- [ ] Task 8: Paper Processing Pipeline
- [ ] Task 9: Knowledge Graph Integration
- [ ] Task 10: Batch Processing
- [ ] Task 11: CLI Commands
- [ ] Task 12: Test Fixtures and Conftest
- [ ] Task 13: Integration Tests (deferred)

**Sprint 02 Completed (Merged to Master):**
- [x] Data acquisition module structure (`data_acquisition/`)
- [x] Configuration module: `config.py`
- [x] Base API client: `base.py`
- [x] Exceptions: `exceptions.py`
- [x] Rate limiting: `rate_limiter.py` (Token bucket with registry)
- [x] Resilience: `resilience.py` (Circuit breaker, retry with backoff)
- [x] Caching: `cache.py` (TTL cache with cachetools)
- [x] Semantic Scholar client: `semantic_scholar.py`
- [x] arXiv client: `arxiv.py` (with Atom feed parsing)
- [x] OpenAlex client: `openalex.py` (with abstract reconstruction)
- [x] Paper normalization: `normalizer.py` (unified schema)
- [x] Multi-source aggregator: `aggregator.py`
- [x] KG importer: `importer.py`
- [x] CLI script: `scripts/import_papers.py`
- [x] Unit tests: 11 test files

**Sprint 01 Completed (Merged to Master):**
- [x] Pydantic models: `knowledge_graph/models.py`
- [x] Neo4j Docker setup: `docker/docker-compose.yml`
- [x] Repository layer: `knowledge_graph/repository.py`
- [x] Schema initialization: `knowledge_graph/schema.py`
- [x] Embedding integration: `knowledge_graph/embeddings.py`
- [x] Hybrid search: `knowledge_graph/search.py`
- [x] Relation operations: `knowledge_graph/relations.py`
- [x] 221 tests

**Priority Tasks:**

1. **Create Sprint 03 Branch** (Immediate)
   - Create branch `claude/sprint-03-extraction-pipeline`
   - Begin Task 1: PDF Text Extraction Module

2. **Implement Extraction Core** (Sprint 03 Focus)
   - PDF extraction with PyMuPDF
   - Section segmentation
   - LLM-based structured extraction

## Recent Decisions

### Decision 9: LLM-Based Structured Extraction (ADR-013)
- **Date:** 2026-01-26
- **Decision:** Use LLM-based extraction with `instructor` library for structured output
- **Rationale:** High quality extraction, schema validation, better than rule-based for diverse papers
- **Impact:** Enables automated problem extraction at scale

### Decision 8: Data Acquisition Architecture (ADR-012)
- **Date:** 2026-01-25
- **Decision:** Implement token bucket rate limiting with per-source configuration
- **Rationale:** Academic APIs have different rate limits; centralized registry simplifies management
- **Impact:** Robust API usage across Semantic Scholar, arXiv, and OpenAlex

### Decision 7: Deferred Items to Backlog
- **Date:** 2026-01-07
- **Decision:** Document Sprint 01 deferred items in `construction/backlog/sprint-01-deferred.md`
- **Rationale:** Keep sprint focused on core deliverables, track non-blocking items separately
- **Impact:** Clear separation between done and deferred work

*Note: Decisions 1-6 archived*

## Key Patterns and Preferences

### Documentation Patterns
- Use markdown for all documentation
- Include ASCII diagrams for architecture
- Update activeContext.md after every significant change
- Reference ADR numbers when implementing decisions
- Use `phases.md` as coordination hub between memory-bank and construction

### Development Patterns
- Python 3.12+ (Denario requirement)
- Use Denario's existing agent frameworks (AG2, LangGraph)
- Docker containers for deployment
- Environment variables for secrets (never commit keys)
- Design-first workflow: complete design docs before implementation

### Extraction Patterns (New for Phase 3)
- PyMuPDF for PDF text extraction
- Heuristic-first, LLM-fallback for section detection
- Instructor library for structured LLM output
- Multi-pass extraction: identify → extract → validate
- Confidence scoring: LLM self-assessment + schema completeness

### Architecture Patterns
- Three-layer architecture (Knowledge, Extraction, Agentic)
- Problems as first-class entities
- Hybrid symbolic-semantic retrieval
- Human-in-the-loop governance

## Important Learnings

### About Information Extraction (Phase 3)
- Section segmentation critical for targeting limitations/future work sections
- LLM structured output via instructor reduces parsing errors
- Confidence scoring should combine multiple signals
- Batch processing needs job state persistence for resume

### About Data Acquisition Layer
- Token bucket rate limiting with registry pattern for per-source limits
- Circuit breaker protects against cascading failures
- Paper normalization unifies different API response schemas
- Multi-source aggregation merges data by DOI for best metadata

### About Claude Code Sub-Agents
- Defined as markdown files with YAML frontmatter in `.claude/agents/`
- Invoked via `@agent-name command` syntax
- Tool access controlled per agent
- Can coordinate via shared files (phases.md)

## Open Questions

1. **Extraction Quality**: What F1 score is achievable on structured extraction vs manual annotation?
2. **Prompt Optimization**: How many iterations needed to stabilize extraction prompts?
3. **Cost Management**: Token usage per paper extraction?
4. **Integration Testing**: Set up test environment with API credentials and Neo4j?

## Reference Materials

- **Paper**: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- **Denario Docs**: https://denario.readthedocs.io/
- **Phase Coordination**: [phases.md](phases.md)
- **Sub-Agents**: [.claude/agents/](../.claude/agents/)

## Notes for Next Session

- Read ALL memory-bank files on context reset (7 core files including phases.md)
- Check phases.md for current phase status
- **Sprint 03 PLANNING COMPLETE** - Ready for implementation
- Sprint 03 docs: `construction/sprints/sprint-03-extraction-pipeline.md`
- Requirements: `construction/requirements/extraction-pipeline-requirements.md`
- ADR-013 documents extraction approach decision
- Next: Create branch and begin Task 1 (PDF Text Extraction)
