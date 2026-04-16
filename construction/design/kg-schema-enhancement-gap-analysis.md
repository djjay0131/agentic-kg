# KG Schema Enhancement: Gap Analysis & Design

**Created:** 2026-02-20
**Status:** Draft - Pending Review
**Reference Paper:** "A Knowledge Graph-based RAG for Cross-Document Information Extraction" (Suryawanshi et al., ICPCSN-2025)
**Reference Implementations:** Microsoft GraphRAG, KG-LLM-MDQA (Wang et al., AAAI 2024)

---

## 1. Executive Summary

After 10 sprints building the agentic-kg system, we have a strong **problem-centric** knowledge graph with sophisticated mention-to-concept canonicalization, confidence-based routing, and multi-agent consensus workflows. However, comparing against the Suryawanshi et al. paper and related work (Microsoft GraphRAG, KG-LLM-MDQA), we identify **significant gaps in entity diversity, relationship richness, community structure, and RAG-oriented retrieval** that limit the system's ability to serve as a comprehensive research knowledge graph.

Our system excels at **depth** (deep problem decomposition with assumptions, constraints, datasets, metrics, baselines, and provenance), but lacks **breadth** (the broader entity ecosystem that makes cross-document information extraction effective).

---

## 2. What We Built: Current State (Sprints 0-10)

### 2.1 Entity Types (5 node types)

| Entity | Purpose | First-Class Node? |
|--------|---------|-------------------|
| **Problem** | Legacy research problem | Yes |
| **ProblemMention** | Paper-specific problem statement with provenance | Yes |
| **ProblemConcept** | Canonical aggregated problem (deduplication target) | Yes |
| **Paper** | Source paper with metadata | Yes |
| **Author** | Paper author | Yes |

### 2.2 Supporting Models (embedded, NOT first-class nodes)

| Model | Where Used | Searchable? |
|-------|-----------|-------------|
| Assumption | Embedded in Problem/Mention/Concept | No (not a node) |
| Constraint | Embedded in Problem/Mention/Concept | No (not a node) |
| Dataset | Embedded in Problem/Mention/Concept | No (not a node) |
| Metric | Embedded in Problem/Mention/Concept | No (not a node) |
| Baseline | Embedded in Problem/Mention/Concept | No (not a node) |
| Evidence | Embedded in Problem | No (not a node) |

### 2.3 Relationship Types (7 edge types)

| Relationship | From → To | Purpose |
|-------------|-----------|---------|
| EXTENDS | Problem → Problem | Problem B builds on Problem A |
| CONTRADICTS | Problem → Problem | Conflicting findings |
| DEPENDS_ON | Problem → Problem | Prerequisite relationship |
| REFRAMES | Problem → Problem | Redefines problem space |
| EXTRACTED_FROM | Problem → Paper | Provenance link |
| AUTHORED_BY | Paper → Author | Authorship |
| INSTANCE_OF | ProblemMention → ProblemConcept | Canonical linking |

### 2.4 Key Capabilities Built

- Mention-to-concept canonicalization with confidence-based routing
- Vector similarity matching (1536-dim OpenAI embeddings on 3 node types)
- Multi-agent consensus (Evaluator, Maker, Hater, Arbiter)
- Human review queue with SLA management
- Concept refinement at mention count thresholds
- 4 research agents (Ranking, Continuation, Evaluation, Synthesis)
- Full extraction pipeline (PDF → sections → LLM extraction → KG integration)
- FastAPI backend + Next.js frontend with graph visualization

---

## 3. What the Paper Proposes

### 3.1 Entity Types (Suryawanshi et al.)

The paper extracts **9 entity types** from research papers:

