"""Opciones de ejecución del puente Text-to-SQL.

No contienen secretos ni describen el alojamiento:

- ``TEXT2SQL_EXECUTE``: ``false`` (por defecto, igual que ``responder_pregunta(ejecutar=False)``)
  genera y valida SQL sin conectarse; ``true`` ejecuta el SQL validado.
- ``TEXT2SQL_SQL_BACKEND``: ``odbc`` (por defecto, ``text2sql_core.ejecutar_sql``) o
  ``simulated`` (base de datos simulada, solo para pruebas locales).

El esquema, la vista, los límites y la conexión siguen en las variables ``SQL_*`` que
lee ``text2sql_core.Configuracion.desde_entorno``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from starter_agent.contract import AgentConfigError

_TRUE = {"1", "true", "yes", "si", "sí"}
_FALSE = {"", "0", "false", "no"}
BACKENDS = ("odbc", "simulated")


@dataclass(frozen=True)
class Text2SqlSettings:
    execute: bool = False
    sql_backend: str = "odbc"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Text2SqlSettings":
        env = os.environ if env is None else env
        execute = env.get("TEXT2SQL_EXECUTE", "").strip().lower()
        if execute not in _TRUE | _FALSE:
            raise AgentConfigError("TEXT2SQL_EXECUTE debe ser 'true' o 'false'.", code="text2sql_config")
        backend = env.get("TEXT2SQL_SQL_BACKEND", "").strip().lower() or "odbc"
        if backend not in BACKENDS:
            raise AgentConfigError("TEXT2SQL_SQL_BACKEND debe ser 'odbc' o 'simulated'.", code="text2sql_config")
        return cls(execute=execute in _TRUE, sql_backend=backend)
