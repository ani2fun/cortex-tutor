# cortex-tutor — dev tasks (best-practice uv workflow)
.PHONY: help install dev test lint fmt gen-models migrate up down

help:
	@grep -E '^[a-zA-Z_-]+:.*?# .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?# "}{printf "  %-14s %s\n", $$1, $$2}'

install:        # sync the venv from pyproject + uv.lock (installs Python 3.12 if needed)
	uv sync

dev:            # run the FastAPI tutor with autoreload on :8000
	uv run uvicorn tutor.app:app --reload --port 8000

test:           # run the test suite
	uv run pytest

lint:           # ruff lint
	uv run ruff check .

fmt:            # ruff format
	uv run ruff format .

gen-models:     # regenerate Pydantic models from the OpenAPI contract (single source of truth)
	uv run datamodel-codegen \
		--input api/tutor-openapi.yaml --input-file-type openapi \
		--output tutor/contract_models.py --output-model-type pydantic_v2.BaseModel \
		--use-standard-collections --use-schema-description

migrate:        # apply Liquibase migrations (via Docker — no local Java needed)
	# NB: no `update` arg — passing it overrides the compose service `command`
	# (which carries --defaults-file/--search-path/--url + `update`), dropping the
	# config; the bare `run` uses the service command as-is.
	docker compose run --rm liquibase

up:             # bring up the local polyglot dev stack
	docker compose up -d

down:           # tear down the local dev stack
	docker compose down
