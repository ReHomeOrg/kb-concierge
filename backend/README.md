# kb-concierge — backend

ИИ-агент-оркестратор «Консьерж» reHome: единый ИИ-вход для обращений пользователя —
распознаёт намерение, отвечает из базы знаний (RAG), маршрутизирует в kb-partners /
kb-support, действует автономно **строго в рамках политики**; остальное — человеку
(human-handoff). LLM — рассуждающий дирижёр, но **никогда в критическом пути**
детерминированных операций (деньги/escrow, `access_level`, машины состояний).

**Стек:** FastAPI · PostgreSQL (своя БД) · Dramatiq + Redis · YandexGPT (intent/NLU,
ADR-0003) · Keycloak (JWT + m2m token-exchange). Соседи — только по сети (typed tools,
ADR-0001: без общих таблиц/кода).

## Разработка

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
mypy src tests
pytest -q
bash ../scripts/check-arch-constraint.sh   # AT-001 (арх-константа)
alembic upgrade head                        # PYTHONPATH=src
uvicorn api.main:app --reload
```

Env-префикс — `KBC_*`. Полное ТЗ/ADR — в `docs/`; правила разработки — в `CLAUDE.md`.

> Этот файл обязателен для сборки: `pyproject.toml` (`readme = "README.md"`) и
> `backend/Dockerfile` (`COPY pyproject.toml README.md ./`) его требуют.
