"""Almacenes de archivos de entrada y salida.

- ``FILES_MODE=none``: sin archivos. ``AgentContext.files`` es ``None``.
- ``FILES_MODE=local``: carpeta local (pruebas sin Azure).
- ``FILES_MODE=blob``: Blob Storage privado con la identidad del agente.

Las salidas se escriben una sola vez con el identificador ``<request_id>/<nombre>``.
Si un reintento del mismo turno vuelve a escribir el archivo, se conserva el
primero y la operación se considera completada (idempotencia).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .contract import FILE_NAME_PATTERN, AgentConfigError, AgentInputError, is_valid_file_id


class FileStore(Protocol):
    def save_output(self, file_id: str, content: bytes, content_type: str) -> None: ...

    def read_input(self, name: str) -> bytes: ...


def _check_input_name(name: str) -> str:
    parts = name.split("/")
    if (not name or len(name) > 256 or "\\" in name or ".." in parts
            or not all(FILE_NAME_PATTERN.match(part) for part in parts)):
        raise AgentInputError("Nombre de archivo de entrada no válido.", code="invalid_file_name")
    return name


class LocalFileStore:
    """``<root>/inputs`` para entradas y ``<root>/outputs`` para salidas."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"

    def save_output(self, file_id: str, content: bytes, content_type: str) -> None:
        if not is_valid_file_id(file_id):
            raise AgentInputError("Identificador de archivo no válido.", code="invalid_file_name")
        target = self.outputs / file_id
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "xb") as handle:  # "x": nunca sobrescribe
                handle.write(content)
        except FileExistsError:
            return

    def read_input(self, name: str) -> bytes:
        target = (self.inputs / _check_input_name(name)).resolve()
        if self.inputs.resolve() not in target.parents:
            raise AgentInputError("Ruta fuera de la carpeta de entradas.", code="invalid_file_name")
        if not target.is_file():
            raise AgentInputError(f"No existe el archivo de entrada «{name}».", code="input_not_found")
        return target.read_bytes()


@lru_cache(maxsize=1)
def _default_credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


class BlobFileStore:
    """Contenedores privados. Requiere RBAC de datos, nunca claves compartidas."""

    def __init__(self, account_url: str, input_container: str, output_container: str, *, credential=None,
                 service_client=None):
        if not account_url.startswith("https://"):
            raise AgentConfigError("STORAGE_ACCOUNT_URL debe usar HTTPS.", code="storage_config")
        if service_client is None:
            from azure.storage.blob import BlobServiceClient

            service_client = BlobServiceClient(account_url, credential=credential or _default_credential())
        self._service = service_client
        self.input_container = input_container
        self.output_container = output_container

    def save_output(self, file_id: str, content: bytes, content_type: str) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        if not is_valid_file_id(file_id):
            raise AgentInputError("Identificador de archivo no válido.", code="invalid_file_name")
        blob = self._service.get_blob_client(self.output_container, file_id)
        try:
            blob.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type=content_type))
        except ResourceExistsError:
            return

    def read_input(self, name: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        if not self.input_container:
            raise AgentConfigError("No hay contenedor de entradas configurado.", code="storage_config")
        blob = self._service.get_blob_client(self.input_container, _check_input_name(name))
        try:
            return blob.download_blob().readall()
        except ResourceNotFoundError:
            raise AgentInputError(f"No existe el archivo de entrada «{name}».", code="input_not_found") from None
