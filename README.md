# Agentic Knowledge Graphs for Research Progression

A system for building and maintaining research knowledge graphs with AI agents that model scientific problems as first-class entities.

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://djjay0131.github.io/agentic-kg/)
[![API Status](https://img.shields.io/badge/API-Healthy-success)](https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/health)

## 🌐 Live Services

- **📚 Documentation:** [https://djjay0131.github.io/agentic-kg/](https://djjay0131.github.io/agentic-kg/)
- **🔌 API:** [https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app](https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app)
- **🎨 UI Dashboard:** [https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app](https://agentic-kg-ui-staging-tqpsba7pza-uc.a.run.app)
- **🔬 Denario App:** [https://denario-app-tqpsba7pza-uc.a.run.app](https://denario-app-tqpsba7pza-uc.a.run.app)

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
├── llm/
│   ├── memory_bank/    # Project documentation and context
│   └── features/       # Feature specs + master catalog (BACKLOG.md)
├── construction/       # Historical design + sprint archive (read-only)
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

## Documentation

- **📖 Main Docs:** [GitHub Pages](https://djjay0131.github.io/agentic-kg/)
- **📋 Service Inventory:** [docs/status/service-inventory.md](docs/status/service-inventory.md)
- **🏗️ Architecture:** See [construction/sprints/](construction/sprints/) for detailed design docs
- **💾 Project Context:** [llm/memory_bank/](llm/memory_bank/) for active development tracking
- **📌 Feature Catalog:** [llm/features/BACKLOG.md](llm/features/BACKLOG.md) — every spec + status

## API Documentation

- **OpenAPI Spec:** [/docs](https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/docs)
- **ReDoc:** [/redoc](https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/redoc)
- **Health Check:** [/health](https://agentic-kg-api-staging-tqpsba7pza-uc.a.run.app/health)

## Development

See [llm/memory_bank/](llm/memory_bank/) for project context and documentation.

### Local Development

```bash
# Start the API
cd packages/api
uvicorn agentic_kg.api.main:app --reload

# Start the UI
cd packages/ui
npm run dev
```

### Running Tests

```bash
# Run unit tests
pytest packages/core/tests/ --ignore=packages/core/tests/e2e

# Run smoke test against staging
make smoke-test
```

## Contributing

We use a sprint-based development process. See [construction/sprints/](construction/sprints/) for active work.

## License

MIT