| Entity Type | Description | Count in Paper's Results |
|-------------|-------------|--------------------------|
| **Person** | Authors and researchers referenced | ~680 (highest) |
| **Concept** | Research concepts ("attention mechanism", "gradient descent") | ~520 |
| **Model** | ML models/architectures ("BERT", "GPT-4", "ResNet") | ~400 |
| **Paper** | Research papers referenced | ~350 |
| **Topic** | Research topics/areas ("NLP", "Computer Vision") | ~250 |
| **Method** | Research methodologies ("fine-tuning", "transfer learning") | ~200 |
| **Variable** | Variables in formulations | ~100 |
| **Dataset** | Datasets referenced | ~60 |
| **Equation** | Mathematical formulations | ~40 |

**Total:** 2,601 entities across 10 papers

### 3.2 Relationship Types (Suryawanshi et al.)

The paper extracts **10+ relationship types**:

| Relationship | Description |
|-------------|-------------|
| **Publication/Venue** | Paper published at venue |
| **Co-author** | Between authors of same paper |
| **Author Role** | Author contributed to paper |
| **Methodology** | Paper uses method |
| **Tool/Resource** | Paper uses tool/software |
| **Comparison** | Between models/methods |
| **Affiliation** | Person affiliated with organization |
| **Application** | Method applied to task |
| **Research Area** | Entity belongs to topic |
| **Dataset Origin** | Dataset sourced from |

**Total:** 3,052 relations across 10 papers

### 3.3 Additional Capabilities

1. **Entity Descriptions** - Each entity gets a brief text description to enhance vector search alignment
2. **Community Detection** - Louvain algorithm identifies 311 clusters (avg 8.36 nodes/community)
3. **Hierarchical Summarization** - Multi-level summaries from subgraph structure
4. **Vector Search-Aided Retrieval** - Keyword extraction → vector search → graph traversal → LLM synthesis

### 3.4 Paper's Own Limitations (from Conclusions)

The paper acknowledges these challenges needing future work:
1. **Entity recognition inconsistencies** - "RAG" vs "Retrieval-Augmented Generation" treated as distinct entities
2. **Search depth constraints** - Limited to depth-3 graph traversal
3. **Rudimentary node selection** - Node importance based primarily on degree centrality
4. **Subtopic selection** - Overly generic subtopics dilute summary quality
5. **Scalability** - Not tested beyond 10 papers

---

## 4. Gap Analysis: Where We Differ

### 4.1 MISSING ENTITY TYPES (Critical Gaps)

#### Gap 1: Topic / Research Area (HIGH PRIORITY)

**Paper has it:** Yes - "Topic" is a first-class entity type (~250 nodes)
**We have:** `domain` as a string field on Problem/Mention/Concept (e.g., "NLP", "Computer Vision")

**Why this matters:**
- Topics can't have relationships, embeddings, or be searched independently
- Can't discover topic clusters or topic evolution over time
- Can't answer "what problems exist in NLP?" via graph traversal
- Can't visualize research landscape by topic hierarchy

**What we need:**
```
(:Topic {id, name, description, parent_topic_id, embedding})
(:ProblemConcept)-[:BELONGS_TO]->(:Topic)
(:Paper)-[:RESEARCHES]->(:Topic)
(:Topic)-[:SUBTOPIC_OF]->(:Topic)  // hierarchical
```

#### Gap 2: Concept (Generic Research Concept) (HIGH PRIORITY)

**Paper has it:** Yes - "Concept" is the 2nd most common entity type (~520 nodes)
**We have:** ProblemConcept (only for canonical problems, not general concepts)

**Why this matters:**
- Research concepts like "attention mechanism", "knowledge distillation", "transfer learning" are NOT problems - they're techniques, ideas, and abstractions that problems reference
- Our extraction only captures problems (from Limitations/Future Work sections)
- We miss the broader conceptual fabric that connects problems

**What we need:**
```
(:ResearchConcept {id, name, description, concept_type, embedding})
(:ProblemConcept)-[:INVOLVES_CONCEPT]->(:ResearchConcept)
(:Paper)-[:DISCUSSES]->(:ResearchConcept)
(:ResearchConcept)-[:RELATED_TO]->(:ResearchConcept)
```

