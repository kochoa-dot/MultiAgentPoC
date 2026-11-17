"""Configuración de la interfaz. Los modos son explícitos; no hay modo por defecto.

``WEB_AGENT_MODE``:

- ``inprocess``: importa ``agent/starter_agent`` y lo ejecuta en el mismo proceso.
- ``local-http``: llama a ``python agent/main.py`` en ``http://localhost:8088``.
- ``foundry``: invoca el Hosted Agent publicado, con token Entra.

En App Service (``WEBSITE_SITE_NAME`` presente) solo se admite ``foundry`` con
``WEB_AUTH=easyauth``. Fuera de App Service, ``foundry`` admite además
``WEB_AUTH=developer`` (usa ``az login`` y aplica la misma lista de usuarios).
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

MODES = ("inprocess", "local-http", "foundry")
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
WEB_ROOT = Path(__file__).resolve().parent


class ConfigError(ValueError):
    """Configuración incompleta o insegura. El mensaje se muestra al operador."""


@dataclass(frozen=True)
class WebSettings:
    mode: str
    on_app_service: bool
    auth: str = "none"  # none | easyauth | developer
    title: str = "Agente · pruebas"
    project_endpoint: str = ""
    agent_name: str = ""
    api_version: str = "v1"
    expected_tenant_id: str = ""
    allowed_user_ids: frozenset[str] = field(default_factory=frozenset)
    managed_identity_client_id: str | None = None
    local_agent_url: str = "http://localhost:8088"
    local_agent_dir: Path = WEB_ROOT.parent / "agent"
    files_mode: str = "none"  # none | local | blob
    local_files_dir: Path = Path(".local-files")
    storage_account_url: str = ""
    output_container: str = ""
    timeout_seconds: float = 120.0
    max_retries: int = 2

    @property
    def is_local(self) -> bool:
        return self.mode in {"inprocess", "local-http"}


def _guid(value: str, name: str) -> str:
    value = value.strip()
    if not GUID.match(value) or value == ZERO_GUID:
        raise ConfigError(f"{name} debe ser un GUID real.")
    return value.lower()


def load_settings(env: Mapping[str, str] | None = None) -> WebSettings:
    env = os.environ if env is None else env
    get = lambda key, default="": (env.get(key) or default).strip()  # noqa: E731
    on_app_service = bool(get("WEBSITE_SITE_NAME"))
    mode = get("WEB_AGENT_MODE").lower()
    if mode not in MODES:
        raise ConfigError("Defina WEB_AGENT_MODE como 'inprocess', 'local-http' o 'foundry'.")
    if on_app_service and mode != "foundry":
        raise ConfigError("En App Service solo se admite WEB_AGENT_MODE=foundry.")

    files_mode = get("WEB_FILES_MODE", "none").lower()
    if files_mode not in {"none", "local", "blob"}:
        raise ConfigError("WEB_FILES_MODE debe ser 'none', 'local' o 'blob'.")
    try:
        timeout = float(get("AGENT_TIMEOUT_SECONDS", "120"))
        retries = int(get("AGENT_MAX_RETRIES", "2"))
    except ValueError:
        raise ConfigError("AGENT_TIMEOUT_SECONDS y AGENT_MAX_RETRIES deben ser numéricos.") from None
    if not 5 <= timeout <= 600 or not 0 <= retries <= 5:
        raise ConfigError("AGENT_TIMEOUT_SECONDS debe estar entre 5 y 600; AGENT_MAX_RETRIES entre 0 y 5.")

    common = {
        "mode": mode, "on_app_service": on_app_service, "title": get("WEB_TITLE", "Agente · pruebas"),
        "files_mode": files_mode, "local_files_dir": Path(get("LOCAL_FILES_DIR", ".local-files")),
        "storage_account_url": get("STORAGE_ACCOUNT_URL"), "output_container": get("OUTPUT_CONTAINER"),
        "timeout_seconds": timeout, "max_retries": retries,
        "managed_identity_client_id": get("AZURE_CLIENT_ID") or None,
    }
    if files_mode == "blob":
        if not common["storage_account_url"].startswith("https://") or not common["output_container"]:
            raise ConfigError("WEB_FILES_MODE=blob requiere STORAGE_ACCOUNT_URL (https) y OUTPUT_CONTAINER.")
    if files_mode == "local" and on_app_service:
        raise ConfigError("WEB_FILES_MODE=local no se admite en App Service.")

    if mode == "inprocess":
        local_dir = get("LOCAL_AGENT_DIR")
        return WebSettings(**common, local_agent_dir=Path(local_dir) if local_dir else WEB_ROOT.parent / "agent")
    if mode == "local-http":
        url = get("LOCAL_AGENT_URL", "http://localhost:8088").rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ConfigError("LOCAL_AGENT_URL debe ser http://localhost:<puerto> o http://127.0.0.1:<puerto>.")
        return WebSettings(**common, local_agent_url=url)

    auth = get("WEB_AUTH", "easyauth").lower()
    if auth not in {"easyauth", "developer"}:
        raise ConfigError("WEB_AUTH debe ser 'easyauth' o 'developer'.")
    if auth == "developer" and on_app_service:
        raise ConfigError("WEB_AUTH=developer no se admite en App Service.")
    endpoint = get("PROJECT_ENDPOINT").rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or "/api/projects/" not in parsed.path:
        raise ConfigError("PROJECT_ENDPOINT debe ser https://<cuenta>.services.ai.azure.com/api/projects/<proyecto>.")
    agent_name = get("HOSTED_AGENT_NAME")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", agent_name):
        raise ConfigError("HOSTED_AGENT_NAME no es válido.")
    allowed = frozenset(_guid(item, "ALLOWED_USER_OBJECT_IDS") for item in get("ALLOWED_USER_OBJECT_IDS").split(",")
                        if item.strip())
    if not allowed:
        raise ConfigError("ALLOWED_USER_OBJECT_IDS debe contener al menos un Object ID.")
    return WebSettings(**common, auth=auth, project_endpoint=endpoint, agent_name=agent_name,
                       api_version=get("AGENT_API_VERSION", "v1"),
                       expected_tenant_id=_guid(get("EXPECTED_TENANT_ID"), "EXPECTED_TENANT_ID"),
                       allowed_user_ids=allowed)
