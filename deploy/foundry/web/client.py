"""Cliente de invocación del agente (contrato ``starter.v1``).

Transportes:

- ``FoundryTransport``: ``POST {proyecto}/agents/{nombre}/endpoint/protocols/invocations
  ?api-version=v1[&agent_session_id=...]`` con token ``https://ai.azure.com/.default``.
- ``LocalHttpTransport``: ``POST http://localhost:8088/invocations`` (``python agent/main.py``).
- ``InProcessTransport``: llama a ``starter_agent.local.LocalAgent`` sin red.

Reintentos (idempotentes: cada turno lleva un ``request_id`` que no cambia):

- Automáticos solo ante 429/502/503/504, errores marcados ``retryable`` por el
  agente o fallos de conexión, con espera exponencial y ``Retry-After``.
- Nunca ante un timeout de lectura: el agente pudo haber terminado. El error se
  entrega a la interfaz, que permite reintentar con el mismo ``request_id``.
- ``session_quota_exceeded`` no se reintenta: hay que liberar sesiones.
"""
from __future__ import annotations

import json
import random
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import requests

CONTRACT_VERSION = "starter.v1"
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
SESSION_HEADER = "x-agent-session-id"
RETRYABLE_STATUS = {429, 502, 503, 504}
NON_RETRYABLE_CODES = {"session_quota_exceeded", "subnet_exhausted"}


@dataclass(frozen=True)
class FileRef:
    id: str
    name: str
    content_type: str
    size: int


@dataclass(frozen=True)
class TurnResult:
    request_id: str
    reply: str
    state: dict[str, Any]
    session_id: str | None
    files: list[FileRef] = field(default_factory=list)


