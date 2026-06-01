# Chess Stats Dashboard — Developer Makefile
# Usage: make <target>
#
# Requires: python3, pip, make

VENV      := .venv
PYTHON    := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
PYTEST    := $(VENV)/bin/pytest
RUFF      := $(VENV)/bin/ruff
STUDY     ?= abcdWXYZ

.PHONY: help venv install install-dev run test lint typecheck clean docker docker-up

help:          ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile | awk 'BEGIN{FS=":.*##"}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

venv:          ## Create Python virtual environment
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv  ## Install runtime dependencies
	$(PIP) install -r requirements.txt

install-dev: venv  ## Install runtime + dev dependencies
	$(PIP) install -r requirements-dev.txt

run: install   ## Start the dashboard locally
	$(PYTHON) app.py --study $(STUDY)

run-debug: install  ## Start with hot-reload debug mode
	$(PYTHON) app.py --study $(STUDY) --debug

test: install-dev  ## Run the test suite
	$(PYTEST) tests/ -v --cov=pgn_stats_core --cov=lichess_client --cov=sync --cov=data --cov=config --cov-report=term-missing

lint: install-dev  ## Lint with ruff
	$(RUFF) check . --fix

typecheck: install-dev  ## Type check with mypy
	$(VENV)/bin/mypy pgn_stats_core.py lichess_client.py sync.py data.py config.py

clean:         ## Remove virtual environment and caches
	rm -rf $(VENV) __pycache__ .pytest_cache .mypy_cache .ruff_cache

docker:        ## Build the Docker image
	docker build -t chess-stats .

docker-up:     ## Run the dashboard in Docker
	docker compose up --build
