"""Contrato del lado del agente: validación, errores, archivos e idempotencia."""
import json

import pytest

from starter_agent.contract import (CONTRACT_VERSION, MAX_MESSAGE_CHARS, AgentBusyError, AgentContext,
                                    AgentResult, OutputFile)
from starter_agent.files import LocalFileStore
from starter_agent.handler import handle_message
from starter_agent.runtime import Runtime, build_runtime
from starter_agent.service import process
from starter_agent.contract import AgentConfigError


def runtime(tmp_path=None, model=None):
    files = LocalFileStore(tmp_path) if tmp_path else None
    return Runtime(model=model, files=files, responder="model" if model else "echo",
                   files_mode="local" if files else "none")


def payload(message="hola", state=None, request_id="req-00000001"):
    return {"contract": CONTRACT_VERSION, "request_id": request_id, "message": message, "state": state or {}}


@pytest.mark.parametrize("bad, code", [
    ([], "invalid_request"),
    ({"message": ""}, "empty_message"),
    ({"message": "   "}, "empty_message"),
    ({"message": "x" * (MAX_MESSAGE_CHARS + 1)}, "message_too_long"),
    ({"message": "hola", "state": []}, "invalid_state"),
    ({"message": "hola", "state": {"big": "x" * 70_000}}, "invalid_state"),
    ({"message": "hola", "request_id": "../../x"}, "invalid_request"),
    ({"message": "hola", "contract": "otro.v9"}, "unsupported_contract"),
])
def test_invalid_requests_are_rejected_with_400(bad, code):
    status, body = process(bad, runtime(), handle_message)
    assert status == 400
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is False
    assert body["contract"] == CONTRACT_VERSION


def test_success_body_matches_contract():
    status, body = process(payload(), runtime(), handle_message, session_id="s1")
    assert status == 200
    assert set(body) == {"contract", "request_id", "reply", "state", "files"}
    assert body["request_id"] == "req-00000001"
    assert body["files"] == []
    json.dumps(body)


def test_missing_request_id_is_generated_for_manual_invocations():
    status, body = process({"message": "hola"}, runtime(), handle_message)
    assert status == 200 and len(body["request_id"]) == 32


def test_tampered_state_is_rejected_and_echoed_back():
    state = {"v": 1, "turns": -3, "name": None, "history": []}
    status, body = process(payload(state=state), runtime(), handle_message)
    assert status == 400
    assert body["error"]["code"] == "invalid_state"
    assert body["state"] == state


def test_unexpected_exception_does_not_leak_details():
    def broken(message, state, context):
        raise RuntimeError("secreto: contenido del usuario")

    status, body = process(payload("dato sensible"), runtime(), broken)
    assert status == 500
    assert body["error"]["code"] == "internal_error"
    assert "secreto" not in json.dumps(body) and "sensible" not in json.dumps(body)


def test_busy_error_is_retryable_with_retry_after():
    def busy(message, state, context):
        raise AgentBusyError("ocupado", retry_after_ms=1500)

    status, body = process(payload(), runtime(), busy)
    assert status == 503
    assert body["error"] == {"code": "busy", "message": "ocupado", "retryable": True, "retry_after_ms": 1500}


def test_runtime_configuration_error_is_reported_per_request():
    status, body = process(payload(), AgentConfigError("falta", code="model_config"), handle_message)
    assert status == 500 and body["error"]["code"] == "model_config"


def test_invalid_handler_result_is_a_configuration_error():
    status, body = process(payload(), runtime(), lambda m, s, c: {"reply": "no es AgentResult"})
    assert status == 500 and body["error"]["code"] == "invalid_result"


def test_files_require_a_store():
    def with_file(message, state, context):
        return AgentResult("ok", {}, [OutputFile("a.txt", b"x")])

    status, body = process(payload(), runtime(), with_file)
    assert status == 500 and body["error"]["code"] == "files_disabled"


def test_output_file_is_written_once_per_request_id(tmp_path):
    calls = []

    def with_file(message, state, context):
        calls.append(context.request_id)
        return AgentResult("ok", {}, [OutputFile("informe.txt", f"intento {len(calls)}".encode(), "text/plain")])

    rt = runtime(tmp_path)
    first = process(payload(), rt, with_file)
    retry = process(payload(), rt, with_file)  # mismo request_id: reintento del cliente
    assert first[0] == retry[0] == 200
    assert first[1]["files"][0]["id"] == "req-00000001/informe.txt"
    stored = tmp_path / "outputs" / "req-00000001" / "informe.txt"
    assert stored.read_bytes() == b"intento 1"  # el reintento no sobrescribe


def test_invalid_output_file_name_is_rejected(tmp_path):
    def bad_name(message, state, context):
        return AgentResult("ok", {}, [OutputFile("../../escape.txt", b"x")])

    status, body = process(payload(), runtime(tmp_path), bad_name)
    assert status == 400 and body["error"]["code"] == "invalid_file_name"
    assert not any((tmp_path).rglob("escape.txt"))


def test_context_carries_session_and_dependencies(tmp_path):
    seen = {}

    def spy(message, state, context: AgentContext):
        seen.update(request=context.request_id, session=context.session_id, files=context.files)
        return AgentResult("ok", state)

    rt = runtime(tmp_path)
    process(payload(), rt, spy, session_id="sess-1")
    assert seen == {"request": "req-00000001", "session": "sess-1", "files": rt.files}


def test_build_runtime_is_explicit():
    assert build_runtime({}).responder == "echo"
    assert build_runtime({}).files is None
    with pytest.raises(AgentConfigError):
        build_runtime({"STARTER_RESPONDER": "model"})  # sin MODEL_ENDPOINT: no hay respaldo silencioso
    with pytest.raises(AgentConfigError):
        build_runtime({"FILES_MODE": "blob"})
    with pytest.raises(AgentConfigError):
        build_runtime({"FILES_MODE": "s3"})
