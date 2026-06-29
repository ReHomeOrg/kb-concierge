"""Application settings via pydantic-settings (kb-concierge).

Все настройки — из env (или `.env` для local dev). Префикс: `KBC_*`.

M0 (каркас): DB, observability, Keycloak-валидатор (agent SP + on-behalf-of),
resilience-параметры HTTP-клиентов, worker/брокер. Доменные настройки (LLM-провайдер
intent-router, base-URL соседей-инструментов, TTL сессий, bounded-loop лимиты,
rate-limit) добавляются по мере эпиков M1+.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальные настройки сервиса."""

    model_config = SettingsConfigDict(
        env_prefix="KBC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- База данных (своя, не shared — арх-константа ADR-0001) ---
    database_url: str = Field(
        default="postgresql+asyncpg://kbconcierge:devpass@localhost:5435/kbconcierge",
        description=(
            "PostgreSQL async DSN (asyncpg). TLS на этом этапе не enforce'ится; "
            "для prod добавить sslmode=require + sslrootcert через query string."
        ),
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_pool_max_overflow: int = Field(default=20, ge=0, le=200)
    database_echo: bool = Field(default=False, description="SQLAlchemy echo. В prod — False.")
    log_level: str = Field(default="INFO", description="Уровень JSON-логирования.")

    # --- Ретенция ПДн / сессий (NFR-5, ФЗ-152 right-to-forget). Конфигурируемая
    # политика; фактический cleanup-воркер — отдельный эпик. ---
    session_anon_ttl_days: int = Field(
        default=7, ge=1, description="TTL анонимной сессии (дни) до обезличивания/удаления."
    )
    session_auth_ttl_days: int = Field(
        default=90, ge=1, description="TTL авторизованной сессии (дни)."
    )

    # --- Rate-limit публичного входа (NFR-12, анти-абьюз). Свой токен-бакет
    # in-memory (без новых зависимостей); ключ — пользователь/анон-сессия. ---
    rate_limit_enabled: bool = Field(
        default=True, description="Включить лимит на POST /sessions/{id}/messages."
    )
    rate_limit_capacity: int = Field(
        default=30, ge=1, description="Ёмкость бакета (burst-лимит реплик)."
    )
    rate_limit_refill_per_minute: int = Field(
        default=30, ge=1, description="Скорость пополнения (устойчивый лимит реплик/мин)."
    )

    # --- Intent Router (E5). Порог уверенности: ниже → clarify/handoff (M5+). Выбор
    # LLM-провайдера; `null` → инертный (только rules). Боевой YandexGPT — ADR-0003. ---
    intent_confidence_threshold: float = Field(
        default=0.75, ge=0.0, le=1.0, description="Порог уверенности маршрутизации (ТЗ Док.2)."
    )
    intent_llm_provider: str = Field(
        default="null", description="Провайдер распознавания намерения: null|yandexgpt."
    )
    # YandexGPT — боевой LLM intent-роутера (ADR-0003). РФ, ФЗ-152. Свой HTTP-адаптер
    # (Api-Key), без вендорского SDK. ПУСТОЙ api_key/folder → провайдер инертен (rules-
    # only). На вход модели идёт ТОЛЬКО маскированный текст (ПДн вырезаны до LLM, G3/G4).
    yandexgpt_api_base_url: str = Field(
        default="https://llm.api.cloud.yandex.net",
        description="Базовый URL YandexGPT Foundation Models API.",
    )
    yandexgpt_api_key: str = Field(
        default="", description="Api-Key YandexGPT (ссылка на kb-vault). ПУСТО → провайдер инертен."
    )
    yandexgpt_folder_id: str = Field(
        default="", description="Идентификатор каталога Yandex Cloud (x-folder-id)."
    )
    yandexgpt_model: str = Field(
        default="yandexgpt-lite",
        description="Имя модели YandexGPT (modelUri=gpt://<folder>/<model>/latest).",
    )

    # --- Reasoning Loop (E6, bounded FR-6.2): лимиты на ход. ---
    loop_max_tool_calls: int = Field(default=3, ge=0, le=20, description="Макс. tool-вызовов/ход.")
    loop_max_steps: int = Field(default=5, ge=1, le=50, description="Макс. шагов цикла/ход.")
    loop_token_budget: int = Field(default=4000, ge=1, description="Бюджет токенов (с боевым LLM).")

    # --- Keycloak Bearer JWT. Пустой auth_jwks_url → auth не сконфигурирован
    # (fail-closed 401). aud токена фронта/агента ДОЛЖЕН содержать `kb-concierge`. ---
    auth_jwks_url: str = Field(default="", description="URL JWKS Keycloak.")
    auth_issuer: str = Field(default="", description="Ожидаемый iss (пусто → не проверять).")
    auth_audience: str = Field(default="", description="Ожидаемый aud (пусто → не проверять).")
    auth_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    auth_leeway: int = Field(default=0, ge=0, description="Допуск (сек) для exp/nbf.")
    auth_jwks_cache_ttl: int = Field(default=300, ge=1, description="TTL кеша JWKS (сек).")

    # --- OAuth2 для исходящих вызовов инструментов (m2m + token-exchange on-behalf-of,
    # G7/FR-9.7). ПУСТЫЕ креды → dev StaticTokenProvider. Секрет — ссылкой на kb-vault. ---
    oauth_token_url: str = Field(default="", description="Keycloak token-endpoint. ПУСТО → dev.")
    oauth_client_id: str = Field(default="", description="client_id сервис-принципала агента.")
    oauth_client_secret: str = Field(default="", description="client_secret (kb-vault).")

    # --- HTTP-клиенты к соседям-инструментам (resilience). Базовые URL — по мере
    # подключения инструментов (M3+). Параметры устойчивости общие (NFR-9). ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis для кеша HTTP-клиентов и брокера. Недоступен → кеш off.",
    )
    client_timeout_seconds: float = Field(default=5.0, gt=0)
    client_retry_attempts: int = Field(default=3, ge=1, le=10)
    client_retry_base_delay: float = Field(default=0.1, gt=0)
    client_retry_max_delay: float = Field(default=2.0, gt=0)
    client_breaker_failure_threshold: int = Field(default=5, ge=1)
    client_breaker_reset_timeout: float = Field(default=30.0, gt=0)
    client_cache_ttl_seconds: int = Field(default=60, ge=1)

    # --- Соседи-инструменты (§8). Base URL + fallback-токен (dev/test; боевой токен —
    # через OAuth2 по ADR). ПУСТОЙ base_url → инструмент инертен/деградирует. ---
    kb_search_api_base_url: str = Field(default="", description="Base URL kb-search (RAG).")
    kb_search_api_token: str = Field(default="", description="Fallback-токен kb-search (dev).")
    # K-4 #15: RAG-ответ (синтез) через chat-роут kb-search. ПО УМОЛЧАНИЮ ВЫКЛ — INFO_QA
    # отвечает детерминированно цитатами kb.search; включение даёт LLM-синтез (kb.answer).
    kb_rag_answer_enabled: bool = Field(
        default=False, description="INFO_QA через RAG-синтез (kb.answer) вместо цитат kb.search."
    )
    platform_api_base_url: str = Field(default="", description="Base URL rehome.one (контекст).")
    platform_api_token: str = Field(default="", description="Fallback-токен rehome.one (dev).")
    kb_support_api_base_url: str = Field(
        default="", description="Base URL kb-support (эскалация/тикеты)."
    )
    kb_support_api_token: str = Field(default="", description="Fallback-токен kb-support (dev).")
    kb_partners_api_base_url: str = Field(
        default="", description="Base URL kb-partners (партнёрские заявки)."
    )
    kb_partners_api_token: str = Field(default="", description="Fallback-токен kb-partners (dev).")

    # --- Dramatiq-воркер (durable исходящие события, фоновые задачи). ПУСТОЙ
    # broker_url → StubBroker, акторы инертны (broker/worker поднимает ops). ---
    worker_broker_url: str = Field(default="", description="Redis-broker Dramatiq. ПУСТО → Stub.")
    # --- Исходящие webhooks событий агента (§10, M8). ПУСТОЙ subscriber_url → события
    # не публикуются (outbox не наполняется). Секрет HMAC — ссылкой на kb-vault. ---
    webhook_subscriber_url: str = Field(
        default="", description="URL подписчика событий агента. ПУСТО → webhooks off."
    )
    webhook_hmac_secret: str = Field(default="", description="Секрет HMAC-подписи (kb-vault).")
    outbox_batch_size: int = Field(default=50, ge=1, le=500)
    outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    outbox_retry_base_seconds: float = Field(default=30.0, gt=0)
    outbox_visibility_timeout_seconds: float = Field(default=300.0, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached Settings instance (один объект на процесс)."""
    return Settings()
