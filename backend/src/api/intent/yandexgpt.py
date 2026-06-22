"""Боевой LLM-провайдер intent-роутера — YandexGPT (E5, FR-5.2, ADR-0003).

«Разрабатываем сами» (CLAUDE.md правило 6): свой HTTP-адаптер поверх
`ResilientHttpClient` (без вендорского SDK), Api-Key-авторизация, переключение по env.
На вход модели идёт ТОЛЬКО маскированный текст (ПДн вырезаны до LLM, guardrails G3/G4) —
текст в логи не пишется. Контент недоверенный: системный промпт фиксирован, инструкции
из пользовательского текста не исполняются (анти-prompt-injection, G4).

Деградация (FR-6.6): недоступность/4xx/битый ответ/неизвестное намерение → `None`
(движок уходит на rules-путь, см. `IntentClassifier`). Трассировка (model/version)
сохраняется в `LLMIntent`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from api.clients.errors import ExternalServiceError
from api.clients.factory import build_resilient_client
from api.config import Settings
from api.intent.enums import Intent
from api.intent.provider import LLMIntent, LLMProvider, NullLLMProvider
from api.observability.logging import get_logger

_logger = get_logger("intent.yandexgpt")

_COMPLETION_PATH = "/foundationModels/v1/completion"

# Версия КОНТРАКТА ПАРСЕРА адаптера (трасса FR-5.4), не версия модели — модель в
# `LLMIntent.model` (`yandexgpt_model`). Меняется при изменении формата разбора ответа.
_PARSER_VERSION = "v1"

# Инструкция модели: классификация намерения (Intent, §5) + строгий JSON. Содержит
# только имена классов — без ПДн и без бизнес-данных.
_SYSTEM_PROMPT = (
    "Ты маршрутизатор обращений пользователя в экосистеме аренды жилья. Определи класс "
    "обращения по тексту. Классы: INFO_QA (вопрос/информация, ответ из базы знаний), "
    "PARTNER_SERVICE (заявка на бытовую услугу: уборка, ремонт, переезд, ключи), "
    "SUPPORT_ISSUE (проблема, жалоба, претензия, требование вернуть деньги), "
    "NON_STANDARD (нестандартная/неоднозначная ситуация, не подходящая под прочие классы), "
    "SMALL_TALK (приветствие/благодарность), OUT_OF_SCOPE (вне области экосистемы). "
    "Правила приоритета на границах: если есть жалоба/претензия/требование вернуть деньги — "
    "это SUPPORT_ISSUE, даже когда упомянута услуга; запрос НА выполнение бытовой услуги без "
    "жалобы — PARTNER_SERVICE; вопрос «как/можно ли/какие документы» — INFO_QA. "
    "Примеры (вход → выход):\n"
    '"Ремонт сделали плохо, верните деньги" → {"intent":"SUPPORT_ISSUE","confidence":0.9}\n'
    '"Нужен ремонт смесителя в ванной" → {"intent":"PARTNER_SERVICE","confidence":0.9}\n'
    '"Можно ли продлить договор онлайн?" → {"intent":"INFO_QA","confidence":0.9}\n'
    '"Добрый день, спасибо за помощь" → {"intent":"SMALL_TALK","confidence":0.9}\n'
    '"Досрочно расторгнуть договор и забрать депозит на особых условиях" → '
    '{"intent":"NON_STANDARD","confidence":0.6}\n'
    '"Какая завтра погода?" → {"intent":"OUT_OF_SCOPE","confidence":0.9}\n'
    "Ответь СТРОГО одним JSON-объектом без пояснений: "
    '{"intent": "<КЛАСС>", "confidence": <0..1>, "slots": {}}'
)


class YandexGptProvider:
    """`LLMProvider` поверх YandexGPT Foundation Models API.

    `transport` инъектируется в тестах (`httpx.MockTransport`) — боевой путь сети не
    трогает.
    """

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._s = settings
        self._transport = transport

    @property
    def _model_uri(self) -> str:
        return f"gpt://{self._s.yandexgpt_folder_id}/{self._s.yandexgpt_model}/latest"

    def _build_body(self, masked_text: str) -> dict[str, Any]:
        return {
            "modelUri": self._model_uri,
            "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 200},
            "messages": [
                {"role": "system", "text": _SYSTEM_PROMPT},
                {"role": "user", "text": masked_text},
            ],
        }

    async def classify(self, masked_text: str) -> LLMIntent | None:
        headers = {
            "Authorization": f"Api-Key {self._s.yandexgpt_api_key}",
            "x-folder-id": self._s.yandexgpt_folder_id,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._s.yandexgpt_api_base_url,
                timeout=self._s.client_timeout_seconds,
                transport=self._transport,
            ) as http:
                client = build_resilient_client("yandexgpt", http, self._s)
                response = await client.request(
                    "POST",
                    _COMPLETION_PATH,
                    operation="classify",
                    headers=headers,
                    json=self._build_body(masked_text),
                )
        except ExternalServiceError as exc:
            _logger.warning("yandexgpt degraded: %s", type(exc).__name__)
            return None
        if response.status_code >= 400:
            _logger.warning("yandexgpt degraded: status=%d", response.status_code)
            return None
        return self._parse(response.json())

    def _parse(self, payload: Any) -> LLMIntent | None:
        """Распарсить ответ YandexGPT → LLMIntent. Любая ошибка формата → None."""
        try:
            alternatives = payload["result"]["alternatives"]
            text = alternatives[0]["message"]["text"]
            data = json.loads(_strip_fences(text))
            intent = Intent(str(data["intent"]).upper())
            confidence = float(data["confidence"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            _logger.warning("yandexgpt degraded: unparseable completion")
            return None
        slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
        return LLMIntent(
            intent=intent,
            confidence=max(0.0, min(1.0, confidence)),
            slots=slots,
            model=self._s.yandexgpt_model,
            version=_PARSER_VERSION,
        )


def _strip_fences(text: str) -> str:
    """Убрать ```json ... ``` обёртку, если модель её добавила.

    Робастно к однострочному варианту (` ```json{...}``` ` без переноса): снимаем
    открывающие/закрывающие бэктики и опц. язык-метку независимо от `\\n`.
    """
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    cleaned = cleaned[3:]  # открывающие бэктики
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]  # закрывающие бэктики
    return cleaned.strip().removeprefix("json").strip()


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Собрать LLM-провайдера intent-роутера по конфигурации (env-switch, ADR-0003).

    `yandexgpt` + заполненные api_key/folder_id → `YandexGptProvider`; иначе (включая
    пустые креды) — `NullLLMProvider` (rules-only, fail-safe). Прочие имена зарезервированы
    под будущие ADR (gigachat/vllm) и пока резолвятся в Null.
    """
    name = settings.intent_llm_provider.strip().lower()
    if name == "yandexgpt" and settings.yandexgpt_api_key and settings.yandexgpt_folder_id:
        return YandexGptProvider(settings)
    return NullLLMProvider()
