# Knowledge Graph Options Comparison

**Created:** 2026-01-04
**Updated:** 2026-01-04
**Status:** Complete
**Purpose:** Compare existing scholarly knowledge graphs with a custom/dynamic solution for the Agentic-KG project

---

## Executive Summary

This document compares three existing scholarly knowledge graphs (CS-KG, Semantic Scholar, ORKG) against a custom Neo4j-based solution. The key finding is that **existing KGs are paper-centric**, while our project requires **research problems as first-class entities**. This fundamental mismatch means existing KGs can serve as data sources but not as the primary knowledge representation.

| Dimension | CS-KG | Semantic Scholar | ORKG | Custom (Neo4j) |
|-----------|-------|------------------|------|----------------|
| **Primary Entity** | Papers/Claims | Papers/Authors | Research Contributions | **Research Problems** |
| **Problem Support** | ❌ Implicit only | ❌ Not modeled | ⚠️ Partial (claims) | ✅ First-class |
| **Custom Schema** | ❌ Fixed | ❌ Fixed | ⚠️ Limited | ✅ Full control |
| **Vector Search** | ❌ No | ✅ SPECTER2 | ❌ No | ✅ Native |
| **Query Flexibility** | ⚠️ SPARQL | ⚠️ REST API | ⚠️ Web interface | ✅ Cypher + API |
| **Data Freshness** | ⚠️ Periodic updates | ✅ Live | ⚠️ Community-driven | ✅ Real-time |
| **Setup Complexity** | ✅ None (API) | ✅ None (API) | ✅ None (Web) | ⚠️ Requires setup |

**Recommendation:** Use a **simplified hybrid approach**:
1. **Custom Neo4j** as the primary knowledge graph with problem-centric schema
2. **Semantic Scholar API** as the external data source for paper metadata, citations, and embeddings
3. **arXiv/OpenAlex** for paper full-text access

**Note:** CS-KG was evaluated but excluded due to being a static academic dataset (data coverage ends at 2022) with no continuous updates. Since we must build a custom LLM extraction pipeline anyway for problems/assumptions/constraints, the pre-extracted task/method entities from CS-KG provide insufficient value to justify the integration complexity.

---

## 1. CS-KG (Computer Science Knowledge Graph)

### Overview
CS-KG is a large-scale automatically generated knowledge graph focused on Computer Science research entities and claims.

### Key Characteristics

| Attribute | Value |
|-----------|-------|
| **Scale** | 25M entities, 67M relationships (CS-KG 2.0) |
| **Coverage** | 14.5M+ Computer Science articles |
| **Entity Types** | Tasks, Methods, Materials, Metrics, Papers |
| **Relations** | 219 semantic relations |
| **Data Format** | RDF triples (TTL, CSV) |
| **Access** | SPARQL endpoint, downloadable dumps |
| **License** | CC BY 4.0 |

### Entity Model

```
Entities:
├── Task (research tasks/problems - loosely defined)
├── Method (algorithms, techniques)
├── Material (datasets, corpora)
├── Metric (evaluation measures)
└── Paper (source documents)

Example Triple:
<sentiment_analysis, uses, deep_learning_classifier>
<cloud_computing, includes, virtualization_security>
```

### Strengths
- **Comprehensive CS coverage**: Extensive extraction from 14.5M+ papers
- **Temporal data**: CS-KG 2.0 includes temporal annotations for trend analysis
- **Semantic relations**: 219 relation types capture nuanced relationships
- **Open access**: Free to download and query via SPARQL
- **Automatic generation**: Pipeline produces consistent extractions

### Weaknesses
- **Static dataset**: Data coverage ends at 2022; no continuous updates
- **Paper-centric**: Problems are not first-class entities
- **No vector search**: Lacks semantic similarity capabilities
- **Fixed schema**: Cannot extend entity types or attributes
- **CS-only**: Limited to Computer Science domain
- **Extraction quality**: Automatic extraction has variable accuracy
- **No provenance**: Limited evidence linking to source text
- **Academic project**: Updates depend on research team releasing new versions (~2-3 year gaps)

### Alignment with Project Goals

| Requirement | Alignment | Notes |
|-------------|-----------|-------|
| Problems as first-class entities | ❌ Poor | "Tasks" exist but lack problem-specific attributes |
| Assumptions/constraints modeling | ❌ None | Not represented |
| Evidence/provenance tracking | ⚠️ Partial | Links to papers but not to specific text spans |
| Relation types (EXTENDS, CONTRADICTS) | ⚠️ Partial | Has some relations but not problem-progression focused |
| Vector/semantic search | ❌ None | SPARQL only |
| Custom extraction pipeline | ❌ None | Fixed extraction pipeline |

