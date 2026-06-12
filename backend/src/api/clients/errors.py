"""Типизированные ошибки слоя HTTP-клиентов к соседям (NFR-9, арх-константа ADR-0001).

Конкретные клиенты-инструменты ловят эти типы и решают, как деградировать (вернуть
кеш/None/пусто — FR-6.6). `ResilientHttpClient` не «глотает» ошибки: типизирует и
пробрасывает. Тело ответа соседа в текст исключения НЕ тащим (ФЗ-152: возможные ПДн).
"""

from __future__ import annotations


class ExternalServiceError(Exception):
    """Сбой вызова соседа (после исчерпания retry / неретраибельный)."""

    def __init__(self, client: str, operation: str, message: str) -> None:
        self.client = client
        self.operation = operation
        super().__init__(f"{client}.{operation}: {message}")


class CircuitOpenError(ExternalServiceError):
    """Circuit breaker открыт — вызов соседа отклонён без сетевой попытки."""

    def __init__(self, client: str, operation: str) -> None:
        super().__init__(client, operation, "circuit breaker is open")
