# Makefile для kb-concierge backend. Все цели запускаются из директории backend/.
.PHONY: help install lint format typecheck test test-cov dev docker-build clean \
        db-up db-down db-logs migrate migrate-down revision arch-check

PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PYBIN  := $(VENV)/bin

help: ## Показать список доступных целей
	@awk 'BEGIN {FS = ":.*##"; printf "Доступные цели:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate ## Установить зависимости (runtime + dev)
	$(PIP) install -e ".[dev]"

lint: ## ruff check + ruff format --check
	$(PYBIN)/ruff check src tests
	$(PYBIN)/ruff format --check src tests

format: ## ruff format (in-place)
	$(PYBIN)/ruff format src tests
	$(PYBIN)/ruff check --fix src tests

typecheck: ## mypy --strict
	$(PYBIN)/mypy src tests

test: ## pytest без coverage
	$(PYBIN)/pytest

test-cov: ## pytest + coverage (CI target)
	$(PYBIN)/pytest --cov=src/api --cov-report=term-missing --cov-report=xml --cov-fail-under=80

dev: ## uvicorn с auto-reload (localhost:8000)
	$(PYBIN)/uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

docker-build: ## Собрать Docker-образ
	docker build -t kb-concierge-backend:dev .

clean: ## Удалить кеши и venv
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
db-up: ## Поднять Postgres (docker compose)
	docker compose up -d postgres

db-down: ## Остановить Postgres (volume сохраняется)
	docker compose stop postgres

db-logs: ## Логи Postgres
	docker compose logs -f postgres

migrate: ## Применить миграции до head
	$(PYBIN)/alembic upgrade head

migrate-down: ## Откатить одну миграцию
	$(PYBIN)/alembic downgrade -1

revision: ## Создать новую миграцию: make revision m="add service_requests"
	@if [ -z "$$m" ]; then echo "Usage: make revision m=\"<message>\""; exit 1; fi
	$(PYBIN)/alembic revision --autogenerate -m "$$m"

# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
arch-check: ## AT-001 — проверка архитектурной константы (см. CLAUDE.md правило 7)
	@bash ../scripts/check-arch-constraint.sh
	@bash ../tests/arch-constraint/test_runner.sh