#### Gap 3: Model / Architecture (MEDIUM PRIORITY)

**Paper has it:** Yes - "Model" is the 3rd most common entity type (~400 nodes)
**We have:** Models appear only in Baseline.name strings, not as nodes

**Why this matters:**
- Can't track which models are used across papers
- Can't compare model performance across different problems
- Can't discover model evolution (BERT → RoBERTa → DeBERTa)

**What we need:**
```
(:Model {id, name, description, architecture_type, year_introduced, paper_doi, embedding})
(:Paper)-[:USES_MODEL]->(:Model)
(:Model)-[:VARIANT_OF]->(:Model)
(:Baseline)-[:IMPLEMENTS]->(:Model)
```

#### Gap 4: Method / Methodology (MEDIUM PRIORITY)

**Paper has it:** Yes - "Method" entity type (~200 nodes)
**We have:** Methods appear in constraint text and baseline descriptions, not as nodes

**Why this matters:**
- Can't track methodology adoption across research areas
- Can't link problems to the methods used to address them
- Can't discover methodological trends

**What we need:**
```
(:Method {id, name, description, method_type, embedding})
(:Paper)-[:APPLIES_METHOD]->(:Method)
(:ProblemConcept)-[:ADDRESSED_BY]->(:Method)
```

#### Gap 5: Equation / Variable (LOW PRIORITY)

**Paper has it:** Yes - ~40 equations, ~100 variables
**We have:** Not captured at all

**Assessment:** Lower priority for our problem-centric use case. Equations are important for mathematical/theoretical papers but less critical for the research progression workflow.

### 4.2 MISSING RELATIONSHIP TYPES (Critical Gaps)

| Missing Relationship | Priority | Impact |
|---------------------|----------|--------|
| **CITES** (Paper → Paper) | HIGH | No citation graph - can't discover influence chains |
| **BELONGS_TO** (Entity → Topic) | HIGH | No topic-based navigation or clustering |
| **USES_MODEL** (Paper → Model) | MEDIUM | Can't track model adoption |
| **APPLIES_METHOD** (Paper → Method) | MEDIUM | Can't track methodology trends |
| **CO_AUTHORED** (Author ↔ Author) | LOW | Can't discover collaboration networks |
| **AFFILIATED_WITH** (Author → Organization) | LOW | No institutional context |
| **PUBLISHED_AT** (Paper → Venue) | LOW | No venue-based analysis |

### 4.3 MISSING CAPABILITIES

#### Capability Gap 1: Community Detection (HIGH PRIORITY)

**Paper uses:** Louvain method → 311 communities from 2,601 entities
**Microsoft GraphRAG uses:** Leiden algorithm → hierarchical community structure
**We have:** Nothing - no community detection at all

**Why this matters:**
- Can't discover hidden research clusters
- Can't partition the graph for efficient retrieval
- Can't generate topic-level summaries
- Can't identify research frontiers or convergence areas

**What we need:**
- Implement Leiden/Louvain community detection on the full graph
- Store community assignments as node properties or separate Community nodes
- Enable multi-level community hierarchy (paper → topic → domain)

#### Capability Gap 2: Hierarchical Graph Summarization (HIGH PRIORITY)

**Paper uses:** Subgraph extraction → subtopic identification → node summarization → compilation
**Microsoft GraphRAG uses:** Community-level summaries at multiple hierarchy levels
**We have:** ProblemConcept.canonical_statement (single-level only, problems only)

**Why this matters:**
- Can't answer global questions ("What are the main research themes?")
- Can't generate research landscape overviews
- Can't provide different levels of detail based on query needs

**What we need:**
- Community-level summaries stored in the graph
- Multi-resolution: domain → topic → subtopic → individual concepts
- LLM-generated summaries at each level
- Incremental update as new papers are ingested

#### Capability Gap 3: Graph-Based RAG Retrieval (HIGH PRIORITY)

**Paper uses:** Keyword extraction → vector search → graph neighbor expansion → LLM synthesis
**KG-LLM-MDQA uses:** TF-IDF seed → graph traversal agent → budget-constrained retrieval
**We have:** Vector search for mention-to-concept matching only (not for query answering)

**Why this matters:**
- Our vector search is internal (matching pipeline), not exposed for user queries
- No graph traversal during retrieval to expand context
- Can't leverage graph structure to improve retrieval relevance
- Can't do cross-document QA

**What we need:**
- Query endpoint that combines vector search + graph traversal
- Neighbor expansion strategy (configurable depth)
- Context assembly from graph paths
- LLM synthesis of retrieved subgraph into coherent response

#### Capability Gap 4: Entity Normalization / Disambiguation (MEDIUM PRIORITY)

**Paper acknowledges as limitation:** "RAG" vs "Retrieval-Augmented Generation" treated as distinct
**We have:** Solved for problems (mention-to-concept architecture) but not for other entity types

**What we need:**
- Extend the mention-to-concept pattern to other entity types (Models, Methods, Concepts)
- Entity normalization during extraction (canonical names for common entities)
- Alias resolution (maintain mapping of alternate names)

#### Capability Gap 5: Entity Descriptions for Vector Search (MEDIUM PRIORITY)

**Paper uses:** Brief descriptions added to each entity node for enhanced vector search
**We have:** Embeddings generated from entity statements/names, but no separate description field

**What we need:**
- Add `description` field to all entity types
- Generate descriptions during extraction ("BERT: A pre-trained transformer-based language model for NLP tasks")
- Use description + name for embedding generation (richer semantic signal)

---

## 5. Comparison Matrix

| Capability | Suryawanshi Paper | Our System | Gap Severity |
|-----------|-------------------|------------|--------------|
| **Problem extraction** | Not specific | Deep (assumptions, constraints, metrics, baselines) | We're AHEAD |
| **Problem deduplication** | Not addressed | Mention-to-concept with agents | We're AHEAD |
| **Entity types** | 9 types | 5 types (3 problem-related) | SIGNIFICANT GAP |
| **Relationship types** | 10+ types | 7 types | MODERATE GAP |
| **Citation graph** | Via Paper-Citation | Not implemented | SIGNIFICANT GAP |
| **Topics/Research Areas** | First-class entities | String field only | SIGNIFICANT GAP |
| **Generic Concepts** | First-class entities | Not present | SIGNIFICANT GAP |
| **Models as entities** | First-class entities | Embedded in baselines | MODERATE GAP |
| **Methods as entities** | First-class entities | Not present | MODERATE GAP |
| **Community detection** | Louvain method | Not implemented | SIGNIFICANT GAP |
| **Hierarchical summarization** | Multi-level | Single-level (concept only) | SIGNIFICANT GAP |
| **Vector search** | Entity embeddings + descriptions | Entity embeddings only | MINOR GAP |
| **Graph-based RAG retrieval** | Keyword + vector + graph | Vector matching only (internal) | SIGNIFICANT GAP |
| **Entity normalization** | Acknowledged as limitation | Solved for problems, not others | PARTIAL |
| **Human review** | Not present | Full queue with SLA | We're AHEAD |
| **Agent workflows** | Not present | Multi-agent consensus | We're AHEAD |
| **Provenance tracking** | Not present | Full evidence spans + metadata | We're AHEAD |
| **Scalability tested** | 10 papers | Not tested at scale | BOTH LIMITED |

---

## 6. What We Failed to Implement from the Paper's Conclusions

The paper's conclusion states these as key contributions/capabilities:

### 6.1 Implemented (we have)
- "Structured, interpretable representation of information" - YES (problems with full attribute decomposition)
- "Structured search method outperforms traditional techniques" - PARTIAL (we have vector search but not full graph-based retrieval)

