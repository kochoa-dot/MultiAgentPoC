"""Adaptador Foundry real (``agent/main.py`` sobre azure-ai-agentserver-invocations), sin red externa.

Los identificadores de suministro son datos de prueba sintéticos (no corresponden a clientes).
"""
import importlib

import pytest
from starlette.testclient import TestClient

from starter_agent.contract import CONTRACT_VERSION


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STARTER_RESPONDER", "echo")
    monkeypatch.setenv("FILES_MODE", "local")
    monkeypatch.setenv("LOCAL_FILES_DIR", str(tmp_path))
    import main

    module = importlib.reload(main)
    with TestClient(module.app) as test_client:
        yield test_client


def invoke(client, message, state=None, session=None, request_id="req-00000001"):
    params = {"agent_session_id": session} if session else {}
    return client.post("/invocations", params=params, json={
        "contract": CONTRACT_VERSION, "request_id": request_id, "message": message, "state": state or {}})


def test_two_turns_keep_state_and_session(client):
    first = invoke(client, "Quiero las coordenadas disponibles del suministro 0099000001", session="sess-abc")
    assert first.status_code == 200
    assert first.headers["x-agent-session-id"] == "sess-abc"
    second = invoke(client, "Dirección del suministro 0099000001", first.json()["state"], "sess-abc", "req-00000002")
    assert second.status_code == 200 and "[Calle]" in second.json()["reply"]
    assert second.json()["state"]["turns"] == 2


def test_session_header_is_generated_without_query(client):
    response = invoke(client, "hola")
    assert response.headers.get("x-agent-session-id")


def test_invalid_json_returns_contract_error(client):
    response = client.post("/invocations", content=b"{no-json", headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_files_are_reported_by_id(client, tmp_path):
    response = invoke(client, "Datos del medidor del suministro 0099000001")
    body = response.json()
    assert response.status_code == 200
    assert body["files"][0]["id"] == "req-00000001/resultado.json"
    assert (tmp_path / "outputs" / "req-00000001" / "resultado.json").exists()


def test_busy_turn_returns_503_retryable(client):
    response = invoke(client, "Datos del medidor del suministro 0099000001 [simular-ocupado]", request_id="req-busy-001")
    assert response.status_code == 503 and response.json()["error"]["retryable"] is True
    assert invoke(client, "Datos del medidor del suministro 0099000001 [simular-ocupado]",
                  request_id="req-busy-001").status_code == 200


def test_missing_sql_view_is_explicit(client, monkeypatch):
    monkeypatch.delenv("SQL_VIEW_NAME")
    response = invoke(client, "Datos del medidor del suministro 0099000001")
    assert response.status_code == 500 and response.json()["error"]["code"] == "sql_config"


def test_readiness_probe(client):
    assert client.get("/readiness").status_code == 200


def test_misconfiguration_is_explicit(monkeypatch):
    monkeypatch.setenv("STARTER_RESPONDER", "model")
    monkeypatch.delenv("MODEL_ENDPOINT", raising=False)
    import main

    module = importlib.reload(main)
    with TestClient(module.app) as test_client:
        response = invoke(test_client, "hola")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "model_config"
    monkeypatch.setenv("STARTER_RESPONDER", "echo")
    importlib.reload(main)
