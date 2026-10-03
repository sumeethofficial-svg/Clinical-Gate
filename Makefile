# ClinicalGate - SYNTHETIC DATA ONLY
SHELL := /bin/bash
-include .env
export

PY ?= python

.PHONY: help up down logs install migrate seed run test lint eval eval-real demo-data readme clean

help:
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

up: ## docker compose: Postgres + API + UI with seeded data  ->  http://localhost:8000
	docker compose up --build

down: ## stop and delete containers + volume
	docker compose down -v

install: ## local venv + pinned deps
	$(PY) -m venv .venv && .venv/bin/pip install -r requirements.lock

migrate: ## apply migrations + create per-persona logins (needs DATABASE_URL, CG_APP_PASSWORD)
	$(PY) -m db.migrate

seed: ## (re)generate deterministic synthetic data
	$(PY) -m db.seed.generate

run: ## local dev server (needs a migrated+seeded database)
	$(PY) -m uvicorn app.main:app --reload --port 8000

lint: ## ruff
	ruff check .

test: ## pytest (needs Postgres; DATABASE_URL points at a DISPOSABLE database)
	$(PY) -m pytest -q

eval: ## deterministic red-team + legitimate-task suite with the mock LLM; fails on any leak
	$(PY) -m evals.runner --tier all --llm mock && $(PY) -m scripts.update_readme

eval-real: ## LLM-in-the-loop suite against the real provider (LLM_PROVIDER + API key); writes evals/report.md
	$(PY) -m evals.runner --tier llm --llm real --out evals/report.md && $(PY) -m scripts.update_readme

readme: ## refresh the README results table from evals/results.*.json
	$(PY) -m scripts.update_readme

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__
