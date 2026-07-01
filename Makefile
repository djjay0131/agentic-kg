.PHONY: install test test-core test-api test-e2e smoke-test smoke-local lint build-ui dev docker-up docker-down clean

# Install all packages in development mode
install:
	pip install -e packages/core[dev]
	pip install -e packages/api[dev]
	cd packages/ui && npm install

# Run all tests
test: test-core test-api

# Core library tests
test-core:
	pytest packages/core/tests/ -v

# API tests
test-api:
	pytest packages/api/tests/ -v

# E2E tests (requires staging env vars)
test-e2e:
	pytest packages/core/tests/e2e/ packages/api/tests/e2e/ -v -m e2e

# Smoke test against staging
smoke-test:
	python scripts/smoke_test.py

# Local reproduction of the ci-smoke-test-ingestion GHA workflow
# (llm/features/ci-smoke-test-ingestion.md AC-15). Mirrors the workflow
# steps line-for-line: spin a local Neo4j, init schema, ingest, assert.
# Usage: make smoke-local
#        QUERY="custom query" LIMIT=5 make smoke-local
smoke-local:
	@command -v docker >/dev/null || { echo "docker required"; exit 1; }
	@[ -n "$$OPENAI_API_KEY" ] || { echo "OPENAI_API_KEY env required"; exit 1; }
	@docker rm -f smoke-neo4j >/dev/null 2>&1 || true
	@docker run -d --name smoke-neo4j \
		-e NEO4J_AUTH=neo4j/testpassword \
		-e NEO4J_PLUGINS='["apoc"]' \
		-p 7687:7687 -p 7474:7474 \
		neo4j:5.26-community
	@echo "Waiting for Neo4j..."
	@until curl -sf http://localhost:7474 >/dev/null 2>&1; do sleep 2; done
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		python -c "from agentic_kg.knowledge_graph.schema import initialize_schema; initialize_schema(force=True)"
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		agentic-kg ingest \
		--query "$${QUERY:-retrieval augmented generation}" \
		--limit "$${LIMIT:-3}" \
		--json > ingest_result.json
	@NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=testpassword \
		python scripts/smoke_assert.py ingest_result.json
	@docker rm -f smoke-neo4j >/dev/null 2>&1 || true

# Lint (ruff)
lint:
	ruff check packages/core/src packages/api/src
	ruff format --check packages/core/src packages/api/src

# Format
format:
	ruff format packages/core/src packages/api/src

# Build UI
build-ui:
	cd packages/ui && npm run build

# Local dev (API)
dev:
	uvicorn agentic_kg_api.main:app --reload --host 0.0.0.0 --port 8000

# Docker
docker-up:
	docker compose -f docker/docker-compose.yml up -d neo4j
	@echo "Waiting for Neo4j to be healthy..."
	@sleep 10
	docker compose -f docker/docker-compose.yml --profile full up -d

docker-down:
	docker compose -f docker/docker-compose.yml --profile full down

# Clean build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf packages/ui/.next packages/ui/out
