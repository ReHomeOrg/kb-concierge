"""Плейбук аварийных ситуаций (документ «Консьерж: правила обработки заявок», аварийный блок).

Детерминированно (rules-first, без LLM и без живого интернет-поиска — безопасно и дёшево):
по тексту обращения определяется ТИП аварии, по нему — немедленное действие по безопасности,
кому звонить (единые номера + диспетчер УК из карточки/договора + физические указатели) и
режим партнёрской заявки. Контакты НЕ берутся из интернета (нет источника неверного номера).

Граница (важно): reHome занимается проблемами ВНУТРИ помещения по контракту. Общедомовое
(лифт, дом-газ) — направляем к ответственной службе, заявку партнёру не создаём (PARTNER_NONE).
Вход — ТОЛЬКО маскированный текст (G3).
"""

from __future__ import annotations

from dataclasses import dataclass

from api.emergency.constants import (
    PARTNER_CREATE,
    PARTNER_NONE,
    PARTNER_OFFER,
    SCOPE_BOTH,
    SCOPE_COMMON,
    SCOPE_PREMISES,
    TYPE_ELECTRICAL,
    TYPE_ELEVATOR,
    TYPE_FIRE,
    TYPE_GAS,
    TYPE_GENERIC,
    TYPE_HEATING,
    TYPE_PLUMBING,
    TYPE_SEWAGE,
)

_UK = "{uk}"  # плейсхолдер контакта управляющей организации


@dataclass(frozen=True)
class PlaybookEntry:
    """Карточка реакции на тип аварии (R-аварийный)."""

    type: str
    scope: str
    mitigation: str  # немедленное действие по безопасности ("" — нет)
    contacts: tuple[str, ...]  # строки «кому звонить»; `{uk}` → контакт УК
    partner_mode: str  # NONE/OFFER/CREATE
    repair_subcategory: str  # предзаполнение REPAIR-флоу ("" — нет)
    partner_question: str  # вопрос про заявку ("" для NONE)


PLAYBOOK: dict[str, PlaybookEntry] = {
    TYPE_GAS: PlaybookEntry(
        type=TYPE_GAS,
        scope=SCOPE_BOTH,
        mitigation=(
            "перекройте газовый кран, не включайте свет и электроприборы, проветрите "
            "помещение; при сильном запахе выйдите и позвоните с улицы"
        ),
        contacts=(
            "104 — аварийная газовая служба (единый номер, соединит со службой вашего района)",
            "112 — единая экстренная служба",
        ),
        partner_mode=PARTNER_OFFER,
        repair_subcategory="газовое оборудование",
        partner_question=(
            "Нужно вызвать мастера-партнёра для проверки газового оборудования, или вам "
            "достаточно аварийной газовой службы?"
        ),
    ),
    TYPE_FIRE: PlaybookEntry(
        type=TYPE_FIRE,
        scope=SCOPE_BOTH,
        mitigation="немедленно покиньте помещение и не пользуйтесь лифтом",
        contacts=("101 или 112 — пожарная служба",),
        partner_mode=PARTNER_NONE,
        repair_subcategory="",
        partner_question="",
    ),
    TYPE_ELEVATOR: PlaybookEntry(
        type=TYPE_ELEVATOR,
        scope=SCOPE_COMMON,
        mitigation="не пытайтесь открыть двери самостоятельно, сохраняйте спокойствие и ждите",
        contacts=(
            "аварийная служба лифта — номер указан в кабине лифта или рядом с ней",
            _UK,
        ),
        partner_mode=PARTNER_NONE,  # общедомовое имущество — мастер-партнёр не применим
        repair_subcategory="",
        partner_question="",
    ),
    TYPE_PLUMBING: PlaybookEntry(
        type=TYPE_PLUMBING,
        scope=SCOPE_PREMISES,
        mitigation="перекройте кран подачи воды (на бойлер или общий ввод в квартиру)",
        contacts=(_UK,),
        partner_mode=PARTNER_CREATE,
        repair_subcategory="сантехника",
        partner_question=(
            "Оформить заявку мастеру-сантехнику на ремонт? Решение займёт больше времени, "
            "чем аварийное перекрытие воды."
        ),
    ),
    TYPE_ELECTRICAL: PlaybookEntry(
        type=TYPE_ELECTRICAL,
        scope=SCOPE_PREMISES,
        mitigation="обесточьте проблемную линию автоматом в электрощите, не трогайте провода",
        contacts=(_UK,),
        partner_mode=PARTNER_CREATE,
        repair_subcategory="электрика",
        partner_question=(
            "Оформить заявку электрику-партнёру на ремонт? Это займёт больше времени, чем "
            "обесточивание."
        ),
    ),
    TYPE_HEATING: PlaybookEntry(
        type=TYPE_HEATING,
        scope=SCOPE_BOTH,
        mitigation="",
        contacts=(_UK,),
        partner_mode=PARTNER_OFFER,
        repair_subcategory="отопление",
        partner_question=(
            "Если проблема внутри квартиры — оформить заявку мастеру-партнёру? Если не греет "
            "весь дом, это вопрос к управляющей организации."
        ),
    ),
    TYPE_SEWAGE: PlaybookEntry(
        type=TYPE_SEWAGE,
        scope=SCOPE_BOTH,
        mitigation="не пользуйтесь канализацией до устранения засора",
        contacts=(_UK,),
        partner_mode=PARTNER_OFFER,
        repair_subcategory="сантехника",
        partner_question="Оформить заявку мастеру-партнёру на устранение засора? Это займёт время.",
    ),
    TYPE_GENERIC: PlaybookEntry(
        type=TYPE_GENERIC,
        scope=SCOPE_BOTH,
        mitigation="",
        contacts=("112 — единая экстренная служба", _UK),
        partner_mode=PARTNER_OFFER,
        repair_subcategory="",
        partner_question="Нужно оформить заявку мастеру-партнёру?",
    ),
}