### Access

- **SPARQL Endpoint**: https://w3id.org/cskg/sparql
- **Portal**: https://scholkg.kmi.open.ac.uk/
- **Download**: Available in TTL and CSV formats on Zenodo

---

## 2. Semantic Scholar Academic Graph (S2AG)

### Overview
Semantic Scholar provides the largest open scholarly knowledge graph, focused on papers, authors, citations, and semantic features.

### Key Characteristics

| Attribute | Value |
|-----------|-------|
| **Scale** | 225M+ papers, 100M+ authors, 2.8B+ citations |
| **Coverage** | All academic disciplines |
| **Entity Types** | Papers, Authors, Venues, Institutions |
| **Relations** | Citations, Authorship, Affiliation |
| **Data Format** | JSON via REST API |
| **Access** | REST API, monthly data dumps |
| **Rate Limits** | 1 RPS (authenticated), shared pool (unauthenticated) |

### Entity Model

```
Entities:
├── Paper
│   ├── paperId, title, abstract, year
│   ├── authors, venue, citations, references
│   ├── embedding (SPECTER2, 768 dims)
│   └── tldr (auto-generated summary)
├── Author
│   ├── authorId, name, affiliations
│   └── papers, citations
├── Venue
└── Institution
```

### API Capabilities

```python
# Paper search
GET /graph/v1/paper/search/bulk?query=transformer+attention

# Paper details with embeddings
GET /graph/v1/paper/{paper_id}?fields=title,abstract,embedding

# Author papers
GET /graph/v1/author/{author_id}/papers?fields=references

# Recommendations
GET /recommendations/v1/papers?positivePaperIds={ids}
```

### Strengths
- **Massive scale**: Largest open scholarly graph
- **Cross-disciplinary**: Covers all academic fields
- **SPECTER2 embeddings**: State-of-the-art paper embeddings for similarity
- **Rich metadata**: tldr summaries, influential citations, venues
- **Recommendations**: Built-in paper recommendation system
- **Regular updates**: Monthly snapshots, continuous API updates
- **Well-documented API**: Comprehensive REST API with good documentation

### Weaknesses
- **Paper-centric**: No research problem modeling
- **Fixed schema**: Cannot add custom entity types
- **Rate limits**: 1 RPS authenticated can be limiting
- **No SPARQL**: REST-only, less flexible for graph traversal
- **No claims/assertions**: Doesn't extract specific claims from papers
- **Citation-focused**: Relations are primarily bibliometric

### Alignment with Project Goals

| Requirement | Alignment | Notes |
|-------------|-----------|-------|
| Problems as first-class entities | ❌ Poor | Not modeled at all |
| Assumptions/constraints modeling | ❌ None | Not represented |
| Evidence/provenance tracking | ❌ None | No text-level provenance |
| Relation types (EXTENDS, CONTRADICTS) | ❌ Poor | Only citation relations |
| Vector/semantic search | ✅ Good | SPECTER2 embeddings available |
| Custom extraction pipeline | ❌ None | Fixed processing |

### Value as Data Source

Despite poor alignment as a primary KG, Semantic Scholar excels as a **data source**:
- Paper metadata enrichment (DOI → full metadata)
- Citation graph for understanding paper relationships
- SPECTER2 embeddings for paper similarity
- Author disambiguation

---

## 3. ORKG (Open Research Knowledge Graph)

### Overview
ORKG is a community-curated knowledge graph focused on research contributions and claims, making scholarly knowledge machine-readable.

### Key Characteristics

| Attribute | Value |
|-----------|-------|
| **Scale** | 1,000+ active contributors |
| **Coverage** | Multi-disciplinary |
| **Entity Types** | Papers, Contributions, Comparisons, Problems (limited) |
| **Relations** | Contribution-based relations |
| **Data Format** | Web interface, API |
| **Access** | Web platform, REST API |
| **Curation** | Expert crowdsourcing |

### Entity Model

```
Entities:
├── Paper
│   └── Contributions (structured claims)
├── Research Contribution
│   ├── Properties (key-value pairs)
│   └── Comparisons (vs other contributions)
├── Problem (limited support)
└── Comparison Table
```

