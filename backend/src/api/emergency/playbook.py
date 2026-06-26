"""Загрузчик плейбука аварийных ситуаций (правится БЕЗ кода).

Триггеры и тексты вынесены в редактируемый `playbook_data.json` рядом с модулем — их можно
менять без правки Python (изменения проверяются golden-тестами). Ops может указать свой файл
через `KBC_EMERGENCY_PLAYBOOK_PATH` (битый override → откат на встроенный, FR-6.6; битый
встроенный — ошибка сборки, fail-fast, чтобы аварийный модуль не «молча» сломался).

Логика остаётся детерминированной (rules-first, без LLM и интернет-поиска). Вход в
классификатор — ТОЛЬКО маскированный текст (G3). Граница: проблемы ВНУТРИ помещения по
контракту; общедомовое (лифт/дом-газ) — к ответственной службе, заявку не создаём.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.emergency.constants import PARTNER_CREATE, PARTNER_NONE, PARTNER_OFFER

_logger = logging.getLogger(__name__)
_UK = "{uk}"  # плейсхолдер контакта управляющей организации в `contacts`
_VALID_MODES = frozenset({PARTNER_NONE, PARTNER_OFFER, PARTNER_CREATE})
_BUNDLED_PATH = Path(__file__).with_name("playbook_data.json")
#: env-переменная ops-override пути к файлу плейбука (необязательна).
_OVERRIDE_ENV = "KBC_EMERGENCY_PLAYBOOK_PATH"


@dataclass(frozen=True)
class PlaybookEntry:
    """Карточка реакции на тип аварии (из `playbook_data.json`)."""

    type: str
    scope: str
    headline: str  # тип-специфичная вводная
    steps: tuple[str, ...]  # конкретные пошаговые действия по безопасности
    call_line: str  # контекстная строка перед контактами
    contacts: tuple[str, ...]  # строки «кому звонить»; `{uk}` → контакт УК
    partner_mode: str  # NONE/OFFER/CREATE
    repair_subcategory: str  # предзаполнение REPAIR-флоу ("" — нет)
    partner_question: str  # вопрос про заявку ("" для NONE)


@dataclass(frozen=True)
class _Playbook:
    """Разобранный и провалидированный плейбук."""

    entries: dict[str, PlaybookEntry]
    triggers: tuple[tuple[str, tuple[str, ...]], ...]  # (тип, стеммы) в порядке приоритета
    info_suppress: tuple[str, ...]
    uk_with: str  # шаблон строки УК с телефоном ({contact})
    uk_without: str  # обобщённая формулировка без телефона


def _parse(data: Any) -> _Playbook:
    """Провалидировать JSON и собрать плейбук. Некорректная структура → ValueError."""
    if not isinstance(data, dict):
        raise ValueError("playbook root must be an object")
    entries: dict[str, PlaybookEntry] = {}
    triggers: list[tuple[str, tuple[str, ...]]] = []
    for item in data["types"]:
        type_ = str(item["type"])
        mode = str(item["partner_mode"])
        if mode not in _VALID_MODES:
            raise ValueError(f"invalid partner_mode for {type_}: {mode}")
        entries[type_] = PlaybookEntry(
            type=type_,
            scope=str(item["scope"]),
            headline=str(item["headline"]),
            steps=tuple(str(s) for s in item.get("steps", [])),
            call_line=str(item["call_line"]),
            contacts=tuple(str(c) for c in item["contacts"]),
            partner_mode=mode,
            repair_subcategory=str(item.get("repair_subcategory", "")),
            partner_question=str(item.get("partner_question", "")),
        )
        triggers.append((type_, tuple(str(t).lower() for t in item.get("triggers", []))))
    if not entries:
        raise ValueError("playbook has no types")
    uk = data.get("uk_line", {})
    return _Playbook(
        entries=entries,
        triggers=tuple(triggers),
        info_suppress=tuple(str(s) for s in data.get("info_suppress", [])),
        uk_with=str(uk["with_contact"]),
        uk_without=str(uk["without_contact"]),
    )


def load_playbook(path: str | None = None) -> _Playbook:
    """Загрузить плейбук: из `path` (ops-override) либо из встроенного файла.

    Битый override → предупреждение + откат на встроенный (FR-6.6). Битый встроенный →
    исключение (fail-fast: аварийный модуль не должен «молча» остаться без правил).
    """
    if path:
        try:
            return _parse(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("emergency playbook override invalid (%s) → встроенный", exc)
    return _parse(json.loads(_BUNDLED_PATH.read_text(encoding="utf-8")))


_DATA = load_playbook(os.getenv(_OVERRIDE_ENV) or None)
#: Карточки плейбука по типу (совместимость: используется в коде/тестах).
PLAYBOOK: dict[str, PlaybookEntry] = _DATA.entries


def classify_emergency(masked_text: str) -> str | None:
    """Определить тип аварии по тексту (rules, high recall). None — не авария.

    Справочные/educational формулировки (`info_suppress`) подавляются — это вопрос, а не
    сообщение об аварии. Триггеры/подавление берутся из `playbook_data.json`.
    """
    text = masked_text.lower()
    if any(stem in text for stem in _DATA.info_suppress):
        return None
    for type_, stems in _DATA.triggers:
        if any(stem in text for stem in stems):
            return type_
    return None


def entry_for(type_: str) -> PlaybookEntry | None:
    """Карточка плейбука по типу (None — нет)."""
    return _DATA.entries.get(type_)


def _uk_line(uk_contact: str | None) -> str:
    return _DATA.uk_with.format(contact=uk_contact) if uk_contact else _DATA.uk_without


def build_emergency_message(entry: PlaybookEntry, uk_contact: str | None) -> str:
    """Собрать конкретный, нешаблонный ответ: заголовок типа + пошаговые действия + кому звонить
    + (если применимо) вопрос про заявку. `uk_contact` — телефон УК из карточки (если есть).

    Чистая функция (golden-тестируемо), без ПДн пользователя.
    """
    lines = [f"⚠️ {entry.headline}"]
    if entry.steps:
        lines.append("Что сделать прямо сейчас:")
        lines.extend(f"{i}. {step}" for i, step in enumerate(entry.steps, start=1))
    lines.append(entry.call_line)
    lines.extend(f"• {_uk_line(uk_contact) if c == _UK else c}" for c in entry.contacts)
    if entry.partner_question:
        lines.append(entry.partner_question)
    return "\n".join(lines)
