"""Config-gating фабрики reader'а онбординг-статуса: боевой только при полном конфиге."""

from __future__ import annotations

import pytest

import api.sessions.dependencies as deps
from api.config import Settings
from api.onboarding.platform_status import PlatformStatusReader
from api.onboarding.status import NullStatusReader, OnboardingStatusReader
from api.sessions.dependencies import get_onboarding_status_reader


def _reader_for(
    monkeypatch: pytest.MonkeyPatch, *, enabled: bool, base_url: str, service_key: str
) -> OnboardingStatusReader:
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: Settings(
            onboarding_platform_status_enabled=enabled,
            platform_api_base_url=base_url,
            platform_service_key=service_key,
        ),
    )
    return get_onboarding_status_reader()


def test_full_config_yields_platform_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader_for(monkeypatch, enabled=True, base_url="http://platform", service_key="k")
    assert isinstance(reader, PlatformStatusReader)


def test_empty_service_key_yields_null(monkeypatch: pytest.MonkeyPatch) -> None:
    # Security: без ключа НЕ шлём в rehome.one → Null (режим ПУТИ).
    reader = _reader_for(monkeypatch, enabled=True, base_url="http://platform", service_key="")
    assert isinstance(reader, NullStatusReader)


def test_disabled_flag_yields_null(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader_for(monkeypatch, enabled=False, base_url="http://platform", service_key="k")
    assert isinstance(reader, NullStatusReader)


def test_missing_base_url_yields_null(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader_for(monkeypatch, enabled=True, base_url="", service_key="k")
    assert isinstance(reader, NullStatusReader)
