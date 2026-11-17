# Text-to-SQL en Microsoft Foundry

Agente Text-to-SQL sobre **una vista autorizada** de SQL Server/Azure SQL, preparado para publicarse como **un único Hosted Agent de Microsoft Foundry**, con una página **Streamlit** de pruebas (local o en App Service con Entra/Easy Auth).

```
Streamlit → (inprocess | HTTP local | Foundry invocations) → handle_message → puente → text2sql_core
                                                                                ├─ prompt + validación AST (siempre)
                                                                                └─ ejecución de solo lectura (opcional)
```

La carpeta es autónoma: no importa ni lee nada de las carpetas de origen. La lógica Text-to-SQL es una **copia literal** del agente original (`agent/text2sql_core.py`, comprobada por hash) y se conecta mediante un puente pequeño (`agent/text2sql_adapter/`). La infraestructura, la web y los adaptadores proceden de la base Foundry sin cambios de comportamiento.

| Documento | Contenido |
| --- | --- |
| [CONFIGURATION.md](CONFIGURATION.md) | Variables del agente (`SQL_*`, `TEXT2SQL_*`, `MODEL_*`), `agent.env`, la web y Bicep |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Componentes, recorrido de un turno, identidades, permisos y decisiones de la integración |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Fallos de SQL, del modelo, de autenticación y de publicación |
| [ADAPTATION.md](ADAPTATION.md) | Reglas del contrato `handle_message` (referencia de la base) |

## Estructura

```
agent/                         Unidad de publicación del Hosted Agent (ZIP; Foundry ejecuta python main.py)
  main.py                      Adaptador Foundry: protocolo invocations 2.0.0 (sin cambios)
  text2sql_core.py             Lógica Text-to-SQL original, copia literal (no editar aquí: ver «Sincronizar el núcleo»)
  text2sql_adapter/
    bridge.py                  Puente: estado, errores, archivos y presentación; reutiliza el núcleo
    settings.py                TEXT2SQL_EXECUTE y TEXT2SQL_SQL_BACKEND (separados del alojamiento)
    simulated.py               Generador y base de datos simulados (solo pruebas)
  starter_agent/
    handler.py                 handle_message(...) -> delega en text2sql_adapter.bridge.answer
    contract.py · service.py   Contrato starter.v1, validación y archivos (sin cambios)
    model.py · runtime.py      Configuración del modelo (MODEL_*) y dependencias (sin cambios)
  requirements.txt             + sqlglot, python-dotenv, pyodbc · requirements.lock.txt: versiones Linux
web/                           Streamlit de pruebas (sin cambios)
infra/                         Bicep del piloto (Foundry; modelo, ACR, App Service y Storage opcionales)
scripts/                       Validación, ejecución local (run-local, invoke-local, check_local_config) y publicación
docker/agent.Dockerfile        Imagen del agente con ODBC Driver 18 (la que se publica con agentHosting.mode=container)
tests/                         Pruebas de la base, del puente y tests/original/test_core.py (pruebas originales)
agent.env.example              Configuración del agente publicado (sin secretos; la contraseña SQL va a una conexión)
.env.example                   Configuración para ejecutar en local
```

## Comportamiento original → dónde vive ahora

| Comportamiento del agente original | Dónde vive ahora | Cambio |
| --- | --- | --- |
| Instrucciones, columnas autorizadas y ejemplos (`construir_prompt`, `ejemplos_sql`) | `agent/text2sql_core.py` | Ninguno |
| Validación AST: un SELECT, una vista con esquema explícito, columnas y funciones permitidas, sin JOIN/CTE/UNION/subconsultas/OFFSET, TOP máximo (`validar_sql`) | `agent/text2sql_core.py` | Ninguno |
| Ejecución: revalidación, metadatos reales y `fetchmany(max_filas)` (`ejecutar_sql`) | `agent/text2sql_core.py`, invocado por `bridge.py` | Ninguno |
| Configuración `SQL_*` sin `dbo` por defecto, solo SQL Server/Azure SQL (`Configuracion.desde_entorno`) | `agent/text2sql_core.py` | Si falta, el turno devuelve `sql_config` (500) en lugar de `ok=false` |
| Generar sin ejecutar por defecto (`ejecutar=False`) | `TEXT2SQL_EXECUTE=false` por defecto | Ninguno |
| `NO_RESPONDIBLE`, SQL rechazado, errores BD sin detalles | `bridge.responder` (mismas etapas y mensajes que `responder_pregunta`) | Paridad comprobada en `tests/test_text2sql_bridge.py` |
| Resultado `RespuestaAgente.to_dict()` | Archivo `resultado.json` de cada turno (si hay archivos habilitados) y respuesta en Markdown | `uso_tokens` queda vacío: el cliente del modelo de la base no devuelve el uso |
| Cliente Azure OpenAI con clave (`generar_sql`) | Agente: `starter_agent/model.py` con token Entra (`MODEL_*`), mismos mensajes. CLI: `generar_sql` sigue disponible | El agente no usa `AZURE_OPENAI_KEY`. Cuota/timeout del modelo → 503 reintentable |
| Timeout o caída de comunicación de la BD | `bridge.responder` | `pyodbc.OperationalError`/timeout → 503 reintentable (antes, mensaje genérico) |
| CLI `--metadatos`, `--exportar-prompt`, pregunta directa | `python agent/text2sql_core.py ...` (lee `.env`) | Ninguno |
| Pruebas originales (28) | `tests/original/test_core.py`, copia literal | Se ejecutan con `pytest` |
| Conversación | `state` versionado con los últimos 10 turnos (`/historial`) | Nuevo; **no** se envía al modelo: cada pregunta se traduce sola, como en el original |

## Inicio local (sin Azure, dependencias simuladas)

Requisitos: Python 3.13 (el mismo runtime que `azure.yaml`; probado con 3.13.1) y Windows PowerShell 5.1 o PowerShell 7. Todos los comandos se ejecutan desde `deploy\foundry`, en una consola nueva:

```powershell
cd "$HOME\Documents\Multi Agent\Agente ultima version\deploy\foundry"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass      # solo esta consola
py -3.13 -m venv .venv                                          # si .venv no existe (no lo copie de otro equipo)
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m pytest
./scripts/run-local.ps1 -Check                                  # valida la configuración, sin mostrar valores
./scripts/run-local.ps1
```

`.venv` contiene rutas absolutas del equipo donde se creó: si se copió de otro equipo (error `did not find executable at 'C:\Users\<otro>\…'`), bórrelo (`Remove-Item -Recurse -Force .venv`) y repita los pasos. Dependencias: `agent/requirements.txt` basta para ejecutar e invocar el agente; `web/requirements.txt` añade Streamlit; `requirements-dev.txt` instala ambas más `pytest`, `httpx` y `uv`.

**Local no es lo mismo que sin conexión.** Con `-Responder echo` y `-Sql simulated|none` (por defecto) no se usa red ni credenciales. Con `-Responder model` el proceso local llama a su recurso Azure OpenAI existente, y con `-Sql odbc`, a su base de datos: ambos siguen siendo servicios remotos.

> **Datos de prueba.** Los identificadores de los ejemplos de esta guía, de los scripts y de las pruebas (suministro `0099000001`, ruta `99000001`, prefijo `00990002` en pruebas multiturno) son sintéticos: conservan el formato real pero no corresponden a clientes. Contra la base real, use un identificador existente en la vista.

`run-local.ps1` abre `http://localhost:8501` con el **generador simulado** (devuelve el SQL de los ejemplos del núcleo con el identificador de la pregunta) y la **base de datos simulada** (filas sintéticas). El SQL pasa siempre por el validador real. Cada respuesta indica «Simulación». Pruebe:

1. `Quiero las coordenadas disponibles del suministro 0099000001` → SQL ejecutado + tabla + **Preparar descarga · resultado.json**.
2. `Datos del medidor del suministro 0099000001` y después `/historial` → dos turnos encadenados.
3. `Ha tenido reclamos el suministro 0099000001` → `NO_RESPONDIBLE`.
4. `hola [simular-sql-invalido]` → el validador rechaza un `DELETE`; no se ejecuta nada.
5. `Datos del medidor del suministro 0099000001 [simular-ocupado:3]` → tras los reintentos automáticos aparece **Reintentar**; al pulsarlo se reenvía el mismo turno y responde.
6. `... [simular-bd-ocupada]` (reintento automático transparente) y `... [simular-error-bd]` (mensaje genérico, sin detalles).
7. **Nueva conversación** → estado, sesión e historial vacíos.

Otras formas:

```powershell
./scripts/run-local.ps1 -Sql none                     # solo genera y valida (comportamiento por defecto del agente)
./scripts/run-local.ps1 -Mode local-http              # Streamlit + agente real del SDK en http://localhost:8088/invocations
./scripts/run-local.ps1 -Mode agent                   # solo el agente (sin Streamlit), en primer plano
cd agent; ..\.venv\Scripts\python -m starter_agent.local   # chat en terminal (generador simulado)
```

### Invocación HTTP reproducible

Con el agente arrancado (`./scripts/run-local.ps1 -Mode agent`), en **otra** consola:

```powershell
./scripts/invoke-local.ps1 -Message 'Quiero las coordenadas disponibles del suministro 0099000001' -StateFile .build/conversacion.json
./scripts/invoke-local.ps1 -Message '/historial' -StateFile .build/conversacion.json     # 2.º turno con el mismo estado
./scripts/invoke-local.ps1 -Message 'hola [simular-sql-invalido]' -Raw                  # JSON completo del contrato
```

`invoke-local.ps1` espera a `GET /readiness`, envía el mismo cuerpo que la web (`{contract: "starter.v1", request_id, message, state}`) a `POST /invocations`, guarda `state` y `x-agent-session-id` en `-StateFile` y termina con código 0 solo si la respuesta es 200 (si no, muestra `error.code`). Solo admite `http://localhost` o `http://127.0.0.1`.

## Validación local completa

```powershell
./scripts/validate-local.ps1
```

Ejecuta pytest, verifica que los paquetes del agente y la web son autónomos (imports resueltos, dependencias declaradas, sin referencias a carpetas de origen), compila Bicep y analiza la sintaxis de los scripts. Bicep se instala **dentro del proyecto** (carpeta `.azure-local`, ignorada por git; sin instalación global ni sesión de Azure):

```powershell
$env:AZURE_CONFIG_DIR = "$PWD\.azure-local"; az bicep install --version v0.48.1; Remove-Item Env:AZURE_CONFIG_DIR
```

`scripts/common.ps1` busca Bicep en `$env:BICEP_PATH`, en el `PATH`, en `.azure-local\bin` y en `~\.azure\bin`.

## Probar con el modelo y la base reales (paso separado, pendiente)

No forma parte de la validación automática (que usa simulaciones) y **no se ha ejecutado**. Usa su recurso Azure OpenAI y su base existentes desde el proceso local; no crea ni modifica recursos. Requiere autorización sobre ambos. Hágalo por etapas, en una consola nueva desde `deploy\foundry`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# 1. Configuración (solo estados, sin valores): MODEL_* y SQL_* en .env o en la terminal
./scripts/run-local.ps1 -Check -Mode agent -Responder model -Sql odbc

# 2. Identidad para el modelo (token Entra, sin claves). Su usuario necesita Cognitive Services OpenAI User en el recurso.
az login --tenant <tenantId>
az account get-access-token --resource https://cognitiveservices.azure.com --query expiresOn --output tsv

# 3. Base: metadatos reales de la vista (solo lectura; driver ODBC 17/18 local)
cd agent; ..\.venv\Scripts\python text2sql_core.py --metadatos; cd ..

# 4. Modelo real, sin ejecutar SQL (genera y valida)
./scripts/run-local.ps1 -Mode agent -Responder model -Sql none
#    en otra consola:
./scripts/invoke-local.ps1 -Message 'Datos del medidor del suministro 0099000001'

# 5. Modelo real y ejecución de solo lectura en la base
./scripts/run-local.ps1 -Mode agent -Responder model -Sql odbc
#    en otra consola:
./scripts/invoke-local.ps1 -Message 'Quiero las coordenadas disponibles del suministro 0099000001' -StateFile .build/real.json
./scripts/invoke-local.ps1 -Message 'Ha tenido reclamos el suministro 0099000001'     # NO_RESPONDIBLE esperado

# 6. Imagen publicable (Driver 18) contra la base real: solo SQL, generador simulado (sin modelo en el contenedor)
./scripts/build-agent-image.ps1 -Tag real
docker run --rm -p 127.0.0.1:8089:8088 --env-file .env -e "SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server" -e STARTER_RESPONDER=echo -e TEXT2SQL_EXECUTE=true text2sql-agent:real
#    en otra consola:
./scripts/invoke-local.ps1 -Url http://127.0.0.1:8089 -Message 'Datos del medidor del suministro 0099000001'
```

El paso 6 comprueba lo que la publicación cambia respecto a su equipo: el Driver 18 y Linux, con la misma IP y la misma cuenta SQL. `-e` prevalece sobre el driver 17 de `.env`, y el `.env` no entra en la imagen. Dentro del contenedor no hay credenciales Entra, así que el modelo real se prueba en los pasos 4 y 5 (local) y en Foundry (§ Publicación, comprobación 3).

Resultados esperados: HTTP 200 sin la marca «Simulación» (en el paso 6 solo se marca el generador simulado; el SQL se ejecuta en la base real); el SQL usa la vista de `.env`; `resultado.json` aparece en `.local-files/outputs` (pasos 4 y 5). Si falla: `model_forbidden` (rol o tenant), `model_not_found` (despliegue), `sql_unavailable` (red o firewall), «No fue posible ejecutar la consulta» (columnas, permisos o driver): ver [TROUBLESHOOTING.md](TROUBLESHOOTING.md#modo-local). La web también sirve para este paso: `./scripts/run-local.ps1 -Mode local-http -Responder model -Sql odbc`.

Configuración y permisos: [CONFIGURATION.md § Pruebas reales](CONFIGURATION.md#pruebas-reales-pendientes).

## Publicación en Azure (preparada, no ejecutada)

Todo va a un grupo de recursos nuevo definido en `infra/parameters/pilot.bicepparam`; ningún identificador se reutiliza de otro entorno. Decisiones del piloto, ya reflejadas en `pilot.example.bicepparam` y `agent.env`:

| Decisión | Configuración | Efecto |
| --- | --- | --- |
| Reutilizar su Azure OpenAI y despliegue | `model = { deploy: false }`; `MODEL_ENDPOINT`, `MODEL_DEPLOYMENT_NAME`, `MODEL_RESOURCE_ID` en `agent.env` | Bicep no crea modelo ni pide cuota; la identidad del agente recibe Cognitive Services OpenAI User en su recurso |
| Contenedor con ODBC | `agentHosting = { mode: 'container', registryName: ... }` | ACR Basic solo Entra; se publica **la misma imagen** de `docker/agent.Dockerfile` probada en local, fijada por digest |
| Autenticación SQL existente | `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD_CONNECTION` en `agent.env` | La contraseña vive en una conexión del proyecto (Key Vault administrado), no en archivos ni en azd. Motivo: [CONFIGURATION.md § Secretos](CONFIGURATION.md#secretos-y-autenticación-sql-en-foundry) |
| Driver | Local: el de `.env` (17); publicado: `SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server` | `publish-agent.ps1` rechaza otro driver en modo contenedor |
| Web separada | `web = { deploy: false, ... }` | Sin App Service ni registro Entra: el agente se consume por API |

**Requisitos** (comprobados en Microsoft Learn, referencia de `azure.yaml` actualizada el 29-10-2026): Azure CLI, Bicep 0.48.1 (`.azure-local`), **azd ≥ 1.27.1** con las extensiones **`azure.ai.agents` ≥ 1.0.0-beta.8** y **`azure.ai.projects` ≥ 1.0.0-beta.4** (`azd extension install azure.ai.agents`, `azd extension install azure.ai.projects`; el manifiesto las declara en `requiredVersions`), y Docker Desktop (imágenes linux/amd64). Operador: Owner (o Contributor + Role Based Access Control Administrator) en el grupo de recursos destino, porque Bicep y azd crean asignaciones de roles, y `roleAssignments/write` en el recurso Azure OpenAI existente.

**Datos pendientes** (sin ellos los scripts se detienen antes de tocar Azure):

| Dato | Dónde | Cómo obtenerlo |
| --- | --- | --- |
| `subscriptionId`, `tenantId`, `resourceGroupName`, `location` | `pilot.bicepparam` | Suscripción y región destino con Hosted Agents; el tenant debe ser el de su Azure OpenAI y su Azure SQL |
| `prefix`, `agentName`, `agentHosting.registryName`, `files.storageAccountName` | `pilot.bicepparam` | Nombres libres; `preview-infra.ps1` comprueba ACR y Storage |
| `publisherObjectId` | `pilot.bicepparam` | `az ad signed-in-user show --query id -o tsv` |
| `MODEL_RESOURCE_ID` | `agent.env` | En la suscripción del recurso: `az cognitiveservices account list --query "[?properties.endpoint=='<MODEL_ENDPOINT>/'].id" -o tsv` |
| Red de Azure SQL hacia el Hosted Agent | Azure SQL | Ver comprobación 4 más abajo |

### Agente (API): secuencia única

En una consola nueva, desde `deploy\foundry`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# 1. Parámetros (una vez). agent.env ya existe (generado de .env, sin secretos): complete MODEL_RESOURCE_ID.
Copy-Item infra/parameters/pilot.example.bicepparam infra/parameters/pilot.bicepparam   # complete [PENDIENTE]
# 2. Validación local, sin Azure: pruebas, paquetes, Bicep, scripts; imagen + prueba de humo
./scripts/validate-local.ps1
./scripts/build-agent-image.ps1
# 3. Sesiones y versiones
az login --tenant <tenantId>; az account set --subscription <subscriptionId>
azd auth login --tenant-id <tenantId>
azd version; azd extension list --installed      # >= 1.27.1; azure.ai.agents >= 1.0.0-beta.8; azure.ai.projects >= 1.0.0-beta.4
# 4. Infraestructura (Foundry, proyecto, ACR, Blob; sin modelo ni web)
./scripts/preview-infra.ps1                      # solo lectura + what-if
./scripts/deploy-infra.ps1
# 5. Contraseña SQL en la conexión del proyecto (antes de publicar la versión)
./scripts/set-sql-secret.ps1 -FromDotEnv         # o sin -FromDotEnv para escribirla oculta
# 6. Imagen al ACR + azd deploy + permisos de la identidad del agente
./scripts/publish-agent.ps1
# 7. Comprobaciones posteriores
./scripts/grant-agent-permissions.ps1 -CheckOnly -ModelResourceId <MODEL_RESOURCE_ID>
./scripts/set-sql-secret.ps1 -CheckOnly
./scripts/check-availability.ps1 -InvokeAgent -Message 'Datos del medidor del suministro 0099000001'
```

