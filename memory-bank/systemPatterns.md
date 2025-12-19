# System Patterns: Agentic Knowledge Graph Architecture

## System Architecture

### High-Level Overview

The system is organized into three conceptual layers as defined in the Agentic Knowledge Graphs paper:

```
┌─────────────────────────────────────────────────────────────────┐
│                   AGENTIC ORCHESTRATION LAYER                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ Ranking  │ │Continua- │ │Evaluation│ │Synthesis │          │
│  │  Agent   │ │tion Agent│ │  Agent   │ │  Agent   │          │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │
│       │            │            │            │                  │
│       └────────────┴────────────┴────────────┘                  │
│                         │                                       │
│              ┌──────────▼──────────┐                           │
│              │  Agent Orchestrator │  (LangGraph / AG2)        │
│              └──────────┬──────────┘                           │
└─────────────────────────┼───────────────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────────────┐
│               KNOWLEDGE REPRESENTATION LAYER                    │
│                          │                                      │
│    ┌─────────────────────▼─────────────────────┐               │
│    │         Research Knowledge Graph           │               │
│    │  ┌─────────────────────────────────────┐  │               │
│    │  │  Problems (first-class entities)    │  │               │
│    │  │  • Assumptions, Constraints         │  │               │
│    │  │  • Datasets, Metrics, Baselines     │  │               │
│    │  │  • Evidence spans + Provenance      │  │               │
│    │  └─────────────────────────────────────┘  │               │
│    │  ┌─────────────────────────────────────┐  │               │
│    │  │  Relations                          │  │               │
│    │  │  extends | contradicts | depends-on │  │               │
│    │  └─────────────────────────────────────┘  │               │
│    └───────────────────────────────────────────┘               │
│                          │                                      │
│    ┌─────────────────────┴─────────────────────┐               │
│    │  Hybrid Retrieval: Graph + Vector Index   │               │
│    └───────────────────────────────────────────┘               │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────────────┐
│              AUTOMATION AND EXTRACTION LAYER                    │
│                          │                                      │
│    ┌─────────────────────▼─────────────────────┐               │
│    │         Denario Document Processing        │               │
│    │  • Ingestion from arXiv, OpenAlex         │               │
│    │  • Section segmentation                    │               │
│    │  • Structured extraction (LLM + heuristics)│               │
│    │  • Schema validation                       │               │
│    └───────────────────────────────────────────┘               │
│                          ▲                                      │
│    ┌─────────────────────┴─────────────────────┐               │
│    │         Research Papers (PDF/Text)         │               │
│    └───────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

## Layer Details

### Layer 1: Knowledge Representation

**Purpose:** Store and query structured research knowledge

**Components:**
- **Problem Nodes**: Research problems as first-class entities
  - Attributes: assumptions, constraints, datasets, metrics, baselines
  - Evidence spans: DOIs, section IDs, quoted text
  - Confidence scores and extraction metadata

- **Relation Edges**: Semantic connections between problems
  - `extends`: Problem B extends work on Problem A
  - `contradicts`: Problem B presents conflicting findings
  - `depends-on`: Problem B requires solution to Problem A
  - `reframes`: Problem B redefines the problem space

- **Vector Index**: Embeddings for semantic search
  - Problem statement embeddings
  - Assumption/constraint embeddings
  - Enables hybrid structured + semantic retrieval

### Layer 2: Automation and Extraction

**Purpose:** Populate the knowledge graph from research papers

**Pipeline Stages:**

```
[Paper Source] → [Ingestion] → [Segmentation] → [Extraction] → [Validation] → [Graph]
```

1. **Document Ingestion**
   - Sources: arXiv, OpenAlex, PDF uploads
   - Output: Structured intermediate representation

2. **Section Segmentation**
   - Layout-aware parsing
   - Identify: Introduction, Methods, Limitations, Future Work
   - Leverage section priors for extraction

3. **Structured Problem Extraction**
   - Hybrid heuristic + LLM approach
   - Target high-yield sections (Limitations, Future Work)
   - Emit normalized JSON conforming to schema
   - Link all content to evidence spans

4. **Normalization and Validation**
   - Schema validation
   - Ontology mapping where available
   - Deduplication via lexical + embedding similarity

5. **Relation Inference**
   - Pairwise comparison prompts
   - Contextual similarity signals
   - Confidence scoring for candidate links

### Layer 3: Agentic Orchestration

**Purpose:** Operate over the graph to support research progression

**Agent Types:**

| Agent | Input | Output | Purpose |
|-------|-------|--------|---------|
| **Ranking** | Graph query | Ranked problem list | Prioritize by tractability, data availability |
| **Continuation** | Selected problem | Proposed next step | Suggest experiments, extensions, proofs |
| **Evaluation** | Proposal + datasets | Execution results | Run reproducible workflows |
| **Synthesis** | Results | New graph artifacts | Summarize and update graph state |

**Orchestration Pattern:**
```
                    ┌──────────────┐
                    │ Human Review │
                    │  (approve/   │
                    │   modify/    │
                    │   reject)    │
                    └──────┬───────┘
                           │