### Strengths
- **Structured contributions**: Extracts structured claims from papers
- **Comparison tables**: Enables systematic literature comparisons
- **Expert curation**: Human-reviewed quality
- **Multi-disciplinary**: Not limited to CS
- **Active community**: Growing contributor base
- **Open infrastructure**: Open-source platform

### Weaknesses
- **Limited problem support**: Problems are secondary entities
- **Manual curation**: Doesn't scale automatically
- **Web-focused**: API is less developed than Semantic Scholar
- **No embeddings**: No vector search capability
- **Inconsistent coverage**: Depends on community contributions
- **Contribution-centric**: Not problem-progression oriented

### Alignment with Project Goals

| Requirement | Alignment | Notes |
|-------------|-----------|-------|
| Problems as first-class entities | ⚠️ Partial | Has "Research Problem" concept but limited |
| Assumptions/constraints modeling | ⚠️ Partial | Can be added as properties |
| Evidence/provenance tracking | ✅ Good | Links to source papers |
| Relation types (EXTENDS, CONTRADICTS) | ⚠️ Partial | Comparison-focused relations |
| Vector/semantic search | ❌ None | No embedding support |
| Custom extraction pipeline | ⚠️ Partial | Manual curation, templates |

### Value for Project

ORKG is **philosophically closest** to our project goals but lacks:
- Automation (requires manual curation)
- Problem-as-first-class-entity schema
- Vector search for semantic similarity
- Custom relation types for problem progression

---

## 4. Custom/Dynamic Option (Neo4j-based)

### Overview
A purpose-built knowledge graph using Neo4j with a schema designed specifically for research problem progression.

### Key Characteristics

| Attribute | Value |
|-----------|-------|
| **Scale** | Starts empty, grows with use |
| **Coverage** | User-defined scope |
| **Entity Types** | Problem (first-class), Paper, Author, custom |
| **Relations** | EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES |
| **Data Format** | Property graph (Cypher) |
| **Access** | Cypher queries, custom API |
| **Control** | Full schema control |

### Entity Model (from Phase 1 Design)

```
Problem Node:
├── Core
│   ├── id, statement, domain, status
│   └── embedding (1536 dims)
├── Context
│   ├── assumptions (text, implicit, confidence)
│   ├── constraints (text, type, confidence)
│   ├── datasets (name, url, available)
│   └── metrics (name, baseline_value)
├── Evidence
│   ├── source_doi, quoted_text
│   └── char_offset_start, char_offset_end
└── Metadata
    ├── extraction_model, confidence_score
    └── human_reviewed, version

Relations:
├── EXTENDS (confidence, evidence_doi)
├── CONTRADICTS (confidence, contradiction_type)
├── DEPENDS_ON (dependency_type)
├── REFRAMES (evidence_doi)
└── EXTRACTED_FROM (section, date)
```

### Query Capabilities

```cypher
-- Find tractable open problems with available datasets
MATCH (p:Problem)
WHERE p.status = 'open'
  AND ANY(d IN p.datasets WHERE d.available = true)
RETURN p

-- Semantic + structured hybrid search
CALL db.index.vector.queryNodes('problem_embedding', 10, $embedding)
YIELD node, score
WHERE node.domain = 'NLP' AND node.status = 'open'
RETURN node, score

-- Problem dependency chains
MATCH path = (p1:Problem)-[:DEPENDS_ON*1..3]->(p2:Problem)
WHERE p1.id = $problem_id
RETURN path
```

### Strengths
- **Problem-first schema**: Designed for research progression
- **Full attribute control**: Assumptions, constraints, datasets, metrics
- **Evidence provenance**: Character-level text spans
- **Native vector search**: Neo4j 5.x+ vector indexes
- **Hybrid queries**: Combine semantic + structured filters
- **Custom relations**: EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES
- **Version tracking**: Problem evolution over time
- **Human-in-the-loop**: Review workflow built into schema

### Weaknesses
- **Starts empty**: Requires population from external sources
- **Setup required**: Neo4j infrastructure needed
- **Maintenance burden**: Must manage updates and schema evolution
- **No pre-built extraction**: Must build or integrate extraction pipeline
- **Smaller ecosystem**: Less tooling than established solutions

### Alignment with Project Goals

| Requirement | Alignment | Notes |
|-------------|-----------|-------|
| Problems as first-class entities | ✅ Perfect | Core design principle |
| Assumptions/constraints modeling | ✅ Perfect | Explicit schema support |
| Evidence/provenance tracking | ✅ Perfect | Character-level spans |
| Relation types (EXTENDS, CONTRADICTS) | ✅ Perfect | Custom-designed for progression |
| Vector/semantic search | ✅ Perfect | Native Neo4j vector index |
| Custom extraction pipeline | ✅ Perfect | Full control over ingestion |

