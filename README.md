# kb-concierge

ИИ-агент-оркестратор экосистемы reHome — **«Консьерж»**. Единый вход для обращений
пользователя: распознаёт намерение, отвечает из KB, маршрутизирует в kb-partners /
kb-support, действует автономно строго в рамках политики, остальное — человеку.

**Принцип:** оркестрация поверх слабо связанных детерминированных модулей; LLM —
дирижёр, но никогда не в критическом пути денег/доступа/FSM (ADR-0001).

- ТЗ: `docs/handoff/01_postanovka/01_TZ_kb_concierge_v1.1.md`
- Контракт (источник истины): `docs/openapi.yaml` (префикс `/api/v1/concierge`)
- Правила разработки: `CLAUDE.md` · ревью: `CLAUDE-REVIEWER.md`

## Dev
```
cd backend && make install            # venv + deps
docker compose up -d postgres         # dev Postgres :5435 (kbc-postgres)
make lint && make typecheck && make test && make arch-check
uvicorn api.main:app --reload         # из backend/, порт 8000
```
