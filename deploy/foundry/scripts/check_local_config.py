"""Comprueba la configuración local del agente sin mostrar valores ni conectarse a servicios.

Uso habitual (aplica los mismos modos que la ejecución)::

    ./scripts/run-local.ps1 -Check
    ./scripts/run-local.ps1 -Check -Responder model -Sql odbc

También directamente: ``python scripts/check_local_config.py``. Precedencia: variables de la
terminal > ``.env`` > valores por defecto (python-dotenv sin sobrescribir).

Usa los validadores reales del agente (``ModelSettings.from_env``, ``build_runtime``,
``Text2SqlSettings.from_env`` y ``Configuracion.desde_entorno``). No obtiene tokens, no abre
conexiones SQL y no imprime valores: solo el estado de cada variable (definida, vacía, marcador)
y, con ``odbc``, el nombre del driver ODBC (no es un dato sensible y ayuda a diagnosticar).
Código de salida 0 si la configuración permite ejecutar el modo pedido; 1 si falta algo.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

PLACEHOLDER = re.compile(r"REEMPLAZAR|<[^>]*>|^0{8}-0{4}-0{4}-0{4}-0{12}$", re.IGNORECASE)
SQL_SERVER_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")  # misma regla que text2sql_core._conectar
DRIVER_IN_STRING = re.compile(r"(?i)(?:^|;)\s*driver\s*=\s*\{?([^};]+)\}?")
CONNECTION_KEYS = ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD")
DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"


def state(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None:
        return "no definida"
    if not value.strip():
        return "vacía"
    if PLACEHOLDER.search(value.strip()):
        return "marcador"
    return "definida"


def _redact(message: str, env: Mapping[str, str]) -> str:
    """Quita del mensaje cualquier valor de variable (los validadores podrían citarlo)."""
    for value in sorted({v.strip() for v in env.values() if v and len(v.strip()) >= 3}, key=len, reverse=True):
        message = message.replace(value, "***")
    return message


class Report:
    def __init__(self, env: Mapping[str, str]):
        self.env = env
        self.lines: list[str] = []
        self.errors = 0

    def var(self, key: str, kind: str, required: bool, note: str = "") -> bool:
        current = state(self.env, key)
        ok = current == "definida" or not required
        if not ok:
            self.errors += 1
        mark = "OK " if current == "definida" else ("ERR" if not ok else " - ")
        self.lines.append(f"  {mark} {key:<26} [{kind}] {current}{'  · ' + note if note else ''}")
        return current == "definida"

    def check(self, label: str, func) -> None:
        try:
            func()
            self.lines.append(f"  OK  {label}")
        except Exception as exc:  # noqa: BLE001 - se informa cualquier fallo de validación
            self.errors += 1
            self.lines.append(f"  ERR {label}: {_redact(str(exc), self.env) or type(exc).__name__}")

    def note(self, text: str) -> None:
        self.lines.append(f"  ·   {text}")

    def section(self, title: str) -> None:
        self.lines.append(title)


def check(env: Mapping[str, str] | None = None) -> Report:
    """Valida ``os.environ`` (o ``env``) para el modo indicado por STARTER_RESPONDER y TEXT2SQL_*."""
    env = os.environ if env is None else env
    from starter_agent.model import ModelSettings
    from starter_agent.runtime import build_runtime
    from text2sql_adapter.settings import Text2SqlSettings

    report = Report(env)
    responder = (env.get("STARTER_RESPONDER", "") or "echo").strip().lower()

    report.section(f"Generador (STARTER_RESPONDER={responder})")
    report.var("STARTER_RESPONDER", "opcional", False, "echo por defecto")
    if responder == "model":
        report.var("MODEL_ENDPOINT", "obligatoria con model", True)
        report.var("MODEL_DEPLOYMENT_NAME", "obligatoria con model", True)
        for key in ("MODEL_TOKEN_SCOPE", "MODEL_TIMEOUT_SECONDS", "MODEL_MAX_OUTPUT_TOKENS", "MODEL_TEMPERATURE"):
            report.var(key, "opcional", False)
        report.check("MODEL_* válidas (formato; sin obtener token)", lambda: ModelSettings.from_env(env))
        report.note("Credencial: DefaultAzureCredential (az login). Se comprueba al invocar, no aquí.")
    else:
        report.note("Generador simulado: no usa MODEL_* ni credenciales.")
    files_env = {**env, "STARTER_RESPONDER": "echo"}  # valida archivos sin construir el cliente del modelo
    report.check(f"Archivos (FILES_MODE={env.get('FILES_MODE', '') or 'none'})", lambda: build_runtime(files_env))

    report.section("Vista autorizada (SQL_*)")
    report.var("SQL_VIEW_SCHEMA", "obligatoria", True)
    report.var("SQL_VIEW_NAME", "obligatoria", True)
    for key in ("SQL_ENGINE", "SQL_MAX_ROWS", "SQL_DEFAULT_ROWS", "SQL_QUERY_TIMEOUT"):
        report.var(key, "opcional", False)

    def view():
        import text2sql_core

        text2sql_core.Configuracion.desde_entorno()

    report.check("Configuracion.desde_entorno (motor, identificadores y límites)", view)

    report.section("Ejecución SQL (TEXT2SQL_*)")
    settings = None
    try:
        settings = Text2SqlSettings.from_env(env)
        report.lines.append(f"  OK  TEXT2SQL_EXECUTE={'true' if settings.execute else 'false'} · "
                            f"TEXT2SQL_SQL_BACKEND={settings.sql_backend}")
    except Exception as exc:  # noqa: BLE001
        report.errors += 1
        report.lines.append(f"  ERR {_redact(str(exc), env)}")
    if settings and settings.execute and settings.sql_backend == "odbc":
        report.section("Conexión ODBC (condicional: TEXT2SQL_EXECUTE=true con backend odbc)")
        _check_odbc(report, env)
    elif settings:
        report.note("Sin conexión a base de datos en este modo.")

    report.section("Solo CLI original (python agent/text2sql_core.py); el agente no las usa")
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_KEY"):
        report.var(key, "opcional", False)
    return report


def _check_odbc(report: Report, env: Mapping[str, str]) -> None:
    if report.var("SQL_CONNECTION_STRING", "condicional", False, "tiene prioridad sobre SQL_SERVER/..."):
        match = DRIVER_IN_STRING.search(env["SQL_CONNECTION_STRING"])
        driver = match.group(1).strip() if match else None
        if not driver:
            report.note("La cadena no declara Driver=; pyodbc usará el que indique la cadena (DSN).")
    else:
        present = [report.var(key, "condicional", True) for key in CONNECTION_KEYS]
        server = env.get("SQL_SERVER", "").strip()
        if present[0] and not SQL_SERVER_PATTERN.fullmatch(server):
            report.errors += 1
            report.lines.append("  ERR SQL_SERVER debe ser solo el hostname, sin protocolo ni puerto.")
        report.var("SQL_ODBC_DRIVER", "opcional", False, f"«{DEFAULT_DRIVER}» por defecto")
        driver = env.get("SQL_ODBC_DRIVER", "").strip() or DEFAULT_DRIVER
    if driver:
        try:
            import pyodbc

            installed = pyodbc.drivers()
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.lines.append(f"  ERR pyodbc no disponible: {type(exc).__name__}")
            return
        if driver in installed:
            report.lines.append(f"  OK  Driver ODBC «{driver}» instalado")
        else:
            report.errors += 1
            report.lines.append(f"  ERR Driver ODBC «{driver}» no instalado (instalados: {', '.join(installed) or 'ninguno'})")
    report.note("No se abre ninguna conexión. Prueba real: python agent/text2sql_core.py --metadatos")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)  # misma precedencia que run-local.ps1 y text2sql_core
    report = check()
    print("\n".join(report.lines))
    if report.errors:
        print(f"\nConfiguración incompleta: {report.errors} problema(s). No se muestran valores.")
        return 1
    print("\nConfiguración válida para este modo (sin comprobar credenciales ni conectividad).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