---

## 5. Comparison Matrix

### Functional Comparison

| Feature | CS-KG | Semantic Scholar | ORKG | Custom (Neo4j) |
|---------|-------|------------------|------|----------------|
| Research problems as entities | ⚠️ "Tasks" | ❌ | ⚠️ Partial | ✅ First-class |
| Problem assumptions | ❌ | ❌ | ⚠️ Manual | ✅ Structured |
| Problem constraints | ❌ | ❌ | ⚠️ Manual | ✅ Typed |
| Dataset availability tracking | ⚠️ Materials | ❌ | ⚠️ Properties | ✅ Explicit |
| Baseline/metric tracking | ⚠️ Separate | ❌ | ⚠️ Comparisons | ✅ Integrated |
| EXTENDS relation | ❌ | ❌ | ⚠️ Comparison | ✅ Native |
| CONTRADICTS relation | ❌ | ❌ | ⚠️ Manual | ✅ Native |
| Text-level provenance | ❌ | ❌ | ❌ | ✅ Char offsets |
| Vector embeddings | ❌ | ✅ SPECTER2 | ❌ | ✅ Custom |
| Hybrid search | ❌ | ⚠️ Limited | ❌ | ✅ Native |

### Operational Comparison

| Factor | CS-KG | Semantic Scholar | ORKG | Custom (Neo4j) |
|--------|-------|------------------|------|----------------|
| Setup effort | None | None | None | Medium |
| Maintenance | None | None | None | Ongoing |
| Cost | Free | Free (rate limited) | Free | Infrastructure |
| Scale | Fixed | Massive | Growing | User-controlled |
| Freshness | Periodic | Live | Community | Real-time |
| Customization | None | None | Templates | Full |
| Data sovereignty | None | None | None | Full |

### Integration Potential

| Integration | CS-KG | Semantic Scholar | ORKG |
|-------------|-------|------------------|------|
| **As primary KG** | ❌ Wrong schema | ❌ Wrong schema | ❌ Wrong schema |
| **As data source** | ✅ Task/method data | ✅ Paper metadata | ⚠️ Curated claims |
| **For enrichment** | ✅ CS domain knowledge | ✅ Citations, embeddings | ⚠️ Structured comparisons |
| **API stability** | ⚠️ Academic project | ✅ Well-maintained | ⚠️ Evolving |

---

## 6. Recommended Architecture

### Simplified Hybrid Approach

