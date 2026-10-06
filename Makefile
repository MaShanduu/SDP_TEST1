# Repo Analysis Tool (RAT) -- common tasks.
# `make run` builds the dashboard and serves API + UI on http://127.0.0.1:8000

.PHONY: help install build test run dev-api dev-web demo docker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install backend (pip) and frontend (npm) dependencies
	python -m pip install -r backend/requirements.txt
	npm --prefix frontend install --no-audit --no-fund

build: ## Build the React dashboard into frontend/dist
	npm --prefix frontend run build

test: ## Run the backend test suite
	cd backend && python -m pytest

run: build ## Build the dashboard, then serve API + UI at :8000
	cd backend && python -m rat.cli serve --port 8000

dev-api: ## Run the API with autoreload on :8000
	cd backend && uvicorn rat.api:app --reload --port 8000

dev-web: ## Run the Vite dev server on :5173 (proxies /api to :8000)
	npm --prefix frontend run dev

demo: ## CLI demo: ingest a repo and walk through the metric definitions
	bash scripts/demo.sh

docker: ## Build and run the whole app with docker compose (UI at :8000)
	docker compose up --build

clean: ## Remove build/cache artifacts (keeps cloned repositories)
	rm -rf frontend/dist backend/.pytest_cache
	find backend -name __pycache__ -type d -exec rm -rf {} +
