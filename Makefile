.PHONY: install test test-core test-api test-e2e smoke-test lint build-ui dev docker-up docker-down clean

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
