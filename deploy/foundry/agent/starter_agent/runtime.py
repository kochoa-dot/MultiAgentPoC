"""Construye las dependencias del agente a partir de variables de entorno.

Las variables ``FOUNDRY_*`` y ``AGENT_*`` están reservadas por la plataforma de
Hosted Agents; por eso esta base usa ``STARTER_*``, ``MODEL_*`` y ``FILES_*``.

- ``STARTER_RESPONDER``: ``echo`` (determinista, sin modelo) o ``model``.
- ``FILES_MODE``: ``none`` | ``local`` | ``blob``.
- ``LOCAL_FILES_DIR``: carpeta para ``FILES_MODE=local`` (por defecto ``.local-files``).
- ``STORAGE_ACCOUNT_URL``, ``INPUT_CONTAINER``, ``OUTPUT_CONTAINER``: para ``blob``.

No hay valores de reserva silenciosos: si se pide ``model`` o ``blob`` y falta
configuración, se lanza ``AgentConfigError``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .contract import AgentConfigError, ModelClient
from .files import BlobFileStore, FileStore, LocalFileStore
from .model import ModelSettings, OpenAIModelClient


@dataclass(frozen=True)
class Runtime:
    model: ModelClient | None
    files: FileStore | None
    responder: str
    files_mode: str


def build_runtime(env: Mapping[str, str] | None = None) -> Runtime:
    env = os.environ if env is None else env
    responder = env.get("STARTER_RESPONDER", "echo").strip().lower() or "echo"
    if responder not in {"echo", "model"}:
        raise AgentConfigError("STARTER_RESPONDER debe ser 'echo' o 'model'.", code="runtime_config")
    model = OpenAIModelClient(ModelSettings.from_env(env)) if responder == "model" else None

    files_mode = env.get("FILES_MODE", "none").strip().lower() or "none"
    files: FileStore | None
    if files_mode == "none":
        files = None
    elif files_mode == "local":
        files = LocalFileStore(env.get("LOCAL_FILES_DIR", "").strip() or ".local-files")
    elif files_mode == "blob":
        account_url = env.get("STORAGE_ACCOUNT_URL", "").strip()
        output_container = env.get("OUTPUT_CONTAINER", "").strip()
        if not account_url or not output_container:
            raise AgentConfigError("FILES_MODE=blob requiere STORAGE_ACCOUNT_URL y OUTPUT_CONTAINER.",
                                   code="storage_config")
        files = BlobFileStore(account_url, env.get("INPUT_CONTAINER", "").strip(), output_container)
    else:
        raise AgentConfigError("FILES_MODE debe ser 'none', 'local' o 'blob'.", code="runtime_config")
    return Runtime(model=model, files=files, responder=responder, files_mode=files_mode)
