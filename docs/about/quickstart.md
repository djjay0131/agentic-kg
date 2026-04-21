---
title: Quickstart
parent: About
nav_order: 6
---

# Quickstart

<!-- TODO(phase-b): validate the commands below end-to-end on a fresh checkout, add a "first extraction" example. -->

```bash
git clone https://github.com/djjay0131/agentic-kg.git
cd agentic-kg

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install
uv sync  # or: pip install -e '.[dev]'

# Run unit tests (excludes live-Neo4j tests)
pytest packages/core/tests/ --ignore=packages/core/tests/e2e -q

# Local stack
docker compose up
```

See `README.md` for the full setup guide.
