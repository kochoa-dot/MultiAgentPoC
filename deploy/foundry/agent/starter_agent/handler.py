"""Punto de extensión: conecta el agente Text-to-SQL con el contrato ``starter.v1``.

Firma (sin cambios respecto a la base)::

    handle_message(message: str, state: dict, context: AgentContext) -> AgentResult

- La lógica Text-to-SQL vive en ``agent/text2sql_core.py`` (copia literal del agente
  original) y el puente en ``agent/text2sql_adapter/bridge.py``. Este archivo no contiene
  lógica de dominio.
- ``context.model`` es el modelo configurado con ``MODEL_*`` (``STARTER_RESPONDER=model``);
  si es ``None`` (``echo``) se usa el generador simulado y la respuesta lo indica.
- ``state`` es el historial acotado y versionado que devolvió el turno anterior (``{}`` al
  empezar). Se valida como entrada no confiable.
- Errores: ``AgentInputError`` (estado o comando no válidos), ``AgentBusyError`` (modelo o
  base de datos ocupados; reintentable con el mismo ``request_id``) y ``AgentConfigError``
  (falta configuración ``SQL_*``, ``TEXT2SQL_*`` o del modelo).
- Las consultas son lecturas validadas; un reintento con el mismo ``request_id`` no tiene
  efectos externos y ``resultado.json`` se escribe una sola vez por ``request_id``.
"""
from __future__ import annotations

from typing import Any

from text2sql_adapter.bridge import answer

from .contract import AgentContext, AgentResult


def handle_message(message: str, state: dict[str, Any], context: AgentContext) -> AgentResult:
    return answer(message, state, context)
