"""Cliente web: URL y token Foundry, sesión, formatos JSON/SSE, errores y reintentos idempotentes."""
import json

import pytest
import requests

from web.client import (FOUNDRY_SCOPE, AgentCallError, AgentClient, FoundryTransport, RawResponse,
                        interpret, _read_sse)

ENDPOINT = "https://acct.services.ai.azure.com/api/projects/proj"


class FakeCredential:
    def __init__(self):
        self.scopes = []

    def get_token(self, scope):
        self.scopes.append(scope)
        return type("Token", (), {"token": "test-token"})()


class FakeHttpResponse:
    def __init__(self, status, body, headers=None, lines=None):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self._lines = lines

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines or [])


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(request_id, reply="hola", state=None, session="sess-1"):
    return FakeHttpResponse(200, {"contract": "starter.v1", "request_id": request_id, "reply": reply,
                                  "state": state or {"turns": 1}, "files": []},
                            {"x-agent-session-id": session})


def make_client(responses, retries=2):
    http = FakeSession(responses)
    credential = FakeCredential()
    transport = FoundryTransport(ENDPOINT, "my-agent", credential, http=http)
    sleeps = []
    return AgentClient(transport, max_retries=retries, sleep=sleeps.append), http, credential, sleeps


def test_foundry_url_token_and_session_parameters():
    client, http, credential, _ = make_client([ok("r1-000000"), ok("r2-000000")])
    first = client.send("hola", {}, None, "r1-000000")
    client.send("sigue", first.state, first.session_id, "r2-000000")
    url, kwargs = http.calls[0]
    assert url == f"{ENDPOINT}/agents/my-agent/endpoint/protocols/invocations"
    assert kwargs["params"] == {"api-version": "v1"}
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert credential.scopes[0] == FOUNDRY_SCOPE
    assert http.calls[1][1]["params"] == {"api-version": "v1", "agent_session_id": "sess-1"}
    assert http.calls[1][1]["json"]["state"] == {"turns": 1}
    assert http.calls[1][1]["json"]["contract"] == "starter.v1"


def test_http_endpoint_must_be_https():
    with pytest.raises(ValueError):
        FoundryTransport("http://insecure", "a", FakeCredential())


def test_rate_limit_is_retried_with_same_request_id_and_retry_after():
    busy = FakeHttpResponse(429, {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
                            {"retry-after": "3"})
    client, http, _, sleeps = make_client([busy, ok("req-0000001")])
    result = client.send("hola", {}, None, "req-0000001")
    assert result.reply == "hola"
    assert [call[1]["json"]["request_id"] for call in http.calls] == ["req-0000001", "req-0000001"]
    assert sleeps and sleeps[0] >= 3


def test_agent_busy_error_is_retried_then_surfaces():
    busy = FakeHttpResponse(503, {"contract": "starter.v1", "request_id": "req-0000001",
                                  "error": {"code": "busy", "message": "Modelo ocupado", "retryable": True,
                                            "retry_after_ms": 0}, "state": {}})
    client, http, _, _ = make_client([busy, busy, busy], retries=2)
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {}, None, "req-0000001")
    assert error.value.retryable and error.value.message == "Modelo ocupado"
    assert len(http.calls) == 3


def test_session_quota_is_not_retried():
    quota = FakeHttpResponse(429, {"error": {"code": "session_quota_exceeded", "message": "quota"}})
    client, http, _, _ = make_client([quota])
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {}, None, "req-0000001")
    assert error.value.code == "session_quota_exceeded" and not error.value.retryable
    assert "sesiones" in error.value.message and len(http.calls) == 1


def test_read_timeout_is_not_retried_automatically():
    client, http, _, _ = make_client([requests.ReadTimeout()])
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {}, "sess-9", "req-0000001")
    assert error.value.code == "timeout" and error.value.retryable
    assert error.value.session_id == "sess-9" and len(http.calls) == 1


def test_connection_error_is_retried():
    client, http, _, _ = make_client([requests.ConnectionError(), ok("req-0000001")])
    assert client.send("hola", {}, None, "req-0000001").reply == "hola"
    assert len(http.calls) == 2


@pytest.mark.parametrize("status, code", [(401, "unauthorized"), (403, "forbidden"), (404, "agent_not_found")])
def test_platform_errors_are_explained(status, code):
    client, _, _, _ = make_client([FakeHttpResponse(status, {"error": {"code": "PermissionDenied"}})])
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {}, None, "req-0000001")
    assert error.value.code == code and not error.value.retryable


def test_input_error_from_agent_is_not_retried():
    bad = FakeHttpResponse(400, {"contract": "starter.v1", "error": {"code": "invalid_state", "message": "Estado",
                                                                     "retryable": False}, "state": {}})
    client, http, _, _ = make_client([bad])
    with pytest.raises(AgentCallError) as error:
        client.send("hola", {}, None, "req-0000001")
    assert error.value.code == "invalid_state" and len(http.calls) == 1


def test_incomplete_or_mismatched_responses_fail():
    with pytest.raises(AgentCallError, match="incompleta"):
        interpret(RawResponse(200, {}, {"reply": "sin estado"}), "req-0000001", None)
    with pytest.raises(AgentCallError, match="no corresponde"):
        interpret(RawResponse(200, {}, {"reply": "x", "state": {}, "request_id": "otro-000000"}), "req-0000001", None)


def test_sse_response_is_supported():
    lines = ["event: message", "data: " + json.dumps({"reply": "hola", "state": {"a": 1}}),
             "data: " + json.dumps({"type": "done", "session_id": "sess-sse", "invocation_id": "i"})]
    body, session = _read_sse(lines)
    assert body["reply"] == "hola" and session == "sess-sse"
    sse = FakeHttpResponse(200, None, {"content-type": "text/event-stream"}, lines)
    client, _, _, _ = make_client([sse])
    result = client.send("hola", {}, None, "req-0000001")
    assert result.session_id == "sess-sse" and result.state == {"a": 1}


def test_file_references_are_parsed():
    raw = RawResponse(200, {"x-agent-session-id": "s"}, {"reply": "ok", "state": {}, "files": [
        {"id": "req-0000001/a.txt", "name": "a.txt", "content_type": "text/plain", "size": 3}]})
    result = interpret(raw, "req-0000001", None)
    assert result.files[0].id == "req-0000001/a.txt"