┌─────────┐    ┌───────────▼───────────┐    ┌──────────┐
│ Ranking │───▶│ Continuation Proposal │───▶│Evaluation│
│  Agent  │    │        Agent          │    │  Agent   │
└─────────┘    └───────────────────────┘    └────┬─────┘
     ▲                                           │
     │         ┌───────────────────┐             │
     └─────────│  Synthesis Agent  │◀────────────┘
               │ (update graph)    │
               └───────────────────┘
```

## Design Patterns

### Closed-Loop Research Progression
The system forms an iterative loop:
1. Extract problems from literature
2. Rank and select problems
3. Propose continuations
4. Execute evaluations
5. Write results back to graph
6. Updated graph informs next iteration

### Human-in-the-Loop Governance
- Approval checkpoints at key decisions
- Edit/reject options for agent proposals
- Feedback logging for policy refinement
- Gradual autonomy increases over time

### Provenance-First Design
- Every extracted element links to source
- DOI, section ID, character offset tracking
- Confidence scores on all inferences
- Version history for graph updates

### Hybrid Symbolic-Semantic Retrieval
- Structured graph queries (filter by domain, dataset availability)
- Vector similarity search (conceptual matching)
- Combined ranking for best results

## Critical Integration Points

### Denario Integration
```python
# Denario provides document processing backbone
from denario import Denario

# Extended for knowledge graph population
class KGDenario(Denario):
    def extract_problems(self, paper_path):
        """Extract structured problems from paper"""
        pass

    def populate_graph(self, problems):
        """Add problems to knowledge graph"""
        pass
```

### Agent Framework Integration
```python
# LangGraph for stateful agent workflows
from langgraph.graph import StateGraph

# Define agent workflow
workflow = StateGraph(ResearchState)
workflow.add_node("rank", ranking_agent)
workflow.add_node("continue", continuation_agent)
workflow.add_node("evaluate", evaluation_agent)
workflow.add_node("synthesize", synthesis_agent)
workflow.add_edge("rank", "continue")
# ... conditional edges for human review
```

## Component Relationships

```
Denario Core
    │
    ├── Document Processing ──► Extraction Pipeline ──► Knowledge Graph
    │
    ├── Agent Framework (AG2/LangGraph)
    │       │
    │       └── Specialized Agents ──► Graph Operations
    │
    └── GUI (Streamlit)
            │
            └── Human Review Interface
```

## Technical Decisions

### Why Problems as First-Class Entities?
- Papers/claims as units don't capture "what's still open"
- Problems can be linked, tracked, and progressed
- Enables queries like "tractable problems with available data"

### Why Hybrid Retrieval?
- Structured queries alone miss conceptual similarity
- Semantic search alone can't filter by constraints
- Combination provides best of both worlds

### Why Human-in-the-Loop?
- Agents can make reasoning errors
- Research direction is high-stakes
- Trust requires transparency and control
- Gradual autonomy as system proves reliable