### 6.2 NOT Implemented
1. **"Domain-specific knowledge graph that organizes concepts, methodologies, and relationships"** - We organize problems, but NOT concepts or methodologies as first-class entities
2. **"Navigate relevant methodologies, compare technologies, identify optimal approaches"** - We can't do this because methods and models aren't nodes
3. **"Integrating structured knowledge graphs with vector search for query answering"** - Our vector search is internal (matching), not query-facing
4. **"Community detection refined knowledge structuring"** - Not implemented at all
5. **"Hierarchical summarization effectively captured core topics"** - Not implemented at all

### 6.3 The Paper's Future Work (their unfinished business)
The paper identifies these as future improvements needed:
1. **"Optimizing subtopic selection"** - We could leapfrog this with LLM-guided topic extraction
2. **"Improving scalability"** - Both systems need this
3. **"Incorporating adaptive retrieval mechanisms"** - Our confidence-based routing is actually more sophisticated than what they propose
4. **"Entity recognition inconsistencies"** - Our mention-to-concept architecture already addresses this for problems; we can extend it

---

## 7. Proposed Enhancement Design: "Entity Ecosystem Expansion"

### 7.1 Design Principles

1. **Additive, not disruptive** - New entity types and capabilities added alongside existing schema
2. **Extraction pipeline extension** - Extend LLM extraction prompts to capture new entity types
3. **Mention-to-concept pattern reuse** - Apply the same canonicalization pattern to new entity types
4. **Incremental value** - Each phase delivers standalone value

### 7.2 Proposed New Schema

#### New Node Types

```cypher
// Topic hierarchy
(:Topic {
  id: String,
  name: String,
  description: String,
  level: String,          // "domain" | "area" | "subtopic"
  parent_topic_id: String?,
  paper_count: Int,
  concept_count: Int,
  embedding: [Float],
  created_at: DateTime,
  updated_at: DateTime
})

// Generic research concept (not a problem)
(:ResearchConcept {
  id: String,
  canonical_name: String,
  description: String,
  concept_type: String,   // "technique" | "theory" | "framework" | "paradigm"
  aliases: [String],
  mention_count: Int,
  embedding: [Float],
  created_at: DateTime,
  updated_at: DateTime
})

// ML Model / Architecture
(:Model {
  id: String,
  name: String,
  description: String,
  model_type: String,     // "language_model" | "vision_model" | "multimodal" | ...
  architecture: String?,  // "transformer" | "cnn" | "rnn" | ...
  year_introduced: Int?,
  introducing_paper_doi: String?,
  embedding: [Float],
  created_at: DateTime,
  updated_at: DateTime
})

// Research Method / Methodology
(:Method {
  id: String,
  name: String,
  description: String,
  method_type: String,    // "training" | "evaluation" | "data_processing" | "optimization" | ...
  embedding: [Float],
  created_at: DateTime,
  updated_at: DateTime
})

// Community (from community detection)
(:Community {
  id: String,
  level: Int,             // hierarchy level (0=top, higher=finer)
  summary: String,        // LLM-generated summary of community
  member_count: Int,
  key_themes: [String],
  created_at: DateTime,
  updated_at: DateTime
})
```

#### New Relationship Types

