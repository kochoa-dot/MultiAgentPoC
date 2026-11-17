"""Descarga autorizada de archivos generados por el agente.

Condiciones para descargar, todas obligatorias:

1. La página ya verificó al usuario (Easy Auth + lista de Object IDs).
2. El identificador fue devuelto por el agente *en esta sesión de Streamlit*
   (``allowed_ids``); no se aceptan identificadores escritos por el usuario.
3. El identificador cumple ``<request_id>/<nombre>`` sin rutas relativas.

En Azure se lee el contenedor privado con la identidad administrada de la web,
que solo tiene ``Storage Blob Data Reader`` sobre ese contenedor. No se generan
URL SAS ni enlaces públicos.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

FILE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


class DownloadError(Exception):
    pass


def _check(file_id: str, allowed_ids: Collection[str]) -> None:
    if file_id not in allowed_ids:
        raise DownloadError("Este archivo no pertenece a la conversación actual.")
    if not FILE_ID.match(file_id) or ".." in file_id:
        raise DownloadError("Identificador de archivo no válido.")


class LocalDownloader:
    def __init__(self, root: str | Path):
        self.outputs = (Path(root) / "outputs").resolve()

    def read(self, file_id: str, allowed_ids: Collection[str]) -> bytes:
        _check(file_id, allowed_ids)
        target = (self.outputs / file_id).resolve()
        if self.outputs not in target.parents or not target.is_file():
            raise DownloadError("El archivo no existe.")
        if target.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise DownloadError("El archivo supera el tamaño permitido para descarga.")
        return target.read_bytes()


class BlobDownloader:
    def __init__(self, account_url: str, container: str, credential, *, service_client=None):
        if service_client is None:
            from azure.storage.blob import BlobServiceClient

            service_client = BlobServiceClient(account_url, credential=credential)
        self.service = service_client
        self.container = container

    def read(self, file_id: str, allowed_ids: Collection[str]) -> bytes:
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

        _check(file_id, allowed_ids)
        blob = self.service.get_blob_client(self.container, file_id)
        try:
            properties = blob.get_blob_properties()
            if properties.size > MAX_DOWNLOAD_BYTES:
                raise DownloadError("El archivo supera el tamaño permitido para descarga.")
            return blob.download_blob().readall()
        except ResourceNotFoundError:
            raise DownloadError("El archivo no existe.") from None
        except HttpResponseError as exc:
            if exc.status_code == 403:
                raise DownloadError("La aplicación no tiene permiso de lectura sobre el contenedor.") from None
            raise DownloadError("No se pudo leer el archivo.") from None


def downloader_for(settings, credential_factory):
    if settings.files_mode == "local":
        return LocalDownloader(settings.local_files_dir)
    if settings.files_mode == "blob":
        return BlobDownloader(settings.storage_account_url, settings.output_container, credential_factory())
    return None
