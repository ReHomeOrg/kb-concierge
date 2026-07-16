# ADR-0004 — Делегированная авторизация исходящих вызовов (OAuth2 token-exchange)

- Статус: **Принято** (2026-06-12) — approve Архитектора (Evgeniy) 2026-06-12
- Контекст: ТЗ «Консьерж» v1.1 §8/§9, FR-9.7, guardrails G2/G7; Э0 находка CC-1; CLAUDE.md правило 6
- Связанные: ADR-0001 (арх-константа), kb-partners ADR-0005 (token-exchange), `docs/E0-contract-reconciliation.md`

## Контекст

Сверка контрактов Э0 выявила сквозной BLOCKER **CC-1**: Консьерж передавал делегирование
прав пользователя HTTP-заголовком `X-On-Behalf-Of`, но **ни один сосед его не читает**.
Делегирование (on-behalf-of, FR-9.7) у соседей (`kb-partners`/`kb-support`/`kb-search`)
ожидается через **claim в Bearer-токене** (token-exchange; у kb-partners это `kbp_act_sub`).
Из-за этого агент фактически действовал как сервис-принципал без привязки к пользователю —
downstream применял бы права агента, а не пользователя (нарушение G2/G7).

До настоящего ADR в Консьерже был только `StaticTokenProvider` (dev/test); боевой OAuth2
был осознанно отложен «под ADR» (комментарий в `clients/auth.py`, правило 6).

## Решение

### 1. Делегирование — в самом токене, не заголовком

Убрать `X-On-Behalf-Of` из всех адаптеров (`clients/{partners,support,platform,search}`).
Право пользователя передаётся **в Bearer-токене**, полученном через RFC 8693 token-exchange.

### 2. Боевые OAuth2-провайдеры (свой HTTP-адаптер, без SDK)

`api/clients/oauth.py` (портирован из kb-partners ADR-0005):
- `ClientCredentialsTokenProvider` — m2m-токен сервис-принципала агента
  (`grant_type=client_credentials`), кеш до истечения (минус запас 30 с).
- `TokenExchangeProvider` — `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`,
  `requested_subject = <sub пользователя>` → делегированный токен.

`api/clients/auth.py`:
- Контракт `TokenProvider.get_token(on_behalf_of: str | None = None)`.
- `OAuth2TokenProvider` объединяет m2m + exchange: `get_token(None)` → m2m,
  `get_token(<sub>)` → делегированный.
- `build_token_provider` отдаёт боевой `OAuth2TokenProvider` при заполненном oauth-конфиге
  (`oauth_token_url`/`oauth_client_id`/`oauth_client_secret`), иначе `StaticTokenProvider`.

### 3. Деградация без отката на m2m (критично для G2/G7)

Сбой Keycloak / token-exchange → `ExternalServiceError`. Адаптеры получают токен **внутри
`try`**, поэтому сбой деградирует в `unavailable` (FR-6.6, G6) — ход не падает. **Отката на
m2m-токен при неудаче обмена НЕТ**: иначе агент действовал бы шире прав пользователя.

## Последствия

- Делегирование работает на стороне соседей (они читают actor-claim токена); мёртвый
  заголовок устранён.
- Включение боевого режима — конфигурацией (`KBC_OAUTH_*` + креды kb-vault); код готов,
  тесты — на `httpx.MockTransport` (без сети). Живая проверка — при наличии Keycloak-realm
  и кредов (ops, как YandexGPT ADR-0003).
- **Realm-конфиг — зона ops:** имена actor-claim (`kbp_act_sub` и аналоги), аудитория токена
  per-downstream и политики token-exchange настраиваются в Keycloak. ~~Per-audience scoping
  (`audience` в exchange) — следующий шаг при провижининге realm, на контракт кода не влияет.~~
  → *см. Дополнение (2026-07-16): per-audience ПОТРЕБОВАЛ правки кода.*
- Закрывает CC-1 у partners/support/search (issue #12). rehome.one — после получения
  контракта (issue #16).

## Альтернативы (отклонены)

- **Оставить `X-On-Behalf-Of`** — соседи не читают; делегирование не работает (G2/G7).
- **Откат на m2m при сбое обмена** — агент получил бы права шире пользовательских (нарушение G2/G7).
- **Вендорский OAuth-SDK** — лишняя зависимость, правило 6; свой HTTP-адаптер проще тестировать.

## Дополнение (2026-07-16) — per-audience реализован в коде (PR #39)

Ревью CC-1 (finding B) показало: исходно `TokenExchangeProvider.exchange()` слал только
`requested_subject`, БЕЗ `audience`/`scope`. Без `audience` в запросе Keycloak проставляет
`aud` только из хардкод-мапперов клиента `kb-concierge-m2m` → обменянный делегированный токен
несёт ВСЕ аудитории и **реиграбелен между соседями** (replay). Поэтому исходная оценка
«per-audience на контракт кода не влияет» — **неверна**.

Реализовано (PR #39): `exchange()` кладёт `audience` в запрос при непустом значении;
`build_token_provider(…, audience)` пробрасывает его; `config.py` — карта сосед→audience
(`oauth_audience_kb_support`/`_kb_partners`/`_kb_search`/`_platform`, override `KBC_OAUTH_AUDIENCE_*`);
audience задаётся per-downstream в точках сборки провайдера (reasoning/handoff). Пусто →
прежнее поведение (обратная совместимость с dev `StaticTokenProvider`).

Остаётся зоной ops (без влияния на код): аудитория-мапперы соседей + **token-exchange
permission на каждый aud** — должны настраиваться **синхронно** с боевым включением, иначе
Keycloak вернёт 400 на обмене → деградация в `unavailable` (fail-closed). Имя actor-claim
(`kbp_act_sub` vs стандарт RFC 8693 `act.sub`) — открытый кросс-командный вопрос.
