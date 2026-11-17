"""Identidad del usuario. Falla cerrado.

En App Service, Easy Auth valida el inicio de sesión y añade la cabecera
``X-MS-CLIENT-PRINCIPAL``. App Service elimina esa cabecera si la envía el
cliente, por lo que solo es confiable detrás de Easy Auth (requisito que impone
``config.load_settings``). La aplicación vuelve a comprobar tenant y Object ID.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass

AAD_TYPES = {"aad", "azureactivedirectory"}
OID_CLAIMS = ("oid", "http://schemas.microsoft.com/identity/claims/objectidentifier")
TID_CLAIMS = ("tid", "http://schemas.microsoft.com/identity/claims/tenantid")
NAME_CLAIMS = ("name", "preferred_username")


@dataclass(frozen=True)
class Principal:
    object_id: str
    tenant_id: str
    display_name: str = ""


def _first(claims: dict[str, str], names: tuple[str, ...]) -> str:
    return next((claims[name] for name in names if claims.get(name)), "")


def principal_from_easy_auth(headers: Mapping[str, str], expected_tenant: str) -> Principal | None:
    raw = next((value for key, value in headers.items() if key.lower() == "x-ms-client-principal"), "")
    if not raw:
        return None
    try:
        payload = json.loads(base64.b64decode(raw, validate=True))
        if str(payload.get("auth_typ", "")).lower() not in AAD_TYPES:
            return None
        claims = {item["typ"]: item["val"] for item in payload["claims"]}
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
        return None
    return _principal(claims, expected_tenant)


def principal_from_token(token: str, expected_tenant: str) -> Principal | None:
    """Modo desarrollador: lee claims del token que emitió la propia credencial local.

    No valida la firma porque el token no procede del cliente HTTP sino de
    ``az login`` en la misma máquina. Nunca use esta función con tokens recibidos.
    """
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError):
        return None
    return _principal({key: str(value) for key, value in claims.items()}, expected_tenant)


def _principal(claims: dict[str, str], expected_tenant: str) -> Principal | None:
    oid, tid = _first(claims, OID_CLAIMS), _first(claims, TID_CLAIMS)
    if not oid or not tid or tid.lower() != expected_tenant.lower():
        return None
    return Principal(object_id=oid.lower(), tenant_id=tid.lower(), display_name=_first(claims, NAME_CLAIMS))


def is_authorized(principal: Principal | None, allowed_user_ids: frozenset[str]) -> bool:
    return bool(principal and principal.object_id.lower() in allowed_user_ids)