```cypher
// Topic relationships
(:ProblemConcept)-[:BELONGS_TO]->(:Topic)
(:ResearchConcept)-[:BELONGS_TO]->(:Topic)
(:Paper)-[:RESEARCHES]->(:Topic)
(:Topic)-[:SUBTOPIC_OF]->(:Topic)

// Concept relationships
(:ProblemConcept)-[:INVOLVES_CONCEPT]->(:ResearchConcept)
(:Paper)-[:DISCUSSES]->(:ResearchConcept)
(:ResearchConcept)-[:RELATED_TO]->(:ResearchConcept)

// Model relationships
(:Paper)-[:USES_MODEL]->(:Model)
(:Model)-[:VARIANT_OF]->(:Model)
(:ProblemConcept)-[:BENCHMARKED_ON]->(:Model)

// Method relationships
(:Paper)-[:APPLIES_METHOD]->(:Method)
(:ProblemConcept)-[:ADDRESSED_BY]->(:Method)
(:Method)-[:EXTENDS_METHOD]->(:Method)

// Citation graph
(:Paper)-[:CITES]->(:Paper)

// Community membership
(:ProblemConcept)-[:MEMBER_OF]->(:Community)
(:ResearchConcept)-[:MEMBER_OF]->(:Community)
(:Model)-[:MEMBER_OF]->(:Community)
(:Community)-[:PARENT_COMMUNITY]->(:Community)
```

### 7.3 Implementation Phases

#### Phase A: Topics & Concepts (Sprint 11)
**Goal:** Add Topic and ResearchConcept entity types with extraction

Tasks:
1. Define Pydantic models for Topic and ResearchConcept
2. Extend Neo4j schema with new constraints/indexes/vector indexes
3. Extend LLM extraction prompts to extract topics, concepts from papers
4. Add mention-to-concept pattern for ResearchConcept (reuse ConceptMatcher)
5. Add BELONGS_TO and INVOLVES_CONCEPT relationships
6. Build topic hierarchy from extracted topics (LLM-assisted parent assignment)
7. Backfill: Convert existing `domain` strings to Topic nodes
8. Tests: Unit + integration for new entity types

**Acceptance Criteria:**
- Topic and ResearchConcept nodes created during paper ingestion
- Topics form a hierarchy (domain → area → subtopic)
- ResearchConcepts linked to ProblemConcepts and Papers
- Existing domain fields migrated to Topic nodes
- Vector search works on new entity types

#### Phase B: Models & Methods + Citation Graph (Sprint 12)
**Goal:** Add Model and Method entities, plus paper-to-paper citations

Tasks:
1. Define Pydantic models for Model and Method
2. Extend Neo4j schema
3. Extend LLM extraction to identify models and methods from papers
4. Build citation graph from Semantic Scholar API data
5. Add USES_MODEL, APPLIES_METHOD, CITES relationships
6. Model variant detection (BERT → RoBERTa → DeBERTa chain)
7. Tests

**Acceptance Criteria:**
- Models and Methods extracted as first-class nodes
- Citation edges between papers
- Can query "which models are used for [topic]?"
- Can traverse citation chains

#### Phase C: Community Detection & Hierarchical Summarization (Sprint 13)
**Goal:** Implement community detection and multi-level summaries

Tasks:
1. Implement Leiden/Louvain community detection (python-igraph or networkx)
2. Create Community nodes at multiple hierarchy levels
3. LLM-generated summaries for each community
4. Incremental community update when new entities are added
5. API endpoints for community browsing
6. Frontend visualization of communities
7. Tests

**Acceptance Criteria:**
- Graph partitioned into communities at multiple levels
- Each community has an LLM-generated summary
- Can browse research landscape by community
- Communities update incrementally with new data

#### Phase D: Graph-Based RAG Retrieval (Sprint 14)
**Goal:** Query-facing retrieval combining vector search + graph traversal

Tasks:
1. Implement keyword extraction from user queries
2. Vector search over all entity types (not just problems)
3. Graph neighbor expansion with configurable depth
4. Context assembly from retrieved subgraph
5. LLM synthesis endpoint (question → answer with provenance)
6. Community-based context (add community summaries to context)
7. API endpoint: POST /api/query with RAG response
8. Tests + evaluation against baseline retrieval

**Acceptance Criteria:**
- Can ask natural language questions about the research graph
- Retrieval uses graph structure to expand context beyond vector hits
- Responses include provenance (which papers/entities contributed)
- Measurably better than vector-only retrieval for multi-hop questions

