"""Interfaz Streamlit ejecutada con AppTest: modo local y bloqueo en modo cloud sin autenticación.

Los identificadores de suministro son datos de prueba sintéticos (no corresponden a clientes).
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "web" / "streamlit_app.py")
TENANT = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("WEBSITE_SITE_NAME", "WEB_AGENT_MODE", "WEB_AUTH", "WEB_FILES_MODE", "PROJECT_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    import streamlit as st

    st.cache_resource.clear()


COORDENADAS = "Quiero las coordenadas disponibles del suministro 0099000001"
MEDIDOR = "Datos del medidor del suministro 0099000001"


def local_app(monkeypatch, tmp_path, *, retries="2"):
    monkeypatch.setenv("WEB_AGENT_MODE", "inprocess")
    monkeypatch.setenv("WEB_FILES_MODE", "local")
    monkeypatch.setenv("LOCAL_FILES_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_MAX_RETRIES", retries)
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("TEXT2SQL_SQL_BACKEND", "simulated")
    return AppTest.from_file(APP, default_timeout=30).run()


def texts(app):
    return [block.markdown[0].value for block in app.chat_message]


def test_local_chat_multi_turn_and_new_conversation(monkeypatch, tmp_path):
    app = local_app(monkeypatch, tmp_path)
    assert not app.exception and not app.error
    app.chat_input[0].set_value(COORDENADAS).run()
    app.chat_input[0].set_value(MEDIDOR).run()
    messages = texts(app)
    assert len(messages) == 4 and "SQL ejecutado" in messages[1] and "[Numero de serie]" in messages[3]
    session = app.session_state["agent_session_id"]
    assert session and app.session_state["agent_state"]["turns"] == 2

    next(button for button in app.sidebar.button if button.label == "Nueva conversación").click().run()
    assert texts(app) == [] and app.session_state["agent_session_id"] is None
    app.chat_input[0].set_value("/historial").run()
    assert "Todavía no hay preguntas" in texts(app)[1]


def test_local_file_download_flow(monkeypatch, tmp_path):
    app = local_app(monkeypatch, tmp_path)
    app.chat_input[0].set_value(COORDENADAS).run()
    prepare = [button for button in app.button if button.label.startswith("Preparar descarga")]
    assert prepare and prepare[0].label.endswith("resultado.json")
    prepare[0].click().run()
    assert not app.error
    assert app.session_state["downloads"]


def test_invalid_sql_and_no_respondible_are_answers_not_errors(monkeypatch, tmp_path):
    app = local_app(monkeypatch, tmp_path)
    app.chat_input[0].set_value("Ha tenido reclamos el suministro 0099000001").run()
    app.chat_input[0].set_value("hola [simular-sql-invalido]").run()
    messages = texts(app)
    assert not app.error and "NO_RESPONDIBLE" in messages[1] and "rechazado" in messages[3]


def test_busy_error_offers_manual_retry_with_same_request(monkeypatch, tmp_path):
    app = local_app(monkeypatch, tmp_path, retries="0")
    app.chat_input[0].set_value(MEDIDOR + " [simular-ocupado:1]").run()
    assert app.error and "ocupado" in app.error[0].value
    request_id = app.session_state["pending"]["request_id"]
    retry = next(button for button in app.button if button.label == "Reintentar")
    retry.click().run()
    assert not app.error and app.session_state["pending"] is None
    assert app.session_state["agent_state"]["turns"] == 1 and len(texts(app)) == 2
    assert request_id  # el reintento reutilizó el request_id pendiente (el doble falla solo una vez por id)


def test_agent_error_is_shown_without_crashing(monkeypatch, tmp_path):
    app = local_app(monkeypatch, tmp_path)
    app.session_state["agent_state"] = {"v": 1, "turns": "corrupto", "history": []}
    app.chat_input[0].set_value("hola").run()
    assert app.error and "estado" in app.error[0].value.lower()
    assert app.session_state["pending"] is None


def test_cloud_mode_without_easy_auth_never_invokes_agent(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "starter-web")
    monkeypatch.setenv("WEB_AGENT_MODE", "foundry")
    monkeypatch.setenv("PROJECT_ENDPOINT", "https://a.services.ai.azure.com/api/projects/p")
    monkeypatch.setenv("HOSTED_AGENT_NAME", "starter-agent")
    monkeypatch.setenv("EXPECTED_TENANT_ID", TENANT)
    monkeypatch.setenv("ALLOWED_USER_OBJECT_IDS", USER)
    import web.client

    def forbidden(*args, **kwargs):
        raise AssertionError("no debe crear un cliente sin identidad verificada")

    monkeypatch.setattr(web.client, "transport_for", forbidden)
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert app.error and "identidad" in app.error[0].value
    assert len(app.chat_input) == 0


def test_cloud_mode_rejects_local_mode_on_app_service(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "starter-web")
    monkeypatch.setenv("WEB_AGENT_MODE", "inprocess")
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert app.error and "App Service" in app.error[0].value
    assert len(app.chat_input) == 0
