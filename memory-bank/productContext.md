# Product Context: Agentic Knowledge Graphs for Research Progression

## Problem Statement

The volume of scientific publication has reached a point where progress is constrained not by a lack of ideas, but by the difficulty of systematically advancing existing ones. Current tools emphasize retrieval and summarization but fail to support the full process of research progression:

1. **Discovery tools stop at retrieval** - Keyword search and citation navigation surface papers but leave synthesis and continuation to the researcher
2. **LLM assistants lack provenance** - They summarize and suggest but operate over unstructured text, making outputs difficult to verify or reproduce
3. **Existing knowledge graphs are paper-centric** - They treat papers or claims as atomic units but don't model open research problems or support active continuation
4. **Research workflows remain fragmented** - Manual, difficult to scale, particularly for early-career researchers and interdisciplinary teams

## Solution Approach

An integrated system that supports research progression through three capabilities:

### 1. Structured Knowledge Representation
- Research problems as first-class entities (not just papers/claims)
- Operational context: assumptions, constraints, datasets, metrics, baselines
- Explicit semantic relations: extends, contradicts, reframes, depends-on
- Evidence spans anchored to source papers via DOIs and quoted text

### 2. Automated Extraction with Provenance
- Ingest papers from arXiv, OpenAlex
- LLM-based structured extraction with schema validation
- Hybrid heuristic + LLM pipelines for problem identification
- Confidence scores and provenance tracking throughout

### 3. Agentic Research Progression
- Specialized agents operating over structured graph (not raw text)
- Ranking, continuation, evaluation, and synthesis workflows
- Human-in-the-loop governance at key decision points
- Results written back as new structured artifacts (closed-loop)

## User Experience Goals

### Primary Users

- **Researchers** looking to identify and advance open problems in their field
- **Early-career academics** needing help navigating and contributing to literature
- **Interdisciplinary teams** synthesizing knowledge across domains
- **Research organizations** tracking progress and identifying opportunities

### User Workflows

**Workflow 1: Discover Tractable Problems**
1. Query system with research interest or domain
2. System retrieves ranked open problems with context
3. View assumptions, constraints, and required datasets
4. See related problems (extends, depends-on relationships)
5. Select problem for continuation

**Workflow 2: Propose Research Continuation**
1. Select a prioritized research problem
2. Request continuation agent proposal
3. Review grounded experiment/extension suggestion
4. Approve, modify, or reject proposal
5. Execute evaluation workflow (if approved)

**Workflow 3: Execute and Record Progress**
1. Evaluation agent runs reproducible workflow
2. Results captured in standardized schema
3. Synthesis agent summarizes outcomes
4. New artifacts written back to knowledge graph
5. Graph state updated for future iterations

## Key User Stories

1. **As a researcher**, I want to find open problems with available datasets and clear metrics so that I can identify tractable continuations
2. **As a PhD student**, I want to understand how problems relate to each other so that I can position my work effectively
3. **As a team lead**, I want to track which problems are being worked on and their progress so that I can coordinate efforts
4. **As a skeptical user**, I want to see provenance for all suggestions so that I can verify and trust the system's outputs

## Success Metrics

- **Extraction reliability**: F1 within 10% of inter-annotator agreement
- **Retrieval quality**: MRR and nDCG improvement over keyword/citation baselines
- **Progression utility**: Faster time to actionable continuation, higher feasibility ratings
- **Trust**: Higher user-reported confidence compared to opaque AI assistants
- **Adoption**: Active use by research teams for literature exploration