class AgentCallError(Exception):
    """Error presentable al usuario. ``retryable`` indica si tiene sentido reintentar."""

    def __init__(self, message: str, *, code: str, retryable: bool = False, status: int | None = None,
                 retry_after_ms: int = 0, session_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.session_id = session_id


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: Any


class Transport(Protocol):
    def post(self, payload: dict[str, Any], session_id: str | None, timeout: float) -> RawResponse: ...


def new_request_id() -> str:
    return uuid.uuid4().hex


def build_payload(message: str, state: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {"contract": CONTRACT_VERSION, "request_id": request_id, "message": message, "state": state}


# --- Lectura de respuestas -------------------------------------------------

def _read_http(response: requests.Response) -> RawResponse:
    headers = {key.lower(): value for key, value in response.headers.items()}
    content_type = headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        body, session = _read_sse(response.iter_lines(decode_unicode=True))
        if session:
            headers.setdefault(SESSION_HEADER, session)
    else:
        try:
            body = response.json()
        except ValueError:
            body = None
    return RawResponse(response.status_code, headers, body)


def _read_sse(lines) -> tuple[Any, str | None]:
    """Admite agentes que respondan en SSE: toma el último evento con ``reply`` o ``error``."""
    result, session = None, None
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(event, dict):
            session = event.get("session_id") or session
            if "reply" in event or "error" in event:
                result = event
    return result, session


def _retry_after_ms(raw: RawResponse) -> int:
    for key, factor in (("retry-after-ms", 1), ("retry-after", 1000)):
        value = raw.headers.get(key)
        if value:
            try:
                return int(float(value) * factor)
            except ValueError:
                continue
    error = raw.body.get("error") if isinstance(raw.body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("retry_after_ms"), int):
        return error["retry_after_ms"]
    return 0


_PLATFORM_MESSAGES = {
    401: ("unauthorized", "La credencial no fue aceptada por Foundry. Revisa la identidad de la aplicación."),
    403: ("forbidden", "La identidad de la aplicación no tiene permiso para invocar el agente (Foundry Agent Consumer)."),
    404: ("agent_not_found", "No se encontró el agente publicado. Verifica HOSTED_AGENT_NAME y la publicación."),
}


def interpret(raw: RawResponse, request_id: str, previous_session: str | None) -> TurnResult:
    """Convierte una respuesta en ``TurnResult`` o lanza ``AgentCallError``."""
    session = raw.headers.get(SESSION_HEADER) or previous_session
    body = raw.body if isinstance(raw.body, dict) else {}
    error = body.get("error")
    if raw.status == 200 and not error:
        if (body.get("contract", CONTRACT_VERSION) != CONTRACT_VERSION or not isinstance(body.get("reply"), str)
                or not isinstance(body.get("state"), dict)):
            raise AgentCallError("El agente devolvió una respuesta incompleta.", code="invalid_response",
                                 status=raw.status, session_id=session)
        if body.get("request_id") not in (None, request_id):
            raise AgentCallError("La respuesta no corresponde a la solicitud enviada.", code="request_mismatch",
                                 status=raw.status, session_id=session)
        files = []
        for item in body.get("files") or []:
            try:
                files.append(FileRef(id=str(item["id"]), name=str(item["name"]),
                                     content_type=str(item.get("content_type") or "application/octet-stream"),
                                     size=int(item.get("size") or 0)))
            except (KeyError, TypeError, ValueError):
                raise AgentCallError("El agente devolvió un archivo no válido.", code="invalid_response",
                                     status=raw.status, session_id=session) from None
        return TurnResult(request_id=request_id, reply=body["reply"], state=body["state"],
                          session_id=session, files=files)

    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    contract_error = body.get("contract") == CONTRACT_VERSION and isinstance(message, str)
    if raw.status in _PLATFORM_MESSAGES and not contract_error:
        code, message = _PLATFORM_MESSAGES[raw.status]
    if code == "session_quota_exceeded":
        message = "Se alcanzó el límite de sesiones activas del agente. Cierra conversaciones o espera a que expiren."
    retryable = (code not in NON_RETRYABLE_CODES
                 and (raw.status in RETRYABLE_STATUS or bool(isinstance(error, dict) and error.get("retryable"))))
    if not isinstance(message, str) or not message:
        message = ("El agente está ocupado. Reintenta en unos segundos." if retryable
                   else f"El agente devolvió un error (HTTP {raw.status}).")
    raise AgentCallError(message, code=str(code or f"http_{raw.status}"), retryable=retryable, status=raw.status,
                         retry_after_ms=_retry_after_ms(raw), session_id=session)


# --- Transportes -------------------------------------------------------------

@lru_cache(maxsize=4)
def _credential(kind: str, client_id: str | None):
    if kind == "developer":
        from azure.identity import AzureCliCredential

        return AzureCliCredential()
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(client_id=client_id) if client_id else ManagedIdentityCredential()


def credential_for(settings) -> Any:
    """``easyauth`` -> identidad administrada de la app; ``developer`` -> ``az login``."""
    return _credential(settings.auth, settings.managed_identity_client_id)


class FoundryTransport:
    def __init__(self, project_endpoint: str, agent_name: str, credential, *, api_version: str = "v1",
                 http: requests.Session | None = None):
        if not project_endpoint.startswith("https://"):
            raise ValueError("El endpoint del proyecto debe usar HTTPS.")
        self.url = f"{project_endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/invocations"
        self.credential = credential
        self.api_version = api_version
        self.http = http or requests.Session()

    def post(self, payload: dict[str, Any], session_id: str | None, timeout: float) -> RawResponse:
        token = self.credential.get_token(FOUNDRY_SCOPE).token
        params = {"api-version": self.api_version}
        if session_id:
            params["agent_session_id"] = session_id
        response = self.http.post(self.url, params=params, json=payload, timeout=(10, timeout),
                                  headers={"Authorization": f"Bearer {token}"})
        return _read_http(response)


class LocalHttpTransport:
    def __init__(self, base_url: str, *, http: requests.Session | None = None):
        self.url = base_url.rstrip("/") + "/invocations"
        self.http = http or requests.Session()

    def post(self, payload: dict[str, Any], session_id: str | None, timeout: float) -> RawResponse:
        params = {"agent_session_id": session_id} if session_id else {}
        return _read_http(self.http.post(self.url, params=params, json=payload, timeout=(5, timeout)))


class InProcessTransport:
    def __init__(self, agent_dir, env: dict[str, str] | None = None):
        path = str(agent_dir)
        if path not in sys.path:
            sys.path.insert(0, path)
        from starter_agent.local import LocalAgent  # importado solo en modo local

        self.agent = LocalAgent(env)

    def post(self, payload: dict[str, Any], session_id: str | None, timeout: float) -> RawResponse:
        status, body, session = self.agent.invoke(payload, session_id)
        return RawResponse(status, {SESSION_HEADER: session}, body)


def transport_for(settings) -> Transport:
    if settings.mode == "foundry":
        return FoundryTransport(settings.project_endpoint, settings.agent_name, credential_for(settings),
                                api_version=settings.api_version)
    if settings.mode == "local-http":
        return LocalHttpTransport(settings.local_agent_url)
    env = {"FILES_MODE": "local" if settings.files_mode == "local" else "none",
           "LOCAL_FILES_DIR": str(settings.local_files_dir), "STARTER_RESPONDER": "echo"}
    return InProcessTransport(settings.local_agent_dir, env)


# --- Cliente con política de reintentos ---------------------------------------

class AgentClient:
    def __init__(self, transport: Transport, *, timeout_seconds: float = 120.0, max_retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep, max_delay_seconds: float = 20.0):
        self.transport = transport
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.sleep = sleep
        self.max_delay = max_delay_seconds

    def send(self, message: str, state: dict[str, Any], session_id: str | None,
             request_id: str | None = None) -> TurnResult:
        request_id = request_id or new_request_id()
        payload = build_payload(message, state, request_id)
        attempt = 0
        while True:
            try:
                raw = self.transport.post(payload, session_id, self.timeout)
                return interpret(raw, request_id, session_id)
            except requests.ReadTimeout:
                raise AgentCallError("El agente no respondió dentro del tiempo límite. Al reintentar se reenvía "
                                     "el mismo turno con el mismo estado, sin duplicarlo.",
                                     code="timeout", retryable=True, session_id=session_id) from None
            except requests.ConnectionError:
                error = AgentCallError("No se pudo conectar con el agente.", code="connection_error",
                                       retryable=True, session_id=session_id)
            except AgentCallError as exc:
                error = exc
            except Exception as exc:  # credenciales, DNS, etc.
                name = type(exc).__name__
                if name in {"CredentialUnavailableError", "ClientAuthenticationError"}:
                    raise AgentCallError("No se pudo obtener un token Entra para invocar el agente.",
                                         code="credential_error", session_id=session_id) from None
                raise
            if not error.retryable or attempt >= self.max_retries:
                raise error
            delay = max(error.retry_after_ms / 1000, 1.5 * (2 ** attempt)) + random.uniform(0, 0.5)
            self.sleep(min(delay, self.max_delay))
            attempt += 1
            session_id = error.session_id or session_id
