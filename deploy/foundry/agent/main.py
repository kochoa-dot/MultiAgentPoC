"""Adaptador Foundry: protocolo ``invocations`` 2.0.0 con ``azure-ai-agentserver-invocations``.

Foundry ejecuta ``python main.py`` desde la raíz del ZIP; ``starter_agent/``
está junto a este archivo, así que se importa sin instalar paquetes propios.
En local, ``python agent/main.py`` expone ``POST http://localhost:8088/invocations``
(puerto configurable con ``PORT``).

El adaptador solo traduce HTTP <-> ``service.process``. La lógica está en
``starter_agent/handler.py``.
"""
from __future__ import annotations

import logging

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from starter_agent.contract import AgentError, AgentInputError, error_body
from starter_agent.handler import handle_message
from starter_agent.runtime import Runtime, build_runtime
from starter_agent.service import process

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("starter_agent")


def _load_runtime() -> Runtime | AgentError:
    """Valida la configuración al arrancar; si falla, cada turno devuelve ``misconfigured``.

    Se prefiere un error explícito por solicitud a un contenedor que se reinicia
    en bucle sin poder diagnosticarse desde la interfaz.
    """
    try:
        runtime = build_runtime()
        logger.info("Runtime ready: responder=%s files=%s", runtime.responder, runtime.files_mode)
        return runtime
    except AgentError as exc:
        logger.error("Runtime misconfigured: code=%s detail=%s", exc.code, exc.message)
        return exc


RUNTIME = _load_runtime()
app = InvocationAgentServerHost()


@app.invoke_handler
async def handle(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        error = AgentInputError("El cuerpo de la solicitud debe ser JSON.", code="invalid_json")
        return JSONResponse(error_body(error, None, None), status_code=400)
    status, body = await run_in_threadpool(
        process, payload, RUNTIME, handle_message,
        session_id=getattr(request.state, "session_id", None) or None,
        invocation_id=getattr(request.state, "invocation_id", None) or None,
    )
    return JSONResponse(body, status_code=status)


if __name__ == "__main__":
    app.run()
