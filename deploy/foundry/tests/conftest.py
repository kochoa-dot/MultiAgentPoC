"""Rutas de importación y entorno aislado para las pruebas.

- Raíz (paquete ``web``) y ``agent`` (``starter_agent``, ``text2sql_core``, ``text2sql_adapter``, ``main``).
- ``PYTHONPATH`` incluye ``agent`` para que los subprocesos de las pruebas originales importen el núcleo.
- ``PYTHON_DOTENV_DISABLED``: un ``.env`` del desarrollador no altera las pruebas (ni lleva secretos a ellas).
- Cada prueba parte de una vista ficticia y sin variables ``TEXT2SQL_*`` heredadas de la terminal.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
for path in (ROOT, AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(AGENT), os.environ.get("PYTHONPATH")]))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

TEST_SCHEMA = "lecturas"
TEST_VIEW = "vw_chatbot_mom"


@pytest.fixture(autouse=True)
def text2sql_env(monkeypatch):
    monkeypatch.setenv("SQL_ENGINE", "sqlserver")
    monkeypatch.setenv("SQL_VIEW_SCHEMA", TEST_SCHEMA)
    monkeypatch.setenv("SQL_VIEW_NAME", TEST_VIEW)
    for key in ("SQL_MAX_ROWS", "SQL_DEFAULT_ROWS", "SQL_QUERY_TIMEOUT", "SQL_CONNECTION_STRING", "SQL_SERVER",
                "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD", "TEXT2SQL_EXECUTE", "TEXT2SQL_SQL_BACKEND"):
        monkeypatch.delenv(key, raising=False)


def pytest_configure(config):
    """En una copia limpia no existe .build/; pytest necesita la carpeta padre de --basetemp."""
    basetemp = config.option.basetemp
    if basetemp:
        Path(basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)
