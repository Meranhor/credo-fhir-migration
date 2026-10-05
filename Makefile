# Run from an activated virtual environment (see README).
PYTHON ?= python
NPM ?= npm

.PHONY: setup run run-api run-web test lint format

setup:
	$(PYTHON) -m pip install -e ".[dev]"
	$(NPM) --prefix frontend ci
	$(PYTHON) backend/manage.py migrate

run:
	$(MAKE) -j2 run-api run-web

run-api:
	$(PYTHON) backend/manage.py runserver

run-web:
	$(NPM) --prefix frontend run dev

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .
