"""Paquetes publicables autónomos y coherencia de manifiesto, documentación y scripts."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package  # noqa: E402

DOCS = ["README.md", "ARCHITECTURE.md", "CONFIGURATION.md", "TROUBLESHOOTING.md", "ADAPTATION.md"]
USER_CREATED = {"infra/parameters/pilot.bicepparam"}  # se crea copiando pilot.example.bicepparam


def test_agent_context_is_flat_and_self_contained():
    out = Path(package.ROOT / ".build" / "test-agent-publish")
    package.build_agent("starter-test", out, "AzureRbac")
    manifest = (out / "azure.yaml").read_text(encoding="utf-8")
    assert "name: starter-test" in manifest and "type: AzureRbac" in manifest
    assert "__HOSTED_AGENT_NAME__" not in manifest
    files = {p.relative_to(out / "agent").as_posix() for p in (out / "agent").rglob("*") if p.is_file()}
    assert {"main.py", "requirements.txt", "starter_agent/handler.py", "starter_agent/service.py"} <= files
    assert not any(name.startswith("tests/") or "__pycache__" in name for name in files)
    # El contexto se importa aislado (-I) sin rutas de la base: solo con su propio contenido.
    code = "import sys; sys.path.insert(0, '.'); import main; print(type(main.app).__name__)"
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=out / "agent", capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    assert "InvocationAgentServerHost" in result.stdout


def test_web_zip_has_posix_paths_and_no_agent_code(tmp_path):
    zip_path = package.build_web(tmp_path / "web.zip")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert "requirements.txt" in names and "web/streamlit_app.py" in names
    assert all("\\" not in name and not name.startswith("agent/") for name in names)


def test_manifest_does_not_declare_reserved_variables():
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    env_block = manifest.split("    env:", 1)[1].split("    codeConfiguration:", 1)[0]
    keys = re.findall(r"^\s{6}([A-Z0-9_]+):", env_block, re.MULTILINE)
    assert keys and not [key for key in keys if key.startswith(("FOUNDRY_", "AGENT_"))]


def test_manifest_requires_both_foundry_extensions():
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    block = manifest.split("requiredVersions:", 1)[1].split("services:", 1)[0]
    assert 'azure.ai.agents: ">=1.0.0-beta.8"' in block and 'azure.ai.projects: ">=1.0.0-beta.4"' in block
    publish = (ROOT / "scripts" / "publish-agent.ps1").read_text(encoding="utf-8-sig")
    assert "'azure.ai.agents', 'azure.ai.projects'" in publish
    assert "[string]$AuthScheme = 'Entra'" in publish  # alias de AzureRbac que usa azd


def test_dockerfile_is_outside_the_published_agent():
    out = Path(package.ROOT / ".build" / "test-agent-publish")
    package.build_agent("starter-test", out, "Entra")
    assert "type: Entra" in (out / "azure.yaml").read_text(encoding="utf-8")
    assert not [p for p in out.rglob("*") if "dockerfile" in p.name.lower()]
    dockerfile = (ROOT / "docker" / "agent.Dockerfile").read_text(encoding="utf-8")
    assert "msodbcsql18" in dockerfile and "requirements.lock.txt" in dockerfile and "USER agent" in dockerfile


def test_model_deployment_is_optional_and_project_has_foundry_user():
    resources = (ROOT / "infra" / "resources.bicep").read_text(encoding="utf-8")
    assert "= if (deployModel)" in resources and "model.?deploy ?? true" in resources
    # { deploy: false } basta: el despliegue no exige campos que no se usan.
    assert "model.?deploymentName" in resources and "model.deploymentName" not in resources
    assert "53ca6127-db72-4b80-b1b0-d745d6d5456d" in resources and "project.identity.principalId" in resources
    example = (ROOT / "infra" / "parameters" / "pilot.example.bicepparam").read_text(encoding="utf-8")
    assert re.search(r"param model = \{\s*deploy: false\s*\}", example)  # decisión: modelo existente
    assert "MODEL_RESOURCE_ID=" in (ROOT / "agent.env.example").read_text(encoding="utf-8")
    publish = (ROOT / "scripts" / "publish-agent.ps1").read_text(encoding="utf-8-sig")
    assert "-not $agentSettings['MODEL_RESOURCE_ID']" in publish  # obligatorio con model.deploy=false


def test_container_manifest_publishes_pinned_image_without_code_configuration():
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    image = "starter01acr.azurecr.io/text2sql-agent@sha256:" + "a" * 64
    rendered = package.render_manifest(manifest, "starter-test", "Entra", image)
    assert f"\n    image: {image}\n    docker:\n      imagePassthrough: true\n" in rendered
    assert "codeConfiguration" not in rendered.split("services:", 1)[1]
    assert "container:\n      resources:" in rendered
    code = package.render_manifest(manifest, "starter-test", "Entra")
    assert "\n    codeConfiguration:\n" in code and "\n    image:" not in code
    for bad in ("starter01acr.azurecr.io/text2sql-agent:latest", "docker.io/text2sql-agent@sha256:" + "a" * 64):
        try:
            package.render_manifest(manifest, "starter-test", "Entra", bad)
        except SystemExit:
            continue
        raise AssertionError(f"imagen aceptada: {bad}")


def test_sql_password_only_comes_from_a_project_connection():
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    assert '      SQL_PASSWORD: "$${{connections.__SQL_SECRET_CONNECTION__.credentials.SQL_PASSWORD}}"' in manifest
    without = package.render_manifest(manifest, "starter-test", "Entra")
    assert "      SQL_PASSWORD:" not in without
    rendered = package.render_manifest(manifest, "starter-test", "Entra", sql_secret_connection="sql-credentials",
                                       omit_env=("SQL_CONNECTION_STRING",))
    assert '      SQL_PASSWORD: "$${{connections.sql-credentials.credentials.SQL_PASSWORD}}"' in rendered
    assert "      SQL_CONNECTION_STRING:" not in rendered and "      SQL_SERVER:" in rendered
    try:
        package.render_manifest(manifest, "starter-test", "Entra", omit_env=("NO_EXISTE",))
    except SystemExit:
        pass
    else:
        raise AssertionError("--omit-env aceptó una variable inexistente")
    example = (ROOT / "agent.env.example").read_text(encoding="utf-8")
    assert not re.search(r"^SQL_PASSWORD=", example, re.MULTILINE) and "SQL_PASSWORD_CONNECTION=" in example
    publish = (ROOT / "scripts" / "publish-agent.ps1").read_text(encoding="utf-8-sig")
    assert "$key -eq 'SQL_PASSWORD'" in publish and "Get-ProjectConnection" in publish


def test_container_registry_is_entra_only_and_driver_is_aligned():
    resources = (ROOT / "infra" / "resources.bicep").read_text(encoding="utf-8")
    assert "Microsoft.ContainerRegistry/registries@2025-04-01' = if (containerHosting)" in resources
    assert "adminUserEnabled: false" in resources and "azureADAuthenticationAsArmPolicy: { status: 'enabled' }" in resources
    assert "roles.acrPull" in resources and "roles.acrPush" in resources
    common = (ROOT / "scripts" / "common.ps1").read_text(encoding="utf-8-sig")
    assert "$script:ContainerOdbcDriver = 'ODBC Driver 18 for SQL Server'" in common
    dockerfile = (ROOT / "docker" / "agent.Dockerfile").read_text(encoding="utf-8")
    assert "msodbcsql18" in dockerfile
    example = (ROOT / "agent.env.example").read_text(encoding="utf-8")
    assert "\nSQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server\n" in example.replace("\r\n", "\n")
    publish = (ROOT / "scripts" / "publish-agent.ps1").read_text(encoding="utf-8-sig")
    assert "$agentSettings['SQL_ODBC_DRIVER'] -ne $script:ContainerOdbcDriver" in publish
    assert "TEXT2SQL_EXECUTE=true necesita el driver ODBC" in publish  # code + ejecutar SQL se rechaza
    build = (ROOT / "scripts" / "build-agent-image.ps1").read_text(encoding="utf-8-sig")
    assert "--platform', 'linux/amd64'" in build and "ContainerOdbcDriver" in build


def test_web_is_optional_and_separate_from_the_agent():
    resources = (ROOT / "infra" / "resources.bicep").read_text(encoding="utf-8")
    for name in ("plan", "webIdentity", "webSite", "webAuth", "webAgentConsumer"):
        assert re.search(rf"resource {name} '[^']+' = if \(deployWeb\)", resources), name
    example = (ROOT / "infra" / "parameters" / "pilot.example.bicepparam").read_text(encoding="utf-8")
    assert re.search(r"param web = \{\s*deploy: false", example)
    common = (ROOT / "scripts" / "common.ps1").read_text(encoding="utf-8-sig")
    assert "if (Test-WebDeploy $p) {  # el registro Entra" in common
    for script in ("configure-auth.ps1", "publish-web.ps1"):
        assert "Assert-WebDeployed $p" in (ROOT / "scripts" / script).read_text(encoding="utf-8-sig"), script


def test_agent_declares_httpx_for_openai_telemetry():
    requirements = (ROOT / "agent" / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^httpx>=0\.28,<0\.29$", requirements, re.MULTILINE)
    lock = (ROOT / "agent" / "requirements.lock.txt").read_text(encoding="utf-8")
    assert re.search(r"^httpx==0\.28\.\d+$", lock, re.MULTILINE)


def test_examples_do_not_carry_real_identifiers():
    guid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    for name in ("infra/parameters/pilot.example.bicepparam", "agent.env.example", ".env.example"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert set(guid.findall(text)) <= {"00000000-0000-0000-0000-000000000000"}, name
        if name.endswith(".env.example"):
            # Los lectores de run-local.ps1 y publish-agent.ps1 no quitan comentarios al final de la línea.
            assert not re.search(r"^[A-Z0-9_]+=[^\n]*[ \t]#", text, re.MULTILINE), name


def test_docs_reference_existing_files():
    pattern = r"(?<![\w/.-])(?:\./)?((?:scripts|infra|agent|web|tests)/[\w./-]+\.(?:ps1|py|bicepparam|bicep|txt))\b"
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for match in sorted(set(re.findall(pattern, text))):
            assert match in USER_CREATED or (ROOT / match).exists(), f"{doc} cita un archivo inexistente: {match}"
    assert (ROOT / "infra/parameters/pilot.example.bicepparam").exists()


def _param_block(script_text: str) -> str:
    start = script_text.find("param(")
    if start < 0:
        return ""
    depth = 0
    for index in range(start + len("param"), len(script_text)):
        depth += {"(": 1, ")": -1}.get(script_text[index], 0)
        if depth == 0:
            return script_text[start:index + 1]
    return script_text[start:]


def test_documented_script_parameters_exist():
    usage = re.compile(r"(?<![\w/.-])(?:\./)?scripts/([\w-]+\.ps1)((?:[ \t]+-[A-Za-z]+(?:[ \t]+(?!-)[\w.:/'-]+)?)*)")
    checked = 0
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for script, args in usage.findall(text):
            block = _param_block((ROOT / "scripts" / script).read_text(encoding="utf-8-sig"))
            for flag in re.findall(r"(?:^|\s)-([A-Za-z]+)", args):
                checked += 1
                assert re.search(rf"\${flag}\b", block, re.IGNORECASE), f"{doc}: {script} no acepta -{flag}"
    assert checked >= 5


def test_no_source_project_dependencies():
    pattern = re.compile(r"contract_assistant|contract-assistant|cdsdatapilot|rg-contratos|python-docx|"
                         r"microsoft-agents|azure-functions", re.IGNORECASE)
    for folder in ("agent", "web", "infra", "scripts"):
        for path in (ROOT / folder).rglob("*"):
            if path.name == "package.py":  # define el propio patrón de verificación
                continue
            if path.is_file() and path.suffix in {".py", ".txt", ".bicep", ".bicepparam", ".ps1", ".yaml"}:
                assert not pattern.search(path.read_text(encoding="utf-8")), path
