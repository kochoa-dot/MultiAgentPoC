"""Scripts PowerShell: codificación compatible con Windows PowerShell 5.1 y separación de etapas."""
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
STAGES = ["validate-local.ps1", "preview-infra.ps1", "deploy-infra.ps1", "configure-auth.ps1", "publish-agent.ps1",
          "grant-agent-permissions.ps1", "publish-web.ps1", "check-availability.ps1", "set-sql-secret.ps1",
          "build-agent-image.ps1"]


def test_every_stage_has_its_own_script():
    for name in STAGES:
        assert (SCRIPTS / name).exists(), name


def test_scripts_are_utf8_with_bom():
    # Sin BOM, Windows PowerShell 5.1 interpreta el archivo como ANSI y corrompe los acentos.
    for path in SCRIPTS.glob("*.ps1"):
        assert path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name


def test_scripts_avoid_powershell7_only_syntax():
    forbidden = [r"\?\?", r"\s\?\s[^:]+\s:\s", r"&&", r"\|\|", r"-AsHashtable", r"-SkipHttpErrorCheck"]
    for path in SCRIPTS.glob("*.ps1"):
        code = "\n".join(line for line in path.read_text(encoding="utf-8-sig").splitlines()
                         if not line.lstrip().startswith("#"))
        for pattern in forbidden:
            assert not re.search(pattern, code), f"{path.name}: {pattern}"


def test_cloud_scripts_check_the_azure_session():
    for name in STAGES[1:]:
        text = (SCRIPTS / name).read_text(encoding="utf-8-sig")
        assert "Assert-AzureContext" in text, name


def test_preview_does_not_apply_changes():
    text = (SCRIPTS / "preview-infra.ps1").read_text(encoding="utf-8-sig")
    assert "what-if" in text and "deployment sub create" not in text
