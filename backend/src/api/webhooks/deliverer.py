"""Доставка исходящих webhooks подписчику с HMAC-подписью (§10, целостность).

Тело — канонический JSON; подпись `HMAC-SHA256(secret, body)` в заголовке
`X-Concierge-Signature: sha256=<hex>` (подписчик проверяет аутентичность/целостность).
2xx → доставлено; иначе/исключение → повтор воркером. Секрет — ссылкой на kb-vault.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Protocol

import httpx


class WebhookDeliverer(Protocol):
    async def deliver(self, *, event_type: str, payload: dict[str, Any], event_id: str) -> bool: ...


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 подпись тела (hex)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class HmacWebhookDeliverer:
    """Боевой доставщик: POST подписчику с HMAC-подписью."""

    def __init__(self, *, http: httpx.AsyncClient, url: str, secret: str) -> None:
        self._http = http
        self._url = url
        self._secret = secret

    async def deliver(self, *, event_type: str, payload: dict[str, Any], event_id: str) -> bool:
        body = json.dumps(
            {"id": event_id, "event": event_type, "data": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Concierge-Event": event_type,
            "X-Concierge-Signature": f"sha256={sign(self._secret, body)}",
        }
        response = await self._http.post(self._url, content=body, headers=headers)
        return 200 <= response.status_code < 300