---

## 8. Priority Recommendation

### Immediate (Sprint 11-12): Entity Ecosystem
These are the most impactful gaps. Adding Topics and Concepts transforms the graph from a "problem database" into a true "research knowledge graph." Adding citation edges enables influence analysis.

### Near-term (Sprint 13): Community Structure
Community detection is the key insight from GraphRAG that enables global queries and research landscape navigation. This builds on the richer entity graph from Phase A/B.

### Medium-term (Sprint 14): RAG Retrieval
Graph-based RAG retrieval is the "killer feature" that makes the knowledge graph useful for researchers. This requires the entity ecosystem and community structure to be in place first.

---

## 9. Addressing the Paper's Limitations (Where We Can Leapfrog)

| Paper Limitation | Our Advantage | Enhancement |
|-----------------|---------------|-------------|
| Entity recognition inconsistencies | We have mention-to-concept architecture | Extend canonicalization to all entity types |
| Search depth constraints (depth-3) | We have configurable graph traversal | Implement adaptive depth based on query complexity |
| Rudimentary node selection (degree-based) | We have LLM-based scoring | Use LLM to score node relevance for retrieval |
| Overly generic subtopics | We have structured extraction | LLM-guided topic hierarchy with specificity constraints |
| Scalability (10 papers only) | We have async pipeline + checkpointing | Already better positioned for scale |
| No deduplication | We have mention-to-concept | Extend pattern to all entity types |
| No human review | We have full review queue | Already ahead |
| No agent workflows | We have multi-agent consensus | Already ahead |

---

## 10. Repo Comparison

### Suryawanshi et al. Paper
- **No public repo found.** GitHub user `sudhanshu19102003` has 14 repos, none related to this paper.
- The paper appears to be a conference paper without an accompanying code release.

### KG-LLM-MDQA (Wang et al., AAAI 2024)
- **Repo:** https://github.com/YuWVandy/KG-LLM-MDQA
- **Approach:** Document-passage graph (not entity-based KG). Nodes are text chunks, edges are keyword/entity/similarity connections.
- **Key Innovation:** LLM-fine-tuned graph traversal agents (T5/LLaMA) that learn to navigate the passage graph
- **Not directly comparable** to our approach - they build per-question passage graphs, not persistent entity KGs

### Microsoft GraphRAG
- **Repo:** https://github.com/microsoft/graphrag
- **Approach:** Entity extraction → Leiden community detection → hierarchical summaries → global/local search
- **Most relevant to our enhancement plan** - their community detection + hierarchical summarization approach is exactly what we're missing
- **Key difference:** They're document-generic; we're research-paper-specific with richer problem decomposition

---

## 11. Open Questions for Design Review

1. **Entity extraction scope:** Should we extract all 9 entity types from the paper, or prioritize the top 4 (Topic, Concept, Model, Method)?
2. **Concept canonicalization:** Do we extend the full mention-to-concept pipeline (with agents + human review) to ResearchConcepts, or use a simpler auto-merge approach?
3. **Community detection algorithm:** Leiden (hierarchical, used by GraphRAG) vs Louvain (simpler, used by paper)?
4. **RAG retrieval scope:** Should the RAG endpoint be a separate service, or integrated into the existing API?
5. **Backward compatibility:** How do we migrate existing `domain` string fields to Topic nodes without breaking existing functionality?

---

## References

- Suryawanshi et al., "A Knowledge Graph-based RAG for Cross-Document Information Extraction," ICPCSN-2025
- Wang et al., "Knowledge Graph Prompting for Multi-Document Question Answering," AAAI 2024
- Edge et al., "From Local to Global: A Graph RAG Approach to Query-Focused Summarization," 2024
- Microsoft GraphRAG: https://github.com/microsoft/graphrag
- KG-LLM-MDQA: https://github.com/YuWVandy/KG-LLM-MDQA
