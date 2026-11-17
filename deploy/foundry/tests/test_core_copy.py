"""El núcleo Text-to-SQL es una copia literal del agente original y el paquete publicable es autónomo.

Los hashes se calculan con finales de línea normalizados (``\\r\\n`` -> ``\\n``) para no depender
de ``core.autocrlf``. Si el núcleo original cambia, vuelva a copiarlo (README § Sincronizar el
núcleo) y actualice ``CORE_SHA256``.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package  # noqa: E402

CORE_SHA256 = "30044fef5f2a5f2f0b0c7442ad7ae568406281a23ef0d028e6260edde7b1b732"
ORIGINAL_TESTS_SHA256 = "96a002381f850b1e524bfecbb29eb72cca596803af3ac1f1088f0e0a84d64373"
# Solo existe dentro del repositorio de origen; la carpeta funciona sin ella.
ORIGINAL_DIR = ROOT.parents[1] / "files"


def normalized_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_core_and_original_tests_are_unmodified_copies():
    assert normalized_sha256(ROOT / "agent" / "text2sql_core.py") == CORE_SHA256
    assert normalized_sha256(ROOT / "tests" / "original" / "test_core.py") == ORIGINAL_TESTS_SHA256


@pytest.mark.skipif(not (ORIGINAL_DIR / "text2sql_core.py").exists(), reason="agente original no disponible")
def test_copies_match_original_agent_when_available():
    assert normalized_sha256(ORIGINAL_DIR / "text2sql_core.py") == CORE_SHA256
    assert normalized_sha256(ORIGINAL_DIR / "pruebas" / "test_core.py") == ORIGINAL_TESTS_SHA256


def test_published_agent_contains_core_and_adapter_and_imports_in_isolation():
    out = package.ROOT / ".build" / "test-text2sql-publish"
    package.build_agent("text2sql-test", out, "AzureRbac")
    agent = out / "agent"
    files = {p.relative_to(agent).as_posix() for p in agent.rglob("*") if p.is_file()}
    assert {"text2sql_core.py", "text2sql_adapter/bridge.py", "text2sql_adapter/simulated.py",
            "text2sql_adapter/settings.py", "starter_agent/handler.py"} <= files
    assert not any(name.endswith((".env", ".log")) or name.startswith("tests/") for name in files)
    requirements = (agent / "requirements.txt").read_text(encoding="utf-8")
    assert all(dist in requirements for dist in ("sqlglot==30.30.0", "python-dotenv==", "pyodbc=="))
    # Sin variables SQL ni .env: importar el agente no debe exigir configuración (igual que el original).
    code = ("import sys; sys.path.insert(0, '.'); from starter_agent.handler import handle_message; "
            "import main; print('ok')")
    env = {key: value for key, value in __import__("os").environ.items()
           if not key.startswith(("SQL_", "TEXT2SQL_", "AZURE_OPENAI_", "PYTHONPATH"))}
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=agent, capture_output=True, text=True,
                            timeout=60, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
