"""Contrato web <-> agente de extremo a extremo, sin red: el cliente web habla con el servicio real.

Adaptado del demo de la base: los turnos son preguntas Text-to-SQL con el generador simulado
(``STARTER_RESPONDER=echo``) y la base de datos simulada. Los identificadores de suministro son datos de
prueba sintéticos (no corresponden a clientes).
"""
import json

import pytest

from web.client import AgentCallError, AgentClient, InProcessTransport
from web.config import WEB_ROOT

COORDENADAS = "Quiero las coordenadas disponibles del suministro 0099000001"
MEDIDOR = "Datos del medidor del suministro 0099000001"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("TEXT2SQL_SQL_BACKEND", "simulated")
    transport = InProcessTransport(WEB_ROOT.parent / "agent",
                                   {"FILES_MODE": "local", "LOCAL_FILES_DIR": str(tmp_path), "STARTER_RESPONDER": "echo"})
    return AgentClient(transport, max_retries=0)


def test_multi_turn_conversation_and_restart(client):
    first = client.send(COORDENADAS, {}, None)
    second = client.send(MEDIDOR, first.state, first.session_id)
    assert "[Numero de serie]" in second.reply and second.state["turns"] == 2
    assert second.session_id == first.session_id
    restarted = client.send(MEDIDOR, {}, None)  # «Nueva conversación»
    assert restarted.state["turns"] == 1 and restarted.session_id != first.session_id


def test_agent_errors_reach_the_web_as_call_errors(client):
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {"v": 1, "turns": "x", "history": []}, None)
    assert error.value.code == "invalid_state" and error.value.status == 400


def test_generated_file_reference_is_downloadable_locally(client, tmp_path):
    from web.files import LocalDownloader

    result = client.send(COORDENADAS, {}, None)
    reference = result.files[0]
    assert reference.name == "resultado.json"
    data = json.loads(LocalDownloader(tmp_path).read(reference.id, {reference.id}))
    assert data["ok"] and data["ejecutado"] and data["sql_validado"].startswith("SELECT DISTINCT TOP 50")


def test_busy_agent_is_retried_automatically_with_same_request(tmp_path, monkeypatch):
    transport = InProcessTransport(WEB_ROOT.parent / "agent",
                                   {"FILES_MODE": "none", "STARTER_RESPONDER": "echo"})
    client = AgentClient(transport, max_retries=2, sleep=lambda seconds: None)
    result = client.send(MEDIDOR + " [simular-ocupado:2]", {}, None, "req-auto-retry")
    assert result.state["turns"] == 1  # 2 fallos + 1 éxito, un solo turno


def test_busy_agent_surfaces_after_retries_and_manual_retry_succeeds(monkeypatch):
    transport = InProcessTransport(WEB_ROOT.parent / "agent",
                                   {"FILES_MODE": "none", "STARTER_RESPONDER": "echo"})
    client = AgentClient(transport, max_retries=1, sleep=lambda seconds: None)
    message = MEDIDOR + " [simular-ocupado:2]"
    with pytest.raises(AgentCallError) as error:
        client.send(message, {}, None, "req-manual-retry")
    assert error.value.retryable and error.value.code == "model_rate_limited"
    assert client.send(message, {}, None, "req-manual-retry").state["turns"] == 1  # botón «Reintentar»
