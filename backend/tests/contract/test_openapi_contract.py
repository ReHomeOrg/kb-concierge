"""Контрактные тесты — соответствие реализации docs/openapi.yaml.

M0 (каркас): спека парсится (OpenAPI 3.1), объявляет инфраструктурные пробы и
скелет доменных путей (§10), `/healthz` валиден против схемы `Healthz`, схема
`Error` — валидный JSON Schema. По мере эпиков добавляются доменные операции.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

_OPENAPI_PATH = Path(__file__).resolve().parents[3] / "docs" / "openapi.yaml"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    with _OPENAPI_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def test_spec_parses_and_declares_infra(spec: dict[str, Any]) -> None:
    assert spec["openapi"].startswith("3.1")
    for path in ("/healthz", "/readyz", "/metrics"):
        assert path in spec["paths"], f"missing path {path}"


def test_spec_declares_concierge_skeleton(spec: dict[str, Any]) -> None:
    base = "/api/v1/concierge"
    paths = spec["paths"]
    assert "post" in paths[f"{base}/sessions"]
    assert "post" in paths[f"{base}/sessions/{{session_id}}/messages"]
    assert "delete" in paths[f"{base}/sessions/{{session_id}}"]
    assert "post" in paths[f"{base}/inbound/operator-reply"]


def test_healthz_response_matches_schema(spec: dict[str, Any], client: TestClient) -> None:
    schema = spec["components"]["schemas"]["Healthz"]
    Draft202012Validator.check_schema(schema)
    body = client.get("/healthz").json()
    Draft202012Validator(schema).validate(body)


def test_error_schema_is_valid(spec: dict[str, Any]) -> None:
    schema = spec["components"]["schemas"]["Error"]
    Draft202012Validator.check_schema(schema)


def test_public_probes_have_empty_security(spec: dict[str, Any]) -> None:
    for path in ("/healthz", "/readyz", "/metrics"):
        assert spec["paths"][path]["get"]["security"] == []


def test_handoff_and_operator_reply_declared(spec: dict[str, Any]) -> None:
    base = "/api/v1/concierge"
    handoff = spec["paths"][f"{base}/sessions/{{session_id}}/handoff"]["post"]
    assert (
        "HandoffAccepted"
        in handoff["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
    )
    reply = spec["paths"][f"{base}/inbound/operator-reply"]["post"]
    assert reply["requestBody"]["required"] is True
    assert "403" in reply["responses"]  # SERVICE-only контур


def test_operator_reply_schema_forbids_extra_fields(spec: dict[str, Any]) -> None:
    # Инвариант «внутреннее ≠ внешнее» (FR-7.3): контракт запрещает посторонние поля
    # (внутренние заметки оператора не могут попасть во входной контур диалога).
    schema = spec["components"]["schemas"]["OperatorReplyIn"]
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
