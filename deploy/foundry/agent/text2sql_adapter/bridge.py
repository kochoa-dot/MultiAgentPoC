"""Traduce un turno ``starter.v1`` a la lógica de ``text2sql_core`` y de vuelta.

Del núcleo se reutilizan, sin cambios: ``Configuracion.desde_entorno`` (variables ``SQL_*``),
``construir_prompt`` (instrucciones y ejemplos), ``validar_sql`` (AST, vista, columnas, TOP),
``ejecutar_sql`` (revalidación, metadatos reales y lectura limitada) y ``RespuestaAgente``
(``to_dict`` con la serialización de Decimal/fechas/bytes).

``responder`` reproduce las etapas y los mensajes de ``core.responder_pregunta`` con dos
puntos inyectados: el generador (modelo del contexto o doble simulado) y el ejecutor. Las
pruebas comparan ambos resultados (``tests/test_text2sql_bridge.py``). Diferencias deliberadas:

- Los errores del modelo ya traducidos por ``starter_agent.model`` (cuota, timeout, permisos)
  se propagan como ``AgentBusyError``/``AgentConfigError`` para que la web pueda reintentar.
- Un timeout o fallo de comunicación de la base (``pyodbc.OperationalError``) se propaga como
  ``AgentBusyError``: reintentar una lectura validada no tiene efectos.
- Un error de configuración SQL es ``AgentConfigError`` (500, operador) en lugar de una
  respuesta con ``ok=False``.

Nunca se registra la pregunta ni el resultado: solo metadatos.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import text2sql_core as core
from starter_agent.contract import (AgentBusyError, AgentConfigError, AgentContext, AgentError, AgentInputError,
                                    AgentResult, ModelClient, OutputFile)

from .settings import Text2SqlSettings
from .simulated import SimulatedDatabase, SimulatedGenerator

logger = logging.getLogger("text2sql_adapter")

STATE_VERSION = 1
MAX_HISTORY = 10
MAX_QUESTION_IN_STATE = 500
MAX_SQL_IN_STATE = 4000
MAX_ROWS_IN_REPLY = 20
RESULT_FILE = "resultado.json"
RESULTS = ("ok", "no_respondible", "error")

Generator = Callable[[str, core.Configuracion], str]
Executor = Callable[[str, core.Configuracion], "tuple[list[str], list[tuple]]"]

# Datos de prueba sintéticos: los identificadores de suministro (0099000001...) y ruta (99000001) de los
# ejemplos no corresponden a clientes ni rutas reales.
HELP = (
    "Escribe una pregunta sobre la vista autorizada. El agente genera T-SQL con las instrucciones de "
    "`text2sql_core`, lo valida (un SELECT simple sobre la vista, columnas autorizadas y TOP máximo) y, "
    "solo si `TEXT2SQL_EXECUTE=true`, lo ejecuta.\n\n"
    "Ejemplos:\n"
    "- Quiero las coordenadas disponibles del suministro 0099000001\n"
    "- Datos del medidor del suministro 0099000001\n"
    "- Última lectura KWH registrada del suministro 0099000001\n"
    "- Cuántos suministros distintos tiene la ruta 99000001\n\n"
    "Comandos: `/ayuda`, `/historial`.\n\n"
    "Solo en simulación (`STARTER_RESPONDER=echo` y/o `TEXT2SQL_SQL_BACKEND=simulated`): añade "
    "`[simular-ocupado:N]`, `[simular-sql-invalido]`, `[simular-bd-ocupada:N]` o `[simular-error-bd]` "
    "a la pregunta para provocar errores y reintentos."
)

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~])")


# --- Generación y orquestación ----------------------------------------------------------

def model_messages(question: str, config: core.Configuracion) -> list[dict[str, str]]:
    """Los mismos mensajes que envía ``core.generar_sql``: instrucciones + pregunta."""
    return [{"role": "system", "content": core.construir_prompt(config)},
            {"role": "user", "content": question.strip()}]


def model_generator(model: ModelClient) -> Generator:
    def generate(question: str, config: core.Configuracion) -> str:
        content = model.complete(model_messages(question, config))
        if not content:
            raise ValueError("El modelo no devolvió contenido SQL.")  # mismo mensaje que generar_sql
        return content.strip()

    return generate


def _is_transient_db_error(exc: Exception) -> bool:
    kind = type(exc)
    return isinstance(exc, TimeoutError) or (kind.__module__ == "pyodbc" and kind.__name__ == "OperationalError")


def responder(question: str, *, generate: Generator, execute: Executor, run_sql: bool,
              config: core.Configuracion) -> core.RespuestaAgente:
    """Etapas y mensajes de ``core.responder_pregunta`` con generador y ejecutor inyectados."""
    resultado = core.RespuestaAgente(pregunta=question, ok=False)
    etapa = "generar"
    try:
        sql = generate(question, config)
        resultado.sql_generado = sql
        if sql.strip().upper() == "NO_RESPONDIBLE":
            resultado.no_respondible = True
            resultado.error = "Falta información, contexto o una fuente autorizada para responder."
            return resultado
        etapa = "validar"
        resultado.sql_validado = core.validar_sql(sql, config)
        if run_sql:
            etapa = "ejecutar"
            resultado.columnas, resultado.filas = execute(resultado.sql_validado, config)
            resultado.sql_ejecutado = resultado.sql_validado
            resultado.ejecutado = True
        resultado.ok = True
    except AgentError:
        logger.warning("Text-to-SQL: etapa=%s error de contrato", etapa)
        raise
    except ValueError as exc:
        resultado.error = (str(exc) if etapa == "validar"
                           else f"No fue posible {etapa} la consulta. Revisa la configuración y los metadatos.")
        logger.warning("Text-to-SQL: etapa=%s tipo=%s", etapa, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - nunca reenviar detalles de BD/SDK
        logger.warning("Text-to-SQL: etapa=%s tipo=%s", etapa, type(exc).__name__)
        if etapa == "ejecutar" and _is_transient_db_error(exc):
            raise AgentBusyError("La base de datos no respondió a tiempo. Reintenta.", code="sql_unavailable") from None
        resultado.error = f"No fue posible {etapa} la consulta. Revisa conexión, permisos y configuración."
    return resultado


# --- Estado de la conversación -----------------------------------------------------------

def _valid_entry(entry: Any) -> bool:
    return (isinstance(entry, dict) and set(entry) == {"pregunta", "resultado", "sql", "ejecutado"}
            and isinstance(entry["pregunta"], str) and len(entry["pregunta"]) <= MAX_QUESTION_IN_STATE
            and entry["resultado"] in RESULTS
            and (entry["sql"] is None or (isinstance(entry["sql"], str) and len(entry["sql"]) <= MAX_SQL_IN_STATE))
            and isinstance(entry["ejecutado"], bool))


def load_state(state: dict[str, Any]) -> dict[str, Any]:
    if not state:
        return {"v": STATE_VERSION, "turns": 0, "history": []}
    history = state.get("history")
    if (set(state) != {"v", "turns", "history"} or state["v"] != STATE_VERSION
            or type(state["turns"]) is not int or state["turns"] < 0
            or not isinstance(history, list) or len(history) > MAX_HISTORY
            or not all(_valid_entry(entry) for entry in history)):
        raise AgentInputError("El estado de la conversación no es válido. Inicia una conversación nueva.",
                              code="invalid_state")
    return {"v": STATE_VERSION, "turns": state["turns"], "history": list(history)}


def _remember(current: dict[str, Any], resultado: core.RespuestaAgente) -> dict[str, Any]:
    outcome = "no_respondible" if resultado.no_respondible else ("ok" if resultado.ok else "error")
    entry = {"pregunta": resultado.pregunta[:MAX_QUESTION_IN_STATE], "resultado": outcome,
             "sql": resultado.sql_validado[:MAX_SQL_IN_STATE] if resultado.sql_validado else None,
             "ejecutado": resultado.ejecutado}
    return {**current, "turns": current["turns"] + 1, "history": [*current["history"], entry][-MAX_HISTORY:]}


# --- Presentación -----------------------------------------------------------------------

def _escape(text: Any, limit: int | None = None) -> str:
    """Texto de BD/modelo como literal Markdown (sin enlaces, imágenes ni tablas rotas)."""
    if text is None:
        return "NULL"
    value = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    value = value.replace("\r", " ").replace("\n", " ")[:limit]
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


def _code(sql: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", sql)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}sql\n{sql}\n{fence}"


def _table(columns: list[str], rows: list[list[Any]]) -> str:
    header = "| " + " | ".join(_escape(name) for name in columns) + " |"
    divider = "|" + "---|" * len(columns)
    body = ["| " + " | ".join(_escape(value, 200) for value in row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def format_reply(data: dict[str, Any], *, simulated: list[str], file_attached: bool) -> str:
    parts = []
    if simulated:
        parts.append("> **Simulación**: " + "; ".join(simulated) + ". No refleja el modelo ni datos reales.")
    if data["no_respondible"]:
        parts.append(f"**NO_RESPONDIBLE** · {_escape(data['error'])}")
    elif not data["ok"]:
        parts.append(f"**No se obtuvo una consulta válida.** {_escape(data['error'])}")
        if data["sql_validado"]:
            parts += ["SQL validado (falló la ejecución):", _code(data["sql_validado"])]
        elif data["sql_generado"]:
            parts += ["SQL generado (rechazado; no se ejecutó):", _code(data["sql_generado"])]
    elif data["ejecutado"]:
        rows = data["filas"]
        parts += [f"**SQL ejecutado** · {len(rows)} fila(s).", _code(data["sql_ejecutado"])]
        if rows:
            parts.append(_table(data["columnas"], rows[:MAX_ROWS_IN_REPLY]))
        else:
            parts.append("La consulta no devolvió filas.")
        if len(rows) > MAX_ROWS_IN_REPLY:
            parts.append(f"Se muestran {MAX_ROWS_IN_REPLY} de {len(rows)} filas."
                         + (f" Descarga `{RESULT_FILE}` para verlas todas." if file_attached else ""))
    else:
        parts += ["**SQL validado** (no ejecutado: `TEXT2SQL_EXECUTE=false`).", _code(data["sql_validado"])]
    return "\n\n".join(parts)


def _history_reply(current: dict[str, Any]) -> str:
    if not current["history"]:
        return "Todavía no hay preguntas en esta conversación."
    lines = [f"Turnos: {current['turns']} (se conservan los últimos {MAX_HISTORY})."]
    for index, entry in enumerate(current["history"], 1):
        executed = " · ejecutado" if entry["ejecutado"] else ""
        lines.append(f"{index}. `{entry['resultado']}`{executed} · {_escape(entry['pregunta'])}")
    return "\n".join(lines)


# --- Punto de entrada -------------------------------------------------------------------

def answer(message: str, state: dict[str, Any], context: AgentContext) -> AgentResult:
    current = load_state(state)
    command = message.split(maxsplit=1)[0].lower()
    if command == "/ayuda":
        return AgentResult(reply=HELP, state=current)
    if command == "/historial":
        return AgentResult(reply=_history_reply(current), state=current)
    if command.startswith("/"):
        raise AgentInputError("Comando desconocido. Escribe /ayuda.", code="unknown_command")

    try:
        config = core.Configuracion.desde_entorno()  # SQL_*; también carga .env en local
    except ValueError as exc:
        raise AgentConfigError(str(exc), code="sql_config") from None
    settings = Text2SqlSettings.from_env()

    simulated = []
    if context.model is not None:
        generate = model_generator(context.model)
    else:
        generate = SimulatedGenerator(context.request_id)
        simulated.append("generador determinista con los ejemplos del núcleo (`STARTER_RESPONDER=echo`)")
    if settings.sql_backend == "simulated":
        execute: Executor = SimulatedDatabase(message, context.request_id)
        if settings.execute:
            simulated.append("base de datos simulada (`TEXT2SQL_SQL_BACKEND=simulated`)")
    else:
        execute = core.ejecutar_sql

    resultado = responder(message, generate=generate, execute=execute, run_sql=settings.execute, config=config)
    data = resultado.to_dict()
    files = []
    if context.files is not None:
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        files.append(OutputFile(RESULT_FILE, content, "application/json"))
    logger.info("Text-to-SQL turn: request_id=%s ok=%s no_respondible=%s ejecutado=%s filas=%d",
                context.request_id, resultado.ok, resultado.no_respondible, resultado.ejecutado,
                len(resultado.filas))
    reply = format_reply(data, simulated=simulated, file_attached=bool(files))
    return AgentResult(reply=reply, state=_remember(current, resultado), files=files)
