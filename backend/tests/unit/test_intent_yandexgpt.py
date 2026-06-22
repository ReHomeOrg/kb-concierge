"""Юнит-тесты боевого LLM-провайдера intent-роутера YandexGPT (E5, ADR-0003).

Без сети (`httpx.MockTransport`). Проверяют разбор строгого JSON, очистку code-fences,
clamp confidence, перенос слотов и деградацию в `None` (FR-6.6) на любой нештатный ответ.
"""

from __future__ import annotations

import httpx
import pytest

from api.config import Settings
from api.intent.enums import Intent
from api.intent.provider import NullLLMProvider
from api.intent.yandexgpt import _SYSTEM_PROMPT, YandexGptProvider, build_llm_provider

_CONFIGURED = Settings(
    intent_llm_provider="yandexgpt", yandexgpt_api_key="k", yandexgpt_folder_id="f"
)


def _completion(text: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json={"result": {"alternatives": [{"message": {"text": text}}]}})

    return httpx.MockTransport(handler)


async def test_parses_strict_json_completion() -> None:
    transport = _completion('{"intent": "SUPPORT_ISSUE", "confidence": 0.92, "slots": {}}')
    result = await YandexGptProvider(_CONFIGURED, transport=transport).classify("сосед затопил")
    assert result is not None
    assert result.intent is Intent.SUPPORT_ISSUE
    assert result.confidence == pytest.approx(0.92)
    assert result.model == "yandexgpt-lite"
    assert result.version == "v1"


async def test_strips_code_fences() -> None:
    transport = _completion('```json\n{"intent": "PARTNER_SERVICE", "confidence": 0.8}\n```')
    result = await YandexGptProvider(_CONFIGURED, transport=transport).classify("нужен клининг")
    assert result is not None
    assert result.intent is Intent.PARTNER_SERVICE


async def test_strips_single_line_code_fences() -> None:
    # NIT #1: fenced-JSON без переноса строки тоже должен разбираться (не теряться).
    transport = _completion('```json{"intent": "SMALL_TALK", "confidence": 0.6}```')
    result = await YandexGptProvider(_CONFIGURED, transport=transport).classify("спасибо")
    assert result is not None
    assert result.intent is Intent.SMALL_TALK


async def test_carries_slots() -> None:
    transport = _completion('{"intent": "INFO_QA", "confidence": 0.7, "slots": {"topic": "lease"}}')
    result = await YandexGptProvider(_CONFIGURED, transport=transport).classify("как продлить")
    assert result is not None
    assert result.slots == {"topic": "lease"}


async def test_confidence_clamped() -> None:
    transport = _completion('{"intent": "INFO_QA", "confidence": 1.5}')
    result = await YandexGptProvider(_CONFIGURED, transport=transport).classify("вопрос")
    assert result is not None and result.confidence == 1.0


async def test_unparseable_completion_degrades_to_none() -> None:
    provider = YandexGptProvider(_CONFIGURED, transport=_completion("это не json"))
    assert await provider.classify("текст") is None


async def test_http_error_degrades_to_none() -> None:
    provider = YandexGptProvider(_CONFIGURED, transport=_completion("{}", status=500))
    assert await provider.classify("текст") is None


async def test_unknown_intent_degrades_to_none() -> None:
    transport = _completion('{"intent": "FLYING", "confidence": 0.9}')
    assert await YandexGptProvider(_CONFIGURED, transport=transport).classify("x") is None


def test_build_provider_inert_by_default() -> None:
    assert isinstance(build_llm_provider(Settings()), NullLLMProvider)
    # Имя без кредов → тоже инертно (fail-safe).
    assert isinstance(
        build_llm_provider(Settings(intent_llm_provider="yandexgpt")), NullLLMProvider
    )
    # Нереализованное имя → Null.
    assert isinstance(build_llm_provider(Settings(intent_llm_provider="gigachat")), NullLLMProvider)


def test_build_provider_yandexgpt_when_configured() -> None:
    assert isinstance(build_llm_provider(_CONFIGURED), YandexGptProvider)


def test_system_prompt_carries_boundary_few_shot() -> None:
    # Few-shot на спорных границах — регрессия точности классификации (не удалять молча).
    assert "Примеры" in _SYSTEM_PROMPT
    assert "верните деньги" in _SYSTEM_PROMPT  # жалоба + услуга → SUPPORT_ISSUE
    assert "ремонт смесителя" in _SYSTEM_PROMPT  # услуга без жалобы → PARTNER_SERVICE
    assert _SYSTEM_PROMPT.count("→") >= 6  # минимум 6 размеченных примеров
    # Без ПДн в промпте (G3): только классы и обобщённые формулировки.
    assert "@" not in _SYSTEM_PROMPT
