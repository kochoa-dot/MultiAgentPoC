"""scripts/check_local_config.py: valida por modo sin mostrar valores ni conectarse."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_local_config as checker  # noqa: E402

SECRET = "ValorSecretoFicticio-123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("STARTER_RESPONDER", "MODEL_ENDPOINT", "MODEL_DEPLOYMENT_NAME", "MODEL_TOKEN_SCOPE",
                "MODEL_TIMEOUT_SECONDS", "MODEL_MAX_OUTPUT_TOKENS", "MODEL_TEMPERATURE", "FILES_MODE",
                "SQL_ODBC_DRIVER", "AZURE_OPENAI_KEY"):
        monkeypatch.delenv(key, raising=False)


def output(report):
    return "\n".join(report.lines)


def test_simulated_mode_is_valid_without_model_or_database(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("TEXT2SQL_SQL_BACKEND", "simulated")
    report = checker.check()
    assert report.errors == 0, output(report)
    assert "Sin conexión a base de datos" in output(report)


def test_model_mode_requires_endpoint_and_deployment(monkeypatch):
    monkeypatch.setenv("STARTER_RESPONDER", "model")
    report = checker.check()
    assert report.errors >= 2
    assert "ERR MODEL_ENDPOINT" in output(report) and "ERR MODEL_DEPLOYMENT_NAME" in output(report)


def test_model_mode_validates_format_without_tokens(monkeypatch):
    monkeypatch.setenv("STARTER_RESPONDER", "model")
    monkeypatch.setenv("MODEL_ENDPOINT", "http://inseguro.example")
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", "despliegue")
    report = checker.check()
    assert report.errors == 1 and "HTTPS" in output(report)


def test_missing_view_is_reported(monkeypatch):
    monkeypatch.delenv("SQL_VIEW_SCHEMA")
    report = checker.check()
    assert "ERR SQL_VIEW_SCHEMA" in output(report) and report.errors >= 2


def test_placeholder_is_not_accepted(monkeypatch):
    monkeypatch.setenv("SQL_VIEW_SCHEMA", "REEMPLAZAR_ESQUEMA")
    report = checker.check()
    assert "SQL_VIEW_SCHEMA            [obligatoria] marcador" in output(report)


def test_odbc_requires_connection_settings(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    report = checker.check()
    text = output(report)
    assert "Conexión ODBC" in text and "ERR SQL_SERVER" in text and "ERR SQL_PASSWORD" in text


def test_odbc_rejects_server_with_protocol_and_missing_driver(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    for key in ("SQL_DATABASE", "SQL_USER"):
        monkeypatch.setenv(key, "x" + key.lower())
    monkeypatch.setenv("SQL_PASSWORD", SECRET)
    monkeypatch.setenv("SQL_SERVER", "tcp:servidor.example,1433")
    monkeypatch.setenv("SQL_ODBC_DRIVER", "Driver Inexistente 99")
    report = checker.check()
    text = output(report)
    assert "solo el hostname" in text and "Driver Inexistente 99" in text and "no instalado" in text


def test_values_are_never_printed(monkeypatch, capsys):
    monkeypatch.setenv("STARTER_RESPONDER", "model")
    monkeypatch.setenv("MODEL_ENDPOINT", f"https://{SECRET}.openai.azure.com")
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", SECRET)
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", SECRET)  # provoca un error de validación
    monkeypatch.setenv("TEXT2SQL_EXECUTE", "true")
    monkeypatch.setenv("SQL_CONNECTION_STRING", f"Driver={{Driver Inexistente}};Server={SECRET};Pwd={SECRET}")
    monkeypatch.setenv("AZURE_OPENAI_KEY", SECRET)
    monkeypatch.setenv("SQL_VIEW_NAME", SECRET)  # identificador no válido: el núcleo podría citarlo
    report = checker.check()
    assert report.errors > 0
    assert SECRET not in output(report)


def test_redact_hides_any_environment_value():
    assert checker._redact("falló con abc-secreto", {"X": "abc-secreto"}) == "falló con ***"
