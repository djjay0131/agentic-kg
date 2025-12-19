# Agentic Knowledge Graphs for Research Progression

A system for building and maintaining research knowledge graphs with AI agents that model scientific problems as first-class entities.

## Overview

This project implements the concepts from ["Agentic Knowledge Graphs for Research Progression"](https://arxiv.org/abs/your-paper) using [Denario](https://github.com/AstroPilot-AI/Denario) as the core agent framework.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Agentic Orchestration Layer              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Navigator  │  │  Extractor  │  │  Continuation Agent │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                 Automation & Extraction Layer               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Ingestion │  │  Parsing    │  │  Structured Extract │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                Knowledge Representation Layer               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │     Research Knowledge Graph (Problems as Nodes)    │    │
│  │  • Assumptions  • Constraints  • Evidence Spans     │    │
│  │  • Datasets     • Metrics      • Semantic Relations │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Repository Structure

```
agentic-kg/
├── packages/
│   ├── core/           # Knowledge graph logic, extended agents
│   ├── api/            # FastAPI/GraphQL service
│   └── ui/             # Streamlit UI
├── deploy/
│   ├── docker/         # Dockerfiles for each service
│   └── terraform/      # Infrastructure as code
├── memory-bank/        # Project documentation and context
├── construction/       # Active development tracking
└── files/              # Reference materials
```

## Quick Start

```bash
# Clone the repository
git clone https://github.com/djjay0131/agentic-kg.git
cd agentic-kg

# Install dependencies
pip install -e ".[dev]"

# Run the UI (coming soon)
# streamlit run packages/ui/src/app.py
```

## Development

See [memory-bank/](memory-bank/) for project context and documentation.

## License

MIT
