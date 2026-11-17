"""Adaptador local: ejecuta la misma lógica sin HTTP, sin Azure y sin credenciales.

Uso desde terminal::

    python -m starter_agent.local          # chat interactivo
    python -m starter_agent.local --once "hola"

La interfaz Streamlit lo usa en ``AGENT_MODE=inprocess``. Respeta las mismas
variables que Foundry (``STARTER_RESPONDER``, ``FILES_MODE``...), por lo que
``STARTER_RESPONDER=model`` exigiría credenciales: el modo local por defecto es
``echo`` y ``FILES_MODE=local``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Mapping
from typing import Any

from .contract import CONTRACT_VERSION, AgentError
from .handler import handle_message
from .runtime import Runtime, build_runtime
from .service import process

LOCAL_DEFAULTS = {"STARTER_RESPONDER": "echo", "FILES_MODE": "local", "LOCAL_FILES_DIR": ".local-files"}


class LocalAgent:
    """Equivalente local del endpoint: ``invoke(payload, session_id) -> (status, body, session_id)``."""

    def __init__(self, env: Mapping[str, str] | None = None, handler=handle_message):
        merged = {**LOCAL_DEFAULTS, **(dict(os.environ) if env is None else dict(env))}
        try:
            self.runtime: Runtime | AgentError = build_runtime(merged)
        except AgentError as exc:
            self.runtime = exc
        self.handler = handler

    def invoke(self, payload: dict[str, Any], session_id: str | None = None) -> tuple[int, dict[str, Any], str]:
        session = session_id or f"local-{uuid.uuid4().hex[:12]}"
        status, body = process(payload, self.runtime, self.handler, session_id=session,
                               invocation_id=uuid.uuid4().hex)
        return status, body, session


def _turn(agent: LocalAgent, message: str, state: dict, session: str | None) -> tuple[dict, str]:
    payload = {"contract": CONTRACT_VERSION, "request_id": uuid.uuid4().hex, "message": message, "state": state}
    status, body, session = agent.invoke(payload, session)
    if status != 200:
        print(f"[{status}] {body['error']['code']}: {body['error']['message']}")
        return state, session
    print(body["reply"])
    for item in body.get("files", []):
        print(f"  archivo: {item['id']} ({item['size']} bytes)")
    return body["state"], session


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Chat local con el agente, sin Azure.")
    parser.add_argument("--once", help="Envía un solo mensaje e imprime la respuesta JSON.")
    args = parser.parse_args(argv)
    agent = LocalAgent()
    if args.once:
        status, body, _ = agent.invoke({"contract": CONTRACT_VERSION, "request_id": uuid.uuid4().hex,
                                        "message": args.once, "state": {}})
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0 if status == 200 else 1
    state: dict = {}
    session = None
    print("Chat local. Escribe /nuevo para reiniciar y /salir para terminar.")
    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if message == "/salir":
            return 0
        if message == "/nuevo":
            state, session = {}, None
            print("Conversación nueva.")
            continue
        if message:
            state, session = _turn(agent, message, state, session)


if __name__ == "__main__":
    raise SystemExit(main())
