"""Interfaz de pruebas Streamlit para el agente.

Ejecución local (sin Azure)::

    $env:WEB_AGENT_MODE = "inprocess"; $env:WEB_FILES_MODE = "local"
    python -m streamlit run web/streamlit_app.py

En App Service se ejecuta con ``WEB_AGENT_MODE=foundry`` detrás de Easy Auth.
La página no persiste el contenido de la conversación fuera de la sesión.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:  # permite «streamlit run web/streamlit_app.py» desde cualquier carpeta
    sys.path.insert(0, ROOT)

import streamlit as st  # noqa: E402

from web.auth import Principal, is_authorized, principal_from_easy_auth, principal_from_token  # noqa: E402
from web.client import (FOUNDRY_SCOPE, AgentCallError, AgentClient, credential_for,  # noqa: E402
                        new_request_id, transport_for)
from web.config import ConfigError, WebSettings, load_settings  # noqa: E402
from web.files import DownloadError, downloader_for  # noqa: E402

MAX_CACHED_DOWNLOADS = 5


def _settings() -> WebSettings:
    try:
        return load_settings()
    except ConfigError as exc:
        st.error(f"La aplicación no está configurada: {exc}")
        st.stop()
        raise  # pragma: no cover


@st.cache_resource(show_spinner=False)
def _cached_client(key: str, _settings: WebSettings) -> AgentClient:
    return AgentClient(transport_for(_settings), timeout_seconds=_settings.timeout_seconds,
                       max_retries=_settings.max_retries)


@st.cache_resource(show_spinner=False)
def _cached_downloader(key: str, _settings: WebSettings):
    return downloader_for(_settings, lambda: credential_for(_settings))


def _client(settings: WebSettings) -> AgentClient:
    return _cached_client(repr(settings), settings)


def _downloader(settings: WebSettings):
    return _cached_downloader(repr(settings), settings)


def _principal(settings: WebSettings) -> Principal | None:
    if settings.is_local:
        return Principal(object_id="local", tenant_id="local", display_name="modo local")
    if settings.auth == "easyauth":
        return principal_from_easy_auth(st.context.headers, settings.expected_tenant_id)
    try:  # developer: identidad de «az login»
        token = credential_for(settings).get_token(FOUNDRY_SCOPE).token
    except Exception:  # noqa: BLE001
        return None
    return principal_from_token(token, settings.expected_tenant_id)


def _reset_conversation() -> None:
    st.session_state.history = []
    st.session_state.agent_state = {}
    st.session_state.agent_session_id = None
    st.session_state.allowed_files = set()
    st.session_state.downloads = {}
    st.session_state.pending = None
    st.session_state.last_error = None


def _run_turn(message: str, request_id: str) -> None:
    st.session_state.pending = {"message": message, "request_id": request_id}
    try:
        with st.spinner("El agente está respondiendo…"):
            result = _client(settings).send(message, st.session_state.agent_state,
                                            st.session_state.agent_session_id, request_id)
    except AgentCallError as exc:
        st.session_state.agent_session_id = exc.session_id or st.session_state.agent_session_id
        st.session_state.last_error = {"message": exc.message, "code": exc.code, "retryable": exc.retryable}
        if not exc.retryable:
            st.session_state.pending = None
        return
    st.session_state.pending = None
    st.session_state.last_error = None
    st.session_state.agent_state = result.state
    st.session_state.agent_session_id = result.session_id
    files = [{"id": item.id, "name": item.name, "content_type": item.content_type, "size": item.size}
             for item in result.files]
    st.session_state.allowed_files.update(item["id"] for item in files)
    st.session_state.history.append({"role": "user", "text": message})
    st.session_state.history.append({"role": "assistant", "text": result.reply, "files": files})


def _render_file(item: dict, index: int) -> None:
    cached = st.session_state.downloads.get(item["id"])
    if cached is None:
        if st.button(f"Preparar descarga · {item['name']}", key=f"prep-{index}-{item['id']}"):
            downloader = _downloader(settings)
            try:
                if downloader is None:
                    raise DownloadError("La descarga de archivos no está habilitada en esta instalación.")
                content = downloader.read(item["id"], st.session_state.allowed_files)
            except DownloadError as exc:
                st.error(str(exc))
                return
            if len(st.session_state.downloads) >= MAX_CACHED_DOWNLOADS:
                st.session_state.downloads.pop(next(iter(st.session_state.downloads)))
            st.session_state.downloads[item["id"]] = content
            st.rerun()
        return
    st.download_button(f"Descargar {item['name']}", cached, file_name=item["name"], mime=item["content_type"],
                       key=f"dl-{index}-{item['id']}")


st.set_page_config(page_title="Agente · pruebas", page_icon="💬", layout="centered")
settings = _settings()
st.title(settings.title)

principal = _principal(settings)
if principal is None:
    st.error("No se pudo verificar tu identidad Entra. Vuelve a iniciar sesión.")
    st.stop()
if not settings.is_local and not is_authorized(principal, settings.allowed_user_ids):
    st.error("Tu cuenta no está autorizada para esta página de pruebas.")
    st.stop()

if st.session_state.get("user_id") != principal.object_id:
    st.session_state.clear()
    st.session_state.user_id = principal.object_id
if "history" not in st.session_state:
    _reset_conversation()

with st.sidebar:
    st.caption(f"Modo: **{settings.mode}**" + (" · sin autenticación (solo local)" if settings.is_local else ""))
    if not settings.is_local:
        st.caption(f"Usuario: {principal.display_name or principal.object_id}")
    session = st.session_state.agent_session_id
    st.caption(f"Sesión del agente: `{session[:18]}…`" if session and len(session) > 18
               else f"Sesión del agente: `{session or 'nueva'}`")
    if st.button("Nueva conversación", type="primary", width="stretch"):
        _reset_conversation()
        st.rerun()

for index, item in enumerate(st.session_state.history):
    with st.chat_message(item["role"]):
        st.markdown(item["text"])
        for file_item in item.get("files", []):
            _render_file(file_item, index)

pending = st.session_state.pending
error = st.session_state.last_error
if error:
    st.error(error["message"] + f"  \n`{error['code']}`")
if pending and error and error.get("retryable"):
    with st.chat_message("user"):
        st.markdown(pending["message"])
    retry, discard = st.columns(2)
    if retry.button("Reintentar", width="stretch"):
        _run_turn(pending["message"], pending["request_id"])
        st.rerun()
    if discard.button("Descartar mensaje", width="stretch"):
        st.session_state.pending = None
        st.session_state.last_error = None
        st.rerun()

message = st.chat_input("Escribe un mensaje", disabled=bool(pending))
if message:
    _run_turn(message, new_request_id())
    st.rerun()