Comprobaciones posteriores (qué confirma cada una):

1. `publish-agent.ps1` termina con «Versión N activa» y asigna, a la identidad del agente, Cognitive Services OpenAI User en su Azure OpenAI, AcrPull en el registro y los roles de Blob. `grant-agent-permissions.ps1 -CheckOnly` lo repite sin cambiar nada; RBAC puede tardar unos minutos.
2. `set-sql-secret.ps1 -CheckOnly`: la conexión existe (la API nunca devuelve el secreto).
3. `check-availability.ps1 -InvokeAgent -Message ...`: HTTP 200 por la API con su identidad, sin la marca «Simulación». Si el SQL se ejecuta, la contraseña llegó y la red funciona.
4. Si responde `sql_unavailable` o «No fue posible ejecutar la consulta», revise la red de Azure SQL. Learn no documenta IP de salida fijas para Hosted Agents, así que hay que **habilitar «Allow Azure services and resources to access this server»** (cualquier IP de Azure) o configurar red privada (`agentSubnet` + Private Endpoint). Es una decisión pendiente: [ARCHITECTURE.md § Etapa 2](ARCHITECTURE.md#etapa-2-publicación-en-un-grupo-de-recursos-nuevo).
5. `model_forbidden` en la respuesta: falta el rol en el Azure OpenAI, o su red bloquea el acceso.

Para aislar fallos: `./scripts/publish-agent.ps1 -Responder echo` publica con el generador simulado (sin modelo) y conserva la ejecución SQL real.

### Consumir el agente por API

`POST {projectEndpoint}/agents/{agentName}/endpoint/protocols/invocations?api-version=v1` con un token Entra para `https://ai.azure.com/.default` y el cuerpo `starter.v1` (`{contract, request_id, message, state}`). Para continuar una conversación, reenvíe `state` y añada `&agent_session_id=<x-agent-session-id de la respuesta anterior>`. `check-availability.ps1` imprime la URL exacta. El llamante necesita **Foundry Agent Consumer** en el proyecto o en el agente; Bicep se lo concede a `publisherObjectId`, y a otros clientes se les asigna con `az role assignment create --role "Foundry Agent Consumer" --assignee-object-id <id> --assignee-principal-type <User|ServicePrincipal> --scope <projectResourceId>`.

```powershell
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$body = @{ contract = 'starter.v1'; request_id = [guid]::NewGuid().ToString('N'); message = 'Datos del medidor del suministro 0099000001'; state = @{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri '<projectEndpoint>/agents/<agentName>/endpoint/protocols/invocations?api-version=v1' `
  -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

### Web (aparte, opcional)

Después del agente, y solo si se necesita la página: `web.deploy: true`, `authClientId` y `allowedUserObjectIds` en `pilot.bicepparam`. Luego:

```powershell
./scripts/deploy-infra.ps1          # añade App Service + Easy Auth
./scripts/configure-auth.ps1
./scripts/publish-web.ps1
./scripts/check-availability.ps1
```

Con `web.deploy: false`, `configure-auth.ps1` y `publish-web.ps1` se detienen con un mensaje explícito.

### Otras rutas y cambios

- **Modelo nuevo en la cuenta Foundry**: `model` completo con `deploy: true`. Lo comprueban `preview-infra.ps1` (catálogo y cuota) y `grant-agent-permissions.ps1`.
- **Publicación por código** (`agentHosting.mode: 'code'`, `remote_build`): no incluye el driver ODBC, así que `publish-agent.ps1` rechaza `TEXT2SQL_EXECUTE=true` en ese modo.
- **Esquema de autorización**: `publish-agent.ps1` declara el endpoint con `-AuthScheme Entra` (alias de `AzureRbac` que usa azd); `-AuthScheme AzureRbac` sigue disponible.
- **Imagen ya subida**: `publish-agent.ps1 -Image <registro>.azurecr.io/text2sql-agent@sha256:<digest>`. `build-agent-image.ps1 -Push` guarda la última referencia en `.build/agent-image-<prefix>.txt`.

| Cambio | Comandos |
| --- | --- |
| Lógica Text-to-SQL, puente o dependencias del agente | `validate-local.ps1` → `build-agent-image.ps1` → `publish-agent.ps1` |
| Configuración SQL o modelo (`agent.env`) | `publish-agent.ps1` (publica una versión nueva) |
| Contraseña SQL | `set-sql-secret.ps1` → `publish-agent.ps1` (las sesiones nuevas leen el valor nuevo) |
| Interfaz | `validate-local.ps1` → `publish-web.ps1` → `check-availability.ps1` |
| Infraestructura | `preview-infra.ps1` → `deploy-infra.ps1` |
| Dependencias | editar `agent/requirements.txt` → `lock-dependencies.ps1` → `validate-local.ps1` |

## Sincronizar el núcleo

`agent/text2sql_core.py` y `tests/original/test_core.py` no se editan aquí. Si el agente original cambia, copie de nuevo ambos archivos, ejecute `pytest`, y actualice `CORE_SHA256`/`ORIGINAL_TESTS_SHA256` en `tests/test_core_copy.py` con el valor que muestra el fallo. Si el núcleo añade dependencias, añádalas a `agent/requirements.txt` y regenere el lock.

## Estado de verificación

| Comprobación | Estado |
| --- | --- |
| 28 pruebas originales del núcleo (copia literal) | **Ejecutadas, pasan** (también en la carpeta original, como línea base) |
| Puente: paridad con `responder_pregunta`, estado, errores, reintentos, archivos, escape | **Ejecutadas, pasan** — con modelo y BD dobles |
| Pruebas de la base (contrato, web, Streamlit AppTest, adaptador Foundry, paquetes, scripts) | **Ejecutadas, pasan** — las que dependían del demo se adaptaron a Text-to-SQL |
| `agent/main.py` real por HTTP y Streamlit real en `local-http` | **Ejecutado** con dependencias simuladas |
| Paquete del agente importable aislado y paquetes autónomos (`package.py verify`) | **Ejecutado** |
| Sintaxis de scripts en Windows PowerShell 5.1 y validación de `agent.env` | **Ejecutado** |
| `.venv` recreado con Python 3.13.1, `pip check` sin conflictos | **Ejecutado** (el anterior venía de otro equipo) |
| `run-local.ps1 -Check` (simulado y `model`+`odbc` con `.env`, sin valores) | **Ejecutado**, válido en ambos modos |
| `run-local.ps1 -Mode agent` + `invoke-local.ps1`: 200 (SQL simulado, 2 turnos, `/historial`, `NO_RESPONDIBLE`, SQL rechazado, error BD), 503 reintentable, 400 | **Ejecutado** con dependencias simuladas |
| Streamlit `inprocess`: `/_stcore/health` = `ok` | **Ejecutado** |
| Compilación Bicep v0.48.1 y `bicep lint`: ejemplo (modelo existente + contenedor + sin web) y variantes code/web/modelo nuevo | **Ejecutado**, sin errores ni avisos |
| `build-agent-image.ps1` (sin `-Push`): imagen linux/amd64, `pyodbc.drivers()` = ODBC Driver 18, instrumentación OpenAI importable, invocación simulada 200 y arranque sin errores | **Ejecutado** en local, simulado |
| `httpx`: fallo reproducido en la imagen anterior (`ModuleNotFoundError` al instrumentar `openai`); corregido; span generado con `openai` 3.49 contra un servidor falso | **Ejecutado** en la imagen |
| `package.py` modo contenedor: `image@sha256`, sin `codeConfiguration`, `SQL_PASSWORD` solo como `${{connections...}}` | **Ejecutado** |
| `publish-agent.ps1`: validación de `agent.env` (modelo, driver, contraseña, autenticación SQL, modo) hasta la sesión de Azure, con Azure CLI aislada | **Ejecutado**, sin llamadas a Azure |
| Calidad del SQL con el modelo real, metadatos, ejecución en la base real y contenedor con Driver 18 contra la base real | **Pendiente** (paso separado, § Probar con el modelo y la base reales) |
| `az acr login`/push, `azd deploy` en modo contenedor, conexión de secretos, RBAC e invocación por API en Foundry | **Pendiente**: no se creó ni publicó nada en Azure |
