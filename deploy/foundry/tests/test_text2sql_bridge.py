"""Puente Text-to-SQL: paridad con el núcleo, estado multiturno, errores, reintentos y archivos.

Todas las dependencias externas son dobles: ni modelo ni base de datos reales. Los identificadores de
suministro, ruta y medidor son datos de prueba sintéticos (no corresponden a clientes).
"""
import json
from unittest.mock import patch

import pytest

import text2sql_core as core
from starter_agent.contract import (CONTRACT_VERSION, AgentBusyError, AgentConfigError, AgentContext,
                                    AgentInputError)
from starter_agent.files import LocalFileStore
from starter_agent.handler import handle_message
from starter_agent.runtime import Runtime
from starter_agent.service import process
from text2sql_adapter import bridge, simulated
from text2sql_adapter.settings import Text2SqlSettings

from conftest import TEST_SCHEMA, TEST_VIEW

CFG = core.Configuracion(TEST_SCHEMA, TEST_VIEW)
VISTA = CFG.nombre_sql
SELECT = f"SELECT v.[Pue.sumin.], v.[Equipo] FROM {VISTA} AS v WHERE v.[Pue.sumin.] = '0099000001'"


class FakeModel:
    """Modelo doble: devuelve un texto fijo o lanza una excepción, y registra los mensajes."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def ctx(request_id="req-00000001", **kwargs):
    return AgentContext(request_id=request_id, **kwargs)


# --- Paridad con text2sql_core.responder_pregunta -----------------------------------------

PARITY_CASES = [
    # (id, salida del modelo, ejecutar, ejecutor)
    ("valido-sin-ejecutar", SELECT, False, None),
    ("valido-ejecutado", SELECT, True, (["Pue.sumin.", "Equipo"], [("0099000001", "0001")])),
    ("no-respondible", "NO_RESPONDIBLE", True, None),
    ("rechazado", "SELECT * FROM dbo.Suministro", True, None),
    ("markdown", "```sql\n" + SELECT + "\n```", False, None),
    ("metadatos-ausentes", SELECT, True, ValueError("La vista real no contiene todas las columnas solicitadas.")),
    ("error-bd", SELECT, True, RuntimeError("Password=SECRETO; host=PRIVADO")),
]


@pytest.mark.parametrize("sql, ejecutar, outcome", [case[1:] for case in PARITY_CASES],
                         ids=[case[0] for case in PARITY_CASES])
def test_bridge_matches_core_responder_pregunta(sql, ejecutar, outcome):
    def fake_execute(validado, config):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(core, "generar_sql", return_value=(sql, {})), \
            patch.object(core, "ejecutar_sql", side_effect=fake_execute):
        original = core.responder_pregunta("pregunta", ejecutar=ejecutar, config=CFG).to_dict()
        puente = bridge.responder("pregunta", generate=lambda q, c: sql, execute=core.ejecutar_sql,
                                  run_sql=ejecutar, config=CFG).to_dict()
    assert puente == original
    assert "SECRETO" not in json.dumps(puente)


def test_empty_model_answer_matches_core():
    with patch.object(core, "generar_sql", side_effect=ValueError("El modelo no devolvió contenido SQL.")):
        original = core.responder_pregunta("p", config=CFG).to_dict()
    puente = bridge.responder("p", generate=bridge.model_generator(FakeModel("")), execute=core.ejecutar_sql,
                              run_sql=False, config=CFG).to_dict()
    assert puente == original and not puente["ok"]


def test_model_receives_the_same_messages_as_core_generar_sql(monkeypatch):
    captured = {}

    class FakeAzureOpenAI:
        def __init__(self, **kwargs):
            self.chat = self
            self.completions = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def create(self, model, messages):
            captured["messages"] = messages
            message = type("M", (), {"content": "NO_RESPONDIBLE"})()
            return type("R", (), {"choices": [type("C", (), {"message": message})()], "usage": None})()

    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.setenv(key, "x")
    import openai
    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureOpenAI)
    core.generar_sql("  ¿Coordenadas del suministro 0099000001?  ", CFG)
    assert bridge.model_messages("  ¿Coordenadas del suministro 0099000001?  ", CFG) == captured["messages"]


# --- Handler con el modelo del contexto ----------------------------------------------------

def test_model_sql_is_validated_and_not_executed_by_default():
    generated = SELECT.replace("SELECT ", "SELECT TOP 999 ", 1)
    model = FakeModel(generated)
    with patch.object(core, "ejecutar_sql", side_effect=AssertionError("no debe ejecutar")):
        result = handle_message("Datos del suministro 0099000001", {}, ctx(model=model))
    assert "**SQL validado**" in result.reply and "TOP 200" in result.reply  # límite real del núcleo
    assert "Simulación" not in result.reply
    assert model.calls[0][0]["content"] == core.construir_prompt(CFG)
    assert result.state["history"][0] == {"pregunta": "Datos del suministro 0099000001", "resultado": "ok",
                                          "sql": core.validar_sql(generated, CFG), "ejecutado": False}


def test_unsafe_model_sql_is_rejected_and_never_executed(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    with patch.object(core, "ejecutar_sql") as execute:
        result = handle_message("borra todo", {}, ctx(model=FakeModel(f"DELETE FROM {VISTA}")))
    execute.assert_not_called()
    assert "No se obtuvo una consulta válida" in result.reply
    assert result.state["history"][0]["resultado"] == "error"


def test_execution_uses_core_ejecutar_sql_when_enabled(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    with patch.object(core, "ejecutar_sql", return_value=(["Pue.sumin."], [("0099000001",)])) as execute:
        result = handle_message("Suministro 0099000001", {}, ctx(model=FakeModel(SELECT)))
    assert execute.call_args.args == (core.validar_sql(SELECT, CFG), CFG)
    assert "**SQL ejecutado** · 1 fila(s)." in result.reply and "| 0099000001 |" in result.reply


@pytest.mark.parametrize("error", [
    AgentBusyError("El modelo alcanzó su cuota.", code="model_rate_limited", retry_after_ms=1000),
    AgentConfigError("El agente no tiene permiso para usar el modelo.", code="model_forbidden"),
])
def test_model_contract_errors_propagate(error):
    with pytest.raises(type(error)) as raised:
        handle_message("pregunta", {}, ctx(model=FakeModel(error)))
    assert raised.value is error


def test_missing_sql_configuration_is_operator_error(monkeypatch):
    monkeypatch.delenv("SQL_VIEW_SCHEMA")
    with pytest.raises(AgentConfigError) as error:
        handle_message("pregunta", {}, ctx())
    assert error.value.code == "sql_config" and "SQL_VIEW_SCHEMA" in error.value.message


@pytest.mark.parametrize("key, value", [("TEXT2SQL_EXECUTE", "quizas"), ("TEXT2SQL_SQL_BACKEND", "sqlite")])
def test_invalid_text2sql_settings_are_operator_errors(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(AgentConfigError):
        handle_message("pregunta", {}, ctx())


def test_settings_defaults_match_original_behaviour():
    assert Text2SqlSettings.from_env({}) == Text2SqlSettings(execute=False, sql_backend="odbc")


@pytest.mark.parametrize("error", [TimeoutError("lento"),
                                   type("OperationalError", (Exception,), {"__module__": "pyodbc"})("08S01")])
def test_transient_database_errors_are_retryable(monkeypatch, error):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    with patch.object(core, "ejecutar_sql", side_effect=error):
        with pytest.raises(AgentBusyError) as raised:
            handle_message("Suministro 0099000001", {}, ctx(model=FakeModel(SELECT)))
    assert raised.value.code == "sql_unavailable" and raised.value.retryable


def test_database_errors_never_reach_the_user(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    with patch.object(core, "ejecutar_sql", side_effect=RuntimeError("Password=SECRETO;Server=PRIVADO")):
        result = handle_message("Suministro 0099000001", {}, ctx(model=FakeModel(SELECT)))
    assert "No fue posible ejecutar la consulta" in result.reply
    assert "SECRETO" not in result.reply and "PRIVADO" not in json.dumps(result.state)


# --- Estado multiturno -----------------------------------------------------------------------

def test_two_turns_chain_state_and_new_conversation_starts_empty():
    first = handle_message("Quiero las coordenadas disponibles del suministro 0099000001", {}, ctx())
    second = handle_message("Dirección del suministro 0099000001", first.state, ctx("req-00000002"))
    assert first.state["turns"] == 1 and second.state["turns"] == 2
    assert [entry["resultado"] for entry in second.state["history"]] == ["ok", "ok"]
    restarted = handle_message("Dirección del suministro 0099000001", {}, ctx("req-00000003"))
    assert restarted.state["turns"] == 1 and len(restarted.state["history"]) == 1


def test_history_is_bounded_and_listed():
    state = {}
    for index in range(bridge.MAX_HISTORY + 3):
        state = handle_message(f"Datos del medidor del suministro 00990002{index:02d}", state, ctx()).state
    assert len(state["history"]) == bridge.MAX_HISTORY and state["turns"] == bridge.MAX_HISTORY + 3
    listed = handle_message("/historial", state, ctx())
    assert listed.state == state and "Turnos: 13" in listed.reply


def test_help_and_history_do_not_count_as_turns():
    assert handle_message("/ayuda", {}, ctx()).state["turns"] == 0
    assert "Todavía no hay preguntas" in handle_message("/historial", {}, ctx()).reply
    with pytest.raises(AgentInputError) as error:
        handle_message("/borrar", {}, ctx())
    assert error.value.code == "unknown_command"


ENTRY = {"pregunta": "p", "resultado": "ok", "sql": None, "ejecutado": False}


@pytest.mark.parametrize("state", [
    {"v": 2, "turns": 0, "history": []},
    {"v": 1, "turns": -1, "history": []},
    {"v": 1, "turns": True, "history": []},
    {"v": 1, "turns": 0, "history": {}},
    {"v": 1, "turns": 0, "history": [], "rol": "admin"},
    {"v": 1, "turns": 1, "history": [{**ENTRY, "resultado": "aprobado"}]},
    {"v": 1, "turns": 1, "history": [{**ENTRY, "sql": "x" * 5000}]},
    {"v": 1, "turns": 1, "history": [{**ENTRY, "extra": 1}]},
    {"v": 1, "turns": 11, "history": [ENTRY] * 11},
])
def test_tampered_state_is_rejected(state):
    with pytest.raises(AgentInputError) as error:
        handle_message("pregunta", state, ctx())
    assert error.value.code == "invalid_state"


# --- Contrato completo: service.process, archivos, reintentos ------------------------------

def runtime(tmp_path=None):
    files = LocalFileStore(tmp_path) if tmp_path else None
    return Runtime(model=None, files=files, responder="echo", files_mode="local" if files else "none")


def payload(message, state=None, request_id="req-00000001"):
    return {"contract": CONTRACT_VERSION, "request_id": request_id, "message": message, "state": state or {}}


def test_service_returns_contract_and_result_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("TEXT2SQL_SQL_BACKEND", "simulated")
    status, body = process(payload("Quiero las coordenadas disponibles del suministro 0099000001"),
                           runtime(tmp_path), handle_message)
    assert status == 200 and set(body) == {"contract", "request_id", "reply", "state", "files"}
    assert body["files"][0]["id"] == "req-00000001/resultado.json"
    stored = json.loads((tmp_path / "outputs" / "req-00000001" / "resultado.json").read_text(encoding="utf-8"))
    assert set(stored) == set(vars(core.RespuestaAgente("p", True)))  # mismo contrato que to_dict()
    assert stored["ejecutado"] and stored["filas"][0][1] == "13.6929"  # Decimal serializado por el núcleo
    assert "Simulación" in body["reply"]


def test_busy_turn_is_retried_with_same_request_id(monkeypatch):
    message = "Datos del medidor del suministro 0099000001 [simular-ocupado:2]"
    first = process(payload(message, request_id="req-retry-01"), runtime(), handle_message)
    second = process(payload(message, request_id="req-retry-01"), runtime(), handle_message)
    third = process(payload(message, request_id="req-retry-01"), runtime(), handle_message)
    assert first[0] == second[0] == 503 and first[1]["error"]["retryable"]
    assert first[1]["state"] == {}  # el estado recibido vuelve sin cambios
    assert third[0] == 200 and third[1]["state"]["turns"] == 1  # el reintento no duplica el turno


def test_database_busy_is_retryable_through_service(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("TEXT2SQL_SQL_BACKEND", "simulated")
    message = "Datos del medidor del suministro 0099000001 [simular-bd-ocupada]"
    status, body = process(payload(message, request_id="req-dbbusy-1"), runtime(), handle_message)
    assert status == 503 and body["error"]["code"] == "sql_unavailable"
    assert process(payload(message, request_id="req-dbbusy-1"), runtime(), handle_message)[0] == 200


def test_reply_escapes_database_values_and_limits_rows(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    rows = [("[clic](https://ejemplo.invalid) | ![x](y)", f"{i}") for i in range(25)]
    with patch.object(core, "ejecutar_sql", return_value=(["Pue.sumin.", "Equipo"], rows)):
        result = handle_message("Suministro 0099000001", {}, ctx(model=FakeModel(SELECT)))
    assert "](https" not in result.reply and "![x]" not in result.reply
    assert result.reply.count("\n| ") == bridge.MAX_ROWS_IN_REPLY + 1  # cabecera + 20 filas
    assert "Se muestran 20 de 25 filas." in result.reply


# --- Dobles simulados ------------------------------------------------------------------------

@pytest.mark.parametrize("question, expected", core.ejemplos_sql(CFG), ids=lambda value: value[:30])
def test_simulated_generator_reproduces_core_examples(question, expected):
    assert simulated.example_sql(question, CFG) == expected


def test_simulated_generator_uses_question_identifier_and_requires_one():
    sql = simulated.example_sql("Datos del medidor del suministro 0001234567", CFG)
    assert "'0001234567'" in sql and "0099000001" not in sql
    assert simulated.example_sql("Datos del medidor", CFG) == "NO_RESPONDIBLE"
    assert simulated.example_sql("Hola", CFG) == "NO_RESPONDIBLE"


def test_simulated_database_revalidates_sql():
    with pytest.raises(ValueError):
        simulated.SimulatedDatabase("p", "req-00000001")(f"DELETE FROM {VISTA}", CFG)
