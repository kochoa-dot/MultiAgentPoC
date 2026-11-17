"""Autenticación, autorización, configuración por modo y descarga privada."""
import base64
import json

import pytest

from web.auth import is_authorized, principal_from_easy_auth, principal_from_token
from web.config import ConfigError, load_settings
from web.files import BlobDownloader, DownloadError, LocalDownloader

TENANT = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
FOUNDRY_ENV = {"WEB_AGENT_MODE": "foundry", "PROJECT_ENDPOINT": "https://a.services.ai.azure.com/api/projects/p",
               "HOSTED_AGENT_NAME": "starter-agent", "EXPECTED_TENANT_ID": TENANT, "ALLOWED_USER_OBJECT_IDS": USER}


def easy_auth(tenant=TENANT, oid=USER, auth_typ="aad", mapped=False):
    tid_claim = "http://schemas.microsoft.com/identity/claims/tenantid" if mapped else "tid"
    oid_claim = "http://schemas.microsoft.com/identity/claims/objectidentifier" if mapped else "oid"
    payload = {"auth_typ": auth_typ, "claims": [{"typ": tid_claim, "val": tenant}, {"typ": oid_claim, "val": oid}]}
    return {"X-Ms-Client-Principal": base64.b64encode(json.dumps(payload).encode()).decode()}


def test_easy_auth_principal_requires_tenant_and_aad():
    assert principal_from_easy_auth(easy_auth(), TENANT).object_id == USER
    assert principal_from_easy_auth(easy_auth(mapped=True), TENANT).object_id == USER
    assert principal_from_easy_auth(easy_auth(tenant=OTHER), TENANT) is None
    assert principal_from_easy_auth(easy_auth(auth_typ="google"), TENANT) is None
    assert principal_from_easy_auth({}, TENANT) is None
    assert principal_from_easy_auth({"x-ms-client-principal": "%%%"}, TENANT) is None


def test_allowlist_is_enforced():
    settings = load_settings(FOUNDRY_ENV)
    assert is_authorized(principal_from_easy_auth(easy_auth(), TENANT), settings.allowed_user_ids)
    assert not is_authorized(principal_from_easy_auth(easy_auth(oid=OTHER), TENANT), settings.allowed_user_ids)
    assert not is_authorized(None, settings.allowed_user_ids)


def test_developer_token_claims():
    body = base64.urlsafe_b64encode(json.dumps({"tid": TENANT, "oid": USER}).encode()).decode().rstrip("=")
    assert principal_from_token(f"h.{body}.s", TENANT).object_id == USER
    assert principal_from_token("not-a-jwt", TENANT) is None


def test_mode_is_mandatory_and_explicit():
    with pytest.raises(ConfigError, match="WEB_AGENT_MODE"):
        load_settings({})
    assert load_settings({"WEB_AGENT_MODE": "inprocess"}).is_local


def test_app_service_refuses_local_modes_and_developer_auth():
    for mode in ("inprocess", "local-http"):
        with pytest.raises(ConfigError, match="App Service"):
            load_settings({"WEB_AGENT_MODE": mode, "WEBSITE_SITE_NAME": "app"})
    with pytest.raises(ConfigError):
        load_settings({**FOUNDRY_ENV, "WEBSITE_SITE_NAME": "app", "WEB_AUTH": "developer"})
    with pytest.raises(ConfigError):
        load_settings({**FOUNDRY_ENV, "WEBSITE_SITE_NAME": "app", "WEB_FILES_MODE": "local"})
    assert load_settings({**FOUNDRY_ENV, "WEBSITE_SITE_NAME": "app"}).auth == "easyauth"


@pytest.mark.parametrize("key, value", [
    ("PROJECT_ENDPOINT", "http://a.services.ai.azure.com/api/projects/p"),
    ("PROJECT_ENDPOINT", ""),
    ("HOSTED_AGENT_NAME", ""),
    ("EXPECTED_TENANT_ID", "00000000-0000-0000-0000-000000000000"),
    ("ALLOWED_USER_OBJECT_IDS", ""),
    ("ALLOWED_USER_OBJECT_IDS", "not-a-guid"),
])
def test_foundry_mode_requires_valid_configuration(key, value):
    with pytest.raises(ConfigError):
        load_settings({**FOUNDRY_ENV, key: value})


def test_local_http_only_targets_localhost():
    with pytest.raises(ConfigError):
        load_settings({"WEB_AGENT_MODE": "local-http", "LOCAL_AGENT_URL": "http://example.com:8088"})
    assert load_settings({"WEB_AGENT_MODE": "local-http"}).local_agent_url == "http://localhost:8088"


def test_blob_mode_requires_storage_settings():
    with pytest.raises(ConfigError):
        load_settings({**FOUNDRY_ENV, "WEB_FILES_MODE": "blob"})


def test_downloads_only_allow_ids_issued_in_this_session(tmp_path):
    target = tmp_path / "outputs" / "req-0000001"
    target.mkdir(parents=True)
    (target / "a.txt").write_bytes(b"ok")
    downloader = LocalDownloader(tmp_path)
    assert downloader.read("req-0000001/a.txt", {"req-0000001/a.txt"}) == b"ok"
    with pytest.raises(DownloadError, match="conversación"):
        downloader.read("req-0000001/a.txt", set())
    with pytest.raises(DownloadError):
        downloader.read("../outputs/a.txt", {"../outputs/a.txt"})


def test_blob_download_uses_private_container_and_maps_403():
    from azure.core.exceptions import HttpResponseError

    class Blob:
        def __init__(self, fail):
            self.fail = fail

        def get_blob_properties(self):
            if self.fail:
                error = HttpResponseError("forbidden")
                error.status_code = 403
                raise error
            return type("P", (), {"size": 2})()

        def download_blob(self):
            return type("D", (), {"readall": lambda self: b"ok"})()

    class Service:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        def get_blob_client(self, container, name):
            self.calls.append((container, name))
            return Blob(self.fail)

    service = Service()
    assert BlobDownloader("https://x", "outputs", None, service_client=service).read(
        "req-0000001/a.txt", {"req-0000001/a.txt"}) == b"ok"
    assert service.calls == [("outputs", "req-0000001/a.txt")]
    with pytest.raises(DownloadError, match="permiso"):
        BlobDownloader("https://x", "outputs", None, service_client=Service(True)).read(
            "req-0000001/a.txt", {"req-0000001/a.txt"})