# Триггеры по типам (порядок = приоритет; первое совпадение выигрывает). Стеммы lower-case.
# Сильные слова опасности (газ/пожар) — первыми; умеренные — по конкретным стеммам.
_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (TYPE_GAS, ("газом", "газа", "запах газ", "утечк газ", "пахнет газ", "воняет газ")),
    (TYPE_FIRE, ("пожар", "задымлен", "возгоран", "горит")),
    (
        TYPE_ELEVATOR,
        (
            "лифт застр",
            "застряли в лифт",
            "застрял в лифт",
            "в лифте застр",
            "лифт не работает",
            "лифт сломал",
            "сломался лифт",
            "лифт встал",
        ),
    ),
    (
        TYPE_ELECTRICAL,
        (
            "искрит",
            "коротит",
            "короткое замыкан",
            "замыкан",
            "дымит розет",
            "дымит провод",
            "дымит",
            "розетка дымит",
            "оголённ провод",
            "оголен провод",
            "бьёт током",
            "выбило пробк",
            "нет света",
            "погас свет",
        ),
    ),
    (
        TYPE_PLUMBING,
        (
            "прорвало",
            "прорвал",
            "затоп",
            "протечк",
            "бойлер",
            "хлещет вода",
            "залива",
            "потоп",
            "течёт вода",
            "течет вода",
            "трубу прорвал",
            "труба течёт",
            "сорвало кран",
        ),
    ),
    (
        TYPE_HEATING,
        (
            "нет отоплен",
            "не греют батар",
            "батареи холодн",
            "отопление не работа",
            "не работает отоплен",
            "холодно в квартир",
        ),
    ),
    (TYPE_SEWAGE, ("канализац", "засор", "нечистот")),
    (TYPE_GENERIC, ("авария", "аварийн", "экстренн", "чрезвычайн")),
)

# Образовательный/справочный контекст — не авария, а вопрос (анти-ложные срабатывания).
_INFO_SUPPRESS = (
    "как оформ",
    "как продл",
    "что такое",
    "что входит",
    "правил",
    "инструкц",
    "расскаж",
    "можно ли получ",
    "для справки",
)


def classify_emergency(masked_text: str) -> str | None:
    """Определить тип аварии по тексту (rules, high recall). None — не авария.

    Справочные/educational формулировки («что такое», «правила», «расскажи») подавляются —
    это вопрос, а не сообщение об аварии.
    """
    text = masked_text.lower()
    if any(s in text for s in _INFO_SUPPRESS):
        return None
    for type_, stems in _TRIGGERS:
        if any(stem in text for stem in stems):
            return type_
    return None


def entry_for(type_: str) -> PlaybookEntry | None:
    """Карточка плейбука по типу (None — нет)."""
    return PLAYBOOK.get(type_)


def _uk_line(uk_contact: str | None) -> str:
    if uk_contact:
        return f"управляющая организация: {uk_contact}"
    return "управляющая организация — телефон в вашем договоре или на стенде в подъезде"


def build_emergency_message(entry: PlaybookEntry, uk_contact: str | None) -> str:
    """Собрать ответ пользователю: действие по безопасности + кому звонить + вопрос про заявку.

    `uk_contact` — телефон УК из карточки объекта (если есть); иначе обобщённая формулировка.
    Чистая функция (golden-тестируемо), без ПДн пользователя.
    """
    lines = ["⚠️ Похоже на аварийную ситуацию — действуйте безопасно."]
    if entry.mitigation:
        lines.append(f"Сначала: {entry.mitigation}.")
    lines.append("Срочно позвоните напрямую:")
    for contact in entry.contacts:
        lines.append(f"• {_uk_line(uk_contact) if contact == _UK else contact}")
    if entry.partner_question:
        lines.append(entry.partner_question)
    return "\n".join(lines)
