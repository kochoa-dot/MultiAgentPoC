"""Contrato entre la interfaz y el agente.

Formato de la solicitud (JSON, cuerpo de ``POST /invocations``)::

    {
      "contract": "starter.v1",          # opcional; si viene debe coincidir
      "request_id": "<8-64 [A-Za-z0-9_-]>",  # clave de idempotencia del turno
      "message": "<texto, 1-4000 caracteres>",
      "state": { ... }                    # estado devuelto en el turno anterior
    }

Respuesta correcta (HTTP 200)::

    {
      "contract": "starter.v1",
      "request_id": "...",
      "reply": "<texto>",
      "state": { ... },                   # el cliente lo guarda y lo reenvía
      "files": [{"id": "<request_id>/<nombre>", "name": "...",
                 "content_type": "...", "size": 123}]
    }

Respuesta de error (HTTP 400/500/503)::

    {
      "contract": "starter.v1",
      "request_id": "...",
      "error": {"code": "...", "message": "<texto apto para el usuario>",
                "retryable": true|false, "retry_after_ms": 0},
      "state": { ... }                    # el estado recibido, sin cambios
    }

El estado lo conserva el cliente y viaja completo en cada turno. Así una
conversación sobrevive a la pérdida del sandbox de la sesión Foundry y a la
publicación de una versión nueva. El agente debe tratarlo como entrada no
confiable y validarlo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from .files import FileStore

CONTRACT_VERSION = "starter.v1"
MAX_MESSAGE_CHARS = 4000
MAX_STATE_BYTES = 64_000
MAX_FILES_PER_TURN = 5
MAX_FILE_BYTES = 10 * 1024 * 1024

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ModelClient(Protocol):
    """Lo mínimo que la lógica del agente necesita de un modelo."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class AgentContext:
    """Datos del turno que no forman parte de la conversación.

    ``model`` y ``files`` son ``None`` cuando la configuración no los habilita;
    la lógica decide si eso es un error (``AgentConfigError``) o si continúa.
    """

    request_id: str
    session_id: str | None = None
    invocation_id: str | None = None
    model: ModelClient | None = None
    files: "FileStore | None" = None


@dataclass(frozen=True)
class OutputFile:
    """Archivo generado en un turno. El servicio lo guarda de forma privada."""

    name: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class AgentResult:
    """Resultado de ``handle_message``."""

    reply: str
    state: dict[str, Any]
    files: list[OutputFile] = field(default_factory=list)


class AgentError(Exception):
    """Error controlado. ``message`` se muestra al usuario; no incluya datos sensibles."""

    code = "agent_error"
    http_status = 500
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retry_after_ms: int = 0):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.retry_after_ms = max(0, min(int(retry_after_ms or 0), 60_000))


class AgentInputError(AgentError):
    """La solicitud o el estado no son válidos. Reintentar igual no sirve."""

    code = "invalid_input"
    http_status = 400


class AgentBusyError(AgentError):
    """Dependencia saturada o lenta (cuota del modelo, timeout). Se puede reintentar."""

    code = "busy"
    http_status = 503
    retryable = True


class AgentConfigError(AgentError):
    """Falta configuración o permisos. Requiere intervención del operador."""

    code = "misconfigured"
    http_status = 500


def file_id(request_id: str, name: str) -> str:
    """Identificador privado de un archivo: ``<request_id>/<nombre>``."""
    if not REQUEST_ID_PATTERN.match(request_id) or not FILE_NAME_PATTERN.match(name) or ".." in name:
        raise AgentInputError("Nombre de archivo no válido.", code="invalid_file_name")
    return f"{request_id}/{name}"


def is_valid_file_id(value: object) -> bool:
    return isinstance(value, str) and bool(FILE_ID_PATTERN.match(value)) and ".." not in value


def error_body(error: AgentError, request_id: str | None, state: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "request_id": request_id,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "retry_after_ms": error.retry_after_ms,
        },
        "state": state if isinstance(state, dict) else {},
    }
