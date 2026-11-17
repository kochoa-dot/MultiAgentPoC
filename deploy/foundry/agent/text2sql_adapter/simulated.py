"""Dobles deterministas para probar el puente sin modelo ni base de datos.

**No miden la calidad del modelo ni acceden a datos reales.**

- ``SimulatedGenerator`` se usa con ``STARTER_RESPONDER=echo`` (sin modelo). Devuelve el SQL
  de los ejemplos de ``text2sql_core.ejemplos_sql`` que corresponde a la pregunta, con el
  identificador numérico de la pregunta, o ``NO_RESPONDIBLE``. El SQL resultante pasa por la
  misma validación AST que el SQL de un modelo real.
- ``SimulatedDatabase`` se usa con ``TEXT2SQL_SQL_BACKEND=simulated``. Revalida el SQL, como
  ``text2sql_core.ejecutar_sql``, y devuelve filas sintéticas con las columnas pedidas.

Marcadores para provocar fallos (solo tienen efecto en estos dobles):

- ``[simular-ocupado]`` o ``[simular-ocupado:N]``: el modelo responde «ocupado» (503,
  reintentable) en los N primeros intentos del mismo ``request_id`` (por defecto 1).
- ``[simular-sql-invalido]``: el generador devuelve un ``DELETE`` que el validador rechaza.
- ``[simular-bd-ocupada]`` o ``[simular-bd-ocupada:N]``: timeout transitorio de la base.
- ``[simular-error-bd]``: error de la base con detalles sensibles que no deben mostrarse.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from collections import OrderedDict
from datetime import date
from decimal import Decimal

import sqlglot
from sqlglot import exp

import text2sql_core as core
from starter_agent.contract import AgentBusyError

_BUSY = re.compile(r"\[simular-ocupado(?::(\d{1,2}))?\]")
_DB_BUSY = re.compile(r"\[simular-bd-ocupada(?::(\d{1,2}))?\]")
INVALID_SQL = "[simular-sql-invalido]"
DB_ERROR = "[simular-error-bd]"
_ID = re.compile(r"\b\d{6,12}\b")
# Literales de los ejemplos del núcleo que se sustituyen por el identificador de la pregunta.
# Son datos de prueba sintéticos (suministro y ruta), no identificadores de clientes reales.
_EXAMPLE_IDS = ("0099000001", "99000001")
# (palabras clave sin acentos, índice en ejemplos_sql). El orden importa: primero lo no respondible.
_RULES = (
    (("croquis", "foto", "documento"), 7),
    (("factur",), 8),
    (("debo colocar", "que nota", "cual nota"), 9),
    (("reclamo",), 10),
    (("contiguo", "vecino"), 11),
    (("terreno", "no me aparece"), 3),
    (("coordenad",), 0),
    (("direccion",), 2),
    (("nota",), 4),
    (("ultima lectura",), 5),
    (("medidor",), 1),
    (("cuantos suministros",), 6),
)

_attempts: OrderedDict[tuple[str, str], int] = OrderedDict()
_lock = threading.Lock()


def _fails_now(kind: str, request_id: str, times: int) -> bool:
    """True durante los ``times`` primeros intentos de ``request_id`` (simula un fallo transitorio)."""
    with _lock:
        key = (kind, request_id)
        count = _attempts.pop(key, 0) + 1
        _attempts[key] = count
        while len(_attempts) > 1000:
            _attempts.popitem(last=False)
    return count <= times


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in plain if not unicodedata.combining(char))


def example_sql(question: str, config: core.Configuracion) -> str:
    """SQL de ``ejemplos_sql`` que corresponde a la pregunta, o ``NO_RESPONDIBLE``."""
    examples = core.ejemplos_sql(config)
    text = _normalize(question)
    for keywords, index in _RULES:
        if any(keyword in text for keyword in keywords):
            sql = examples[index][1]
            if sql == "NO_RESPONDIBLE":
                return sql
            match = _ID.search(question)
            if not match:  # mismo criterio que el prompt: sin identificador no hay consulta
                return "NO_RESPONDIBLE"
            for literal in _EXAMPLE_IDS:
                sql = sql.replace(f"'{literal}'", f"'{match.group(0)}'")
            return sql
    return "NO_RESPONDIBLE"


class SimulatedGenerator:
    """Sustituye al modelo. Firma igual que el generador real: ``(pregunta, config) -> sql``."""

    def __init__(self, request_id: str):
        self.request_id = request_id

    def __call__(self, question: str, config: core.Configuracion) -> str:
        busy = _BUSY.search(question)
        if busy and _fails_now("model", self.request_id, int(busy.group(1) or 1)):
            raise AgentBusyError("Simulación: el modelo está ocupado. Reintenta.", code="model_rate_limited")
        if INVALID_SQL in question:
            return f"DELETE FROM {config.nombre_sql}"
        return example_sql(question, config)


def _synthetic_value(name: str, row: int, filters: dict[str, str]):
    if name in filters:
        return filters[name]
    if name in {"X", "Y", "DS latitud", "DS longitud"}:
        return Decimal("13.6929") + row  # Decimal: comprueba la serialización del núcleo
    if name in {"FeLectEfec", "FeLectPlan"}:
        return date(2026, 10, 6)
    return f"SIM-{row + 1}"


class SimulatedDatabase:
    """Sustituye a ``text2sql_core.ejecutar_sql``. Firma: ``(sql, config) -> (columnas, filas)``."""

    def __init__(self, question: str, request_id: str):
        self.question = question
        self.request_id = request_id

    def __call__(self, sql: str, config: core.Configuracion) -> tuple[list[str], list[tuple]]:
        validated = core.validar_sql(sql, config)  # como ejecutar_sql: revalida antes de «conectar»
        busy = _DB_BUSY.search(self.question)
        if busy and _fails_now("db", self.request_id, int(busy.group(1) or 1)):
            raise TimeoutError("Simulación: la consulta superó el timeout.")
        if DB_ERROR in self.question:
            raise RuntimeError("Password=SECRETO; Server=PRIVADO")  # nunca debe llegar al usuario
        tree = sqlglot.parse_one(validated, read="tsql")
        filters = {eq.this.name: eq.expression.this for eq in tree.find_all(exp.EQ)
                   if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal)}
        top = int(tree.args["limit"].expression.this)
        columns = [item.alias_or_name for item in tree.expressions]
        if any(item.find(exp.Count) for item in tree.expressions):
            return columns, [tuple(3 for _ in columns)]
        rows = [tuple(_synthetic_value(name, row, filters) for name in columns) for row in range(min(top, 2))]
        return columns, rows
