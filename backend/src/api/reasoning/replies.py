"""Загрузчик диалоговых текстов confirmation + handoff (правятся БЕЗ кода).

Ключевые слова согласия/отказа (FR-7.4) и тексты реплик агента вынесены в редактируемый
`replies_data.json` рядом с модулем — правятся без кода (проверяются тестами). Ops может
указать свой файл через `KBC_REPLIES_PATH` (битый override → откат на встроенный, FR-6.6;
битый встроенный — ошибка сборки, fail-fast). Детектор подтверждения остаётся
детерминированным (rules, не LLM, NFR-10).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)
_BUNDLED_PATH = Path(__file__).with_name("replies_data.json")
#: env-переменная ops-override пути к файлу текстов (необязательна).
_OVERRIDE_ENV = "KBC_REPLIES_PATH"
#: Обязательные ключи реплик (battery: отсутствие → ошибка сборки на встроенном файле).
_REQUIRED_REPLIES = frozenset(
    {
        "propose_partner",
        "propose_default",
        "decline",
        "reask",
        "write_unavailable",
        "handoff",
        "clarify",
        "pending",
        "small_talk",
        "out_of_scope",
        "default",
        "no_answer",
        "fallback_answer",
    }
)


@dataclass(frozen=True)
class _Replies:
    """Разобранные тексты/ключевые слова диалога."""

    yes: tuple[str, ...]
    no: tuple[str, ...]
    replies: dict[str, str]


def _parse(data: Any) -> _Replies:
    """Провалидировать JSON и собрать тексты. Некорректная структура → ValueError."""
    if not isinstance(data, dict):
        raise ValueError("replies config root must be an object")
    confirmation = data["confirmation"]
    yes = tuple(str(k).lower() for k in confirmation["yes"])
    no = tuple(str(k).lower() for k in confirmation["no"])
    if not yes or not no:
        raise ValueError("confirmation.yes/no must be non-empty")
    replies = {str(k): str(v) for k, v in data["replies"].items()}
    missing = _REQUIRED_REPLIES - replies.keys()
    if missing:
        raise ValueError(f"missing replies: {sorted(missing)}")
    return _Replies(yes=yes, no=no, replies=replies)


def load_replies(path: str | None = None) -> _Replies:
    """Загрузить тексты: из `path` (ops-override) либо из встроенного файла.

    Битый override → предупреждение + откат на встроенный (FR-6.6). Битый встроенный →
    исключение (fail-fast).
    """
    if path:
        try:
            return _parse(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            _logger.warning("replies override invalid (%s) → встроенный", exc)
    return _parse(json.loads(_BUNDLED_PATH.read_text(encoding="utf-8")))


_DATA = load_replies(os.getenv(_OVERRIDE_ENV) or None)

#: Ключевые слова согласия/отказа (детектор FR-7.4).
CONFIRM_YES: tuple[str, ...] = _DATA.yes
CONFIRM_NO: tuple[str, ...] = _DATA.no
#: Тексты реплик агента по ключу.
REPLIES: dict[str, str] = _DATA.replies
