"""Юнит-тесты HMAC-доставщика webhooks (M8.2): подпись + исход по статусу."""

from __future__ import annotations

import hashlib
import hmac

import httpx

from api.webhooks.deliverer import HmacWebhookDeliverer, sign


def test_sign_is_hmac_sha256_hex() -> None:
    body = b'{"a":1}'
    expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert sign("secret", body) == expected


async def test_deliver_signs_body_and_sets_headers() -> None:
    seen: dict[str, str] = {}
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        captured["body"] = request.content
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://hook")
    async with http:
        deliverer = HmacWebhookDeliverer(http=http, url="/in", secret="s3cret")
        ok = await deliverer.deliver(
            event_type="agent.answered", payload={"session_id": "s-1"}, event_id="e-1"
        )
    assert ok is True
    assert seen["x-concierge-event"] == "agent.answered"
    expected_sig = "sha256=" + sign("s3cret", captured["body"])
    assert seen["x-concierge-signature"] == expected_sig  # целостность/аутентичность


async def test_deliver_returns_false_on_non_2xx() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="http://hook"
    )
    async with http:
        deliverer = HmacWebhookDeliverer(http=http, url="/in", secret="s")
        ok = await deliverer.deliver(event_type="agent.answered", payload={}, event_id="e")
    assert ok is False  # не-2xx → воркер повторит
