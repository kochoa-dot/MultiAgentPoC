"""Procesa un turno sin depender del transporte (Foundry, HTTP local o en proceso).

``process`` valida la solicitud, invoca ``handle_message``, guarda los archivos
y devuelve ``(status_http, cuerpo_json)``. Los adaptadores solo traducen HTTP.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from .contract import (CONTRACT_VERSION, MAX_FILE_BYTES, MAX_FILES_PER_TURN, MAX_MESSAGE_CHARS,
                       MAX_STATE_BYTES, REQUEST_ID_PATTERN, AgentConfigError, AgentContext, AgentError,
                       AgentInputError, AgentResult, error_body, file_id)
from .runtime import Runtime

logger = logging.getLogger("starter_agent")
Handler = Callable[[str, dict, AgentContext], AgentResult]


def _state_size(state: Any) -> int:
    try:
        return len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_STATE_BYTES + 1


def parse_request(payload: Any) -> tuple[str, str, dict[str, Any]]:
    """Devuelve ``(request_id, message, state)`` o lanza ``AgentInputError``."""
    if not isinstance(payload, dict):
        raise AgentInputError("La solicitud debe ser un objeto JSON.", code="invalid_request")
    contract = payload.get("contract")
    if contract is not None and contract != CONTRACT_VERSION:
        raise AgentInputError(f"Versión de contrato no admitida: {contract!r}.", code="unsupported_contract")
    request_id = payload.get("request_id")
    if request_id is None:
        request_id = uuid.uuid4().hex  # p. ej., «azd ai agent invoke»; sin idempotencia entre reintentos
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.match(request_id):
        raise AgentInputError("request_id no válido.", code="invalid_request")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise AgentInputError("El mensaje está vacío.", code="empty_message")
    if len(message) > MAX_MESSAGE_CHARS:
        raise AgentInputError(f"El mensaje supera {MAX_MESSAGE_CHARS} caracteres.", code="message_too_long")
    state = payload.get("state", {})
    if state is None:
        state = {}
    if not isinstance(state, dict) or _state_size(state) > MAX_STATE_BYTES:
        raise AgentInputError("El estado no es válido o es demasiado grande.", code="invalid_state")
    return request_id, message.strip(), state


def process(payload: Any, runtime: Runtime | AgentError, handler: Handler, *,
            session_id: str | None = None, invocation_id: str | None = None) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    received_state = payload.get("state") if isinstance(payload, dict) else None
    try:
        request_id, message, state = parse_request(payload)
        received_state = state
        if isinstance(runtime, AgentError):
            raise runtime
        context = AgentContext(request_id=request_id, session_id=session_id, invocation_id=invocation_id,
                               model=runtime.model, files=runtime.files)
        result = handler(message, state, context)
        if (not isinstance(result, AgentResult) or not isinstance(result.reply, str)
                or not isinstance(result.state, dict)):
            raise AgentConfigError("El handler devolvió un resultado no válido.", code="invalid_result")
        if _state_size(result.state) > MAX_STATE_BYTES:
            raise AgentConfigError("El estado devuelto supera el límite.", code="state_too_large")
        if len(result.files) > MAX_FILES_PER_TURN:
            raise AgentConfigError("Demasiados archivos en un turno.", code="too_many_files")
        files = []
        for output in result.files:
            if runtime.files is None:
                raise AgentConfigError("El handler generó archivos, pero FILES_MODE=none.", code="files_disabled")
            if len(output.content) > MAX_FILE_BYTES:
                raise AgentConfigError("Archivo generado demasiado grande.", code="file_too_large")
            identifier = file_id(request_id, output.name)
            runtime.files.save_output(identifier, output.content, output.content_type)
            files.append({"id": identifier, "name": output.name, "content_type": output.content_type,
                          "size": len(output.content)})
        logger.info("Turn completed: request_id=%s elapsed_ms=%d files=%d", request_id,
                    int((time.monotonic() - started) * 1000), len(files))
        return 200, {"contract": CONTRACT_VERSION, "request_id": request_id, "reply": result.reply,
                     "state": result.state, "files": files}
    except AgentError as exc:
        level = logging.WARNING if exc.http_status < 500 or exc.retryable else logging.ERROR
        logger.log(level, "Turn failed: request_id=%s code=%s status=%d", request_id, exc.code, exc.http_status)
        return exc.http_status, error_body(exc, request_id if isinstance(request_id, str) else None,
                                           received_state)
    except Exception as exc:  # noqa: BLE001 - nunca exponer detalles ni contenido
        logger.error("Turn crashed: request_id=%s type=%s", request_id, type(exc).__name__)
        error = AgentError("No pude completar la operación. Revisa los registros del agente.", code="internal_error")
        return 500, error_body(error, request_id if isinstance(request_id, str) else None, received_state)