```
┌─────────────────────────────────────────────────────────────────┐
│                      Data Sources Layer                         │
├───────────────────────────────┬─────────────────────────────────┤
│       Semantic Scholar API    │        arXiv / OpenAlex         │
│       - Paper metadata        │        - Full text PDFs         │
│       - Citations/references  │        - Preprints              │
│       - SPECTER2 embeddings   │        - Open access papers     │
│       - Author information    │                                 │
└───────────────┬───────────────┴─────────────────┬───────────────┘
                │                                 │
                ▼                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Extraction Layer (Phase 2)                    │
│  - LLM-based problem extraction from paper full text             │
│  - Assumption/constraint/dataset/metric extraction               │
│  - Relation inference (EXTENDS, CONTRADICTS, DEPENDS_ON)         │
│  - Problem embedding generation (OpenAI text-embedding-3-small)  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Custom Knowledge Graph (Neo4j)                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Problem Nodes                         │    │
│  │  - statement, domain, status                             │    │
│  │  - assumptions, constraints, datasets, metrics           │    │
│  │  - embedding (1536d), evidence spans                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│              ┌───────────────┼───────────────┐                   │
│              ▼               ▼               ▼                   │
│         ┌────────┐      ┌────────┐      ┌────────┐              │
│         │EXTENDS │      │CONTRA- │      │DEPENDS │              │
│         │        │      │DICTS   │      │_ON     │              │
│         └────────┘      └────────┘      └────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

### Integration Strategy

1. **Semantic Scholar as Paper Metadata Source**
   - Fetch paper metadata via API (title, abstract, authors, venue, year)
   - Use SPECTER2 embeddings for paper similarity
   - Import citation graph for understanding paper relationships
   - Author disambiguation and affiliation data

2. **arXiv/OpenAlex for Full Text Access**
   - Download PDFs for LLM extraction
   - Access preprints and open access papers
   - Supplement Semantic Scholar metadata

3. **Custom Neo4j as Core KG**
   - Store problems with full schema (first-class entities)
   - Maintain vector indexes for semantic search
   - Support hybrid queries (semantic + structured)
   - Track provenance and human review status

4. **LLM Extraction Pipeline (Custom)**
   - Extract problems from paper PDFs (limitations, future work sections)
   - Extract assumptions, constraints, datasets, metrics
   - Generate problem embeddings
   - Infer EXTENDS/CONTRADICTS/DEPENDS_ON relations

### Why CS-KG Was Excluded

| Factor | Impact |
|--------|--------|
| **Static data (ends 2022)** | Missing 3+ years of recent research |
| **~2-3 year update gaps** | Cannot rely on timely updates |
| **We need custom extraction anyway** | Problems/assumptions/constraints not in CS-KG |
| **Schema mismatch** | CS-KG "tasks" ≠ our "problems" |
| **Integration complexity** | SPARQL queries add overhead for limited value |
| **Semantic Scholar covers papers** | Metadata needs already met |

---

## 7. Cost-Benefit Analysis

### Option A: Use Existing KG Only (CS-KG or ORKG)

| Pros | Cons |
|------|------|
| Zero setup | Wrong schema for problems |
| No maintenance | No vector search |
| Pre-populated data | Cannot customize relations |
| | Limited query flexibility |
| | No provenance tracking |

**Verdict:** ❌ Not viable for project goals

### Option B: Use Semantic Scholar Only

| Pros | Cons |
|------|------|
| Massive scale | Paper-centric, not problem-centric |
| Good API | Cannot store problems |
| Has embeddings | Rate limited |
| | No custom relations |

**Verdict:** ❌ Not viable as primary KG; ✅ Excellent as data source

### Option C: Custom Neo4j Only

| Pros | Cons |
|------|------|
| Perfect schema fit | Starts empty |
| Full control | Setup/maintenance burden |
| Native vector search | Must build extraction pipeline |
| Custom relations | No pre-existing data |

**Verdict:** ✅ Required for problem-centric design

### Option D: Hybrid (Recommended)

| Pros | Cons |
|------|------|
| Best of all worlds | Integration complexity |
| Problem-centric core | Multiple systems to manage |
| Enriched data | API rate limits |
| Scalable extraction | |

**Verdict:** ✅ Optimal approach

---

## 8. Conclusion

### Key Findings

1. **Existing KGs are paper-centric**: CS-KG, Semantic Scholar, and ORKG all model papers/contributions as primary entities, not research problems.

2. **Schema mismatch is fundamental**: The project requires assumptions, constraints, datasets, and metrics attached to problems—attributes not supported by existing KGs.

3. **Vector search is essential**: Only Semantic Scholar (SPECTER2) and Neo4j support vector embeddings; CS-KG and ORKG do not.

4. **Custom relations required**: EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES relations are not available in existing systems.

5. **Hybrid approach maximizes value**: Use existing KGs as data sources while maintaining a custom problem-centric graph.

### Final Recommendation

**Implement the custom Neo4j-based solution as designed in Phase 1**, with the following integrations:

| System | Role | Priority |
|--------|------|----------|
| **Neo4j (Custom)** | Primary KG for problems | Required |
| **Semantic Scholar** | Paper metadata, citations, embeddings | High |
| **arXiv/OpenAlex** | Paper full-text for extraction | High |

**Excluded:**
| System | Reason |
|--------|--------|
| **CS-KG** | Static dataset (ends 2022), schema mismatch, integration overhead exceeds value |
| **ORKG** | Manual curation doesn't scale, no vector search, API less mature |

This simplified approach ensures the project can model research problems as first-class entities while maintaining a lean integration footprint with well-maintained, continuously updated data sources.

---

## Sources

- [CS-KG 2.0 (Nature Scientific Data)](https://www.nature.com/articles/s41597-025-05200-8)
- [CS-KG Portal (ScholKG)](https://scholkg.kmi.open.ac.uk/)
- [Semantic Scholar API](https://www.semanticscholar.org/product/api)
- [Semantic Scholar Open Data Platform (arXiv)](https://arxiv.org/html/2301.10140v2)
- [ORKG Platform](https://orkg.org/)
- [ORKG Reference Book (2024)](https://www.researchgate.net/publication/380664373_Open_Research_Knowledge_Graph)
