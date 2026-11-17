"""Empaqueta y verifica los artefactos publicables. Solo usa la biblioteca estándar.

    python scripts/package.py agent --agent-name <nombre> [--auth-scheme Entra] [--out .build/agent-publish]
        [--image <acr>.azurecr.io/<repo>@sha256:<digest>] [--omit-env CLAVE ...] [--sql-secret-connection <nombre>]
    python scripts/package.py web [--out .build/web.zip]
    python scripts/package.py verify

``agent``: crea un contexto azd autónomo (``azure.yaml`` renderizado + ``agent/``
plano con ``main.py``, ``starter_agent/`` y ``requirements.txt``). Es el diseño
documentado para ``remote_build``: Foundry instala ``requirements.txt`` y ejecuta
``python main.py`` desde la raíz del ZIP. Con ``--image`` el manifiesto publica esa imagen
(modo contenedor): se elimina ``codeConfiguration`` porque Learn indica no combinarlo con una
imagen, y la referencia debe ir fijada por digest. ``--omit-env`` quita del manifiesto variables
que no se usan (Windows PowerShell 5.1 no puede pasar valores vacíos a ``azd env set``).
``--sql-secret-connection`` sustituye el nombre de la conexión CustomKeys del proyecto en el
marcador ``${{connections.<nombre>.credentials.SQL_PASSWORD}}``; sin ella se quita SQL_PASSWORD.

``web``: crea un ZIP para la compilación Oryx de App Service con ``web/`` y
``requirements.txt`` en la raíz. Las entradas usan ``/`` (algunas versiones de
PowerShell/.NET escriben ``\\`` y Linux no las interpreta como carpetas).

``verify``: comprueba que cada import de los paquetes se resuelve con módulos
incluidos, la biblioteca estándar o dependencias declaradas, y que no hay
referencias al proyecto de origen.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"
WEB_DIR = ROOT / "web"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
# Imagen en un ACR, fijada por digest (despliegue reproducible: la misma imagen probada en local).
IMAGE = re.compile(r"^[a-z0-9]{5,50}\.azurecr\.io/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$")
CONNECTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,32}$")
CODE_CONFIGURATION = re.compile(r"^    codeConfiguration:\n(?:      .*\n)+", re.MULTILINE)
SQL_SECRET_MARKER = "__SQL_SECRET_CONNECTION__"
EXCLUDED_PARTS = {"__pycache__", ".local-files", ".pytest_cache"}
# Módulo importado -> distribución que debe aparecer en requirements.txt. Los imports cuyo
# nombre coincide con una distribución declarada (p. ej. «langgraph») no necesitan entrada.
IMPORT_TO_DISTRIBUTION = {
    "azure.ai.agentserver.invocations": "azure-ai-agentserver-invocations",
    "azure.identity": "azure-identity",
    "azure.storage.blob": "azure-storage-blob",
    "azure.core": "azure-core",  # dependencia transitiva de azure-identity/azure-storage-blob
    "openai": "openai",
    "starlette": "starlette",  # transitiva de azure-ai-agentserver-core
    "streamlit": "streamlit",
    "requests": "requests",
    "dotenv": "python-dotenv",  # text2sql_core.Configuracion.desde_entorno
    "sqlglot": "sqlglot",
    "pyodbc": "pyodbc",
}
TRANSITIVE_OK = {"azure-core": {"azure-identity", "azure-storage-blob"},
                 "starlette": {"azure-ai-agentserver-invocations"}}
# La web importa el agente solo en WEB_AGENT_MODE=inprocess, que config.py rechaza en App Service.
LOCAL_ONLY_IMPORTS = {"web": {"starter_agent"}}
# Referencias a las carpetas de origen (plantilla y agente original) que romperían la autonomía.
FORBIDDEN = re.compile(r"contract_assistant|\bCDS\b|cdsdatapilot|contracts-pilot|foundry-starter|MultiAgentPoC|"
                       r"Multi Agent|\.\.[/\\]+files\b", re.IGNORECASE)


def _requirements_source(component: Path) -> Path:
    lock = component / "requirements.lock.txt"
    return lock if lock.exists() else component / "requirements.txt"


def _python_files(base: Path, *, include_tests: bool = False):
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(base)
        if EXCLUDED_PARTS.intersection(relative.parts):
            continue
        if not include_tests and (relative.parts[0] == "tests" or path.name.startswith("test_")):
            continue
        yield path, relative


def render_manifest(manifest: str, agent_name: str, auth_scheme: str, image: str | None = None,
                    omit_env: tuple[str, ...] = (), sql_secret_connection: str | None = None) -> str:
    """Devuelve azure.yaml con nombre, esquema, modo de alojamiento y variables ajustados."""
    manifest = manifest.replace("\r\n", "\n")
    rendered = manifest.replace("__HOSTED_AGENT_NAME__", agent_name).replace("__AUTH_SCHEME__", auth_scheme)
    env_keys = set(re.findall(r"^      ([A-Z0-9_]+):", rendered.split("    env:", 1)[1].split("    codeConfiguration:", 1)[0],
                              re.MULTILINE))
    if sql_secret_connection:
        if not CONNECTION.match(sql_secret_connection):
            raise SystemExit(f"Nombre de conexión no válido: {sql_secret_connection!r}")
        rendered = rendered.replace(SQL_SECRET_MARKER, sql_secret_connection)
    else:
        omit_env = tuple(omit_env) + ("SQL_PASSWORD",)
    for key in omit_env:
        if key not in env_keys:
            raise SystemExit(f"--omit-env {key}: la variable no está en el bloque env de azure.yaml")
        rendered = re.sub(rf"^      {key}:.*\n", "", rendered, flags=re.MULTILINE)
    if image:
        if not IMAGE.match(image):
            raise SystemExit("--image debe ser <registro>.azurecr.io/<repositorio>@sha256:<digest> (fijada por digest).")
        if len(CODE_CONFIGURATION.findall(rendered)) != 1:
            raise SystemExit("azure.yaml debe tener un único bloque codeConfiguration para sustituirlo por la imagen.")
        # docker.imagePassthrough: azd usa la imagen tal cual. Sin él, azd 1.37 intenta compilar «project»
        # (sin Dockerfile recurre a buildpacks) antes de que la extensión vea «image».
        rendered = CODE_CONFIGURATION.sub(f"    image: {image}\n    docker:\n      imagePassthrough: true\n", rendered)
    if "__" in re.sub(r"#.*", "", rendered).replace("__init__", ""):
        raise SystemExit("Quedan marcadores sin sustituir en azure.yaml.")
    return rendered


def build_agent(agent_name: str, out: Path, auth_scheme: str, image: str | None = None,
                omit_env: tuple[str, ...] = (), sql_secret_connection: str | None = None) -> Path:
    if not NAME.match(agent_name):
        raise SystemExit(f"Nombre de agente no válido: {agent_name!r}")
    if auth_scheme not in {"AzureRbac", "Entra"}:
        raise SystemExit("--auth-scheme debe ser AzureRbac o Entra")
    out = out.resolve()
    if ROOT not in out.parents:
        raise SystemExit("La carpeta de salida debe estar dentro de la base.")
    target = out / "agent"
    if target.exists():
        shutil.rmtree(target)  # se conserva out/.azure (ambiente azd)
    target.mkdir(parents=True)
    for path, relative in _python_files(AGENT_DIR):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    shutil.copy2(_requirements_source(AGENT_DIR), target / "requirements.txt")
    manifest = (ROOT / "azure.yaml").read_text(encoding="utf-8")
    rendered = render_manifest(manifest, agent_name, auth_scheme, image, omit_env, sql_secret_connection)
    (out / "azure.yaml").write_text(rendered, encoding="utf-8", newline="\n")
    _verify_tree(target, {"main.py", "requirements.txt", "starter_agent/__init__.py", "starter_agent/handler.py"})
    print(f"Contexto del agente listo: {out}")
    return out


def build_web(out: Path) -> Path:
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp)
        for path, relative in _python_files(WEB_DIR):
            destination = stage / "web" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        shutil.copy2(_requirements_source(WEB_DIR), stage / "requirements.txt")
        _verify_tree(stage, {"requirements.txt", "web/__init__.py", "web/streamlit_app.py", "web/client.py"},
                     frozenset(LOCAL_ONLY_IMPORTS["web"]))
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
    missing = {"requirements.txt", "web/streamlit_app.py", "web/client.py", "web/config.py"} - names
    if missing or any("\\" in name for name in names) or any(name.startswith("agent/") for name in names):
        raise SystemExit(f"ZIP web incorrecto: faltan {sorted(missing)} o contiene rutas no válidas.")
    print(f"ZIP web listo: {out} ({len(names)} archivos)")
    return out


def _declared(requirements: Path) -> set[str]:
    names = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


def _verify_tree(base: Path, required: set[str], local_only: frozenset[str] = frozenset()) -> None:
    files = {path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()}
    missing = required - files
    if missing:
        raise SystemExit(f"Paquete incompleto en {base}: faltan {sorted(missing)}")
    local = {path.split("/")[0].removesuffix(".py") for path in files if path.endswith(".py")}
    declared = _declared(base / "requirements.txt")
    problems = []
    for path in base.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN.search(text):
            problems.append(f"{path.relative_to(base)}: referencia al proyecto de origen")
        for node in ast.walk(ast.parse(text)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            for module in modules:
                top = module.split(".")[0]
                if top in local or top in local_only or top in sys.stdlib_module_names or top == "__future__":
                    continue
                distribution = next((dist for prefix, dist in IMPORT_TO_DISTRIBUTION.items()
                                     if module == prefix or module.startswith(prefix + ".")), None)
                if distribution is None and top.lower().replace("_", "-") in declared:
                    continue  # el nombre del import coincide con una distribución declarada
                if distribution is None:
                    problems.append(f"{path.relative_to(base)}: import «{module}» sin dependencia declarada. "
                                    "Añádala a requirements.txt o, si el nombre difiere (yaml -> pyyaml), "
                                    "a IMPORT_TO_DISTRIBUTION en scripts/package.py")
                elif distribution not in declared and not (TRANSITIVE_OK.get(distribution, set()) & declared):
                    problems.append(f"{path.relative_to(base)}: «{module}» requiere {distribution} en requirements")
    if problems:
        raise SystemExit("Verificación de paquete fallida:\n  " + "\n  ".join(problems))


def verify() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temp:
        build_agent("starter-verify", Path(temp) / "agent-publish", "AzureRbac")
        zip_path = build_web(Path(temp) / "web.zip")
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(Path(temp) / "web-unzipped")
        _verify_tree(Path(temp) / "web-unzipped", {"requirements.txt"}, frozenset(LOCAL_ONLY_IMPORTS["web"]))
    print("Paquetes verificados: módulos completos, dependencias declaradas y sin referencias al origen.")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    agent = sub.add_parser("agent")
    agent.add_argument("--agent-name", required=True)
    agent.add_argument("--auth-scheme", default="Entra")
    agent.add_argument("--image", default=None)
    agent.add_argument("--omit-env", nargs="*", default=[])
    agent.add_argument("--sql-secret-connection", default=None)
    agent.add_argument("--out", default=str(ROOT / ".build" / "agent-publish"))
    web = sub.add_parser("web")
    web.add_argument("--out", default=str(ROOT / ".build" / "web.zip"))
    sub.add_parser("verify")
    args = parser.parse_args(argv)
    if args.command == "agent":
        build_agent(args.agent_name, Path(args.out), args.auth_scheme, args.image, tuple(args.omit_env),
                    args.sql_secret_connection)
    elif args.command == "web":
        build_web(Path(args.out))
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
