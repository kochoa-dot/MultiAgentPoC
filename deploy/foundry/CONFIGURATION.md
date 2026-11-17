# Configuración

Ningún archivo de esta base contiene secretos. Los identificadores van en `infra/parameters/pilot.bicepparam` (ignorado por git); los valores derivados (URLs, nombres, identidades) salen del despliegue y los scripts los leen de `az deployment sub show --name foundry-starter-<prefix>`.

## Parámetros de infraestructura (`*.bicepparam`)

| Parámetro | Obligatorio | Ejemplo | Descripción |
| --- | --- | --- | --- |
| `subscriptionId` | Sí | GUID | Suscripción destino. Los scripts fallan si `az account show` no coincide. |
| `tenantId` | Sí | GUID | Tenant de Entra. Se usa en Easy Auth, en la credencial federada y en `EXPECTED_TENANT_ID`. |
| `resourceGroupName` | Sí | `rg-starter-pilot` | Se crea si no existe. |
| `location` | Sí | `eastus2` | Región de todos los recursos. Debe ofrecer el modelo y Hosted Agents. |
| `prefix` | Sí | `starter01` | 3–12 minúsculas/números. Nombres: `<prefix>-foundry` (subdominio global), `<prefix>-project`, `<prefix>-web` (`azurewebsites.net` global), `<prefix>-plan`, `<prefix>-web-id`. |
| `agentName` | No | `<prefix>-agent` | Nombre del Hosted Agent. Reutilizarlo crea versiones nuevas del mismo agente. |
| `allowedUserObjectIds` | Si `web.deploy` | `['GUID']` | Object IDs (Entra ID > Users > *Object ID*) autorizados en la página. Piloto: uno. Con `web.deploy=false` se ignora (puede quedar en cero). |
| `publisherObjectId` | Recomendado | GUID | Quien publica. Recibe Foundry Project Manager y Foundry Agent Consumer en el proyecto, AcrPush en el registro (modo contenedor) y, con archivos, Blob Data Contributor en `inputs`. Vacío: no se asigna. |
| `publisherPrincipalType` | No | `User` | `User`, `ServicePrincipal` (pipeline) o `Group`. |
| `authClientId` | Si `web.deploy` | GUID | **Application (client) ID** del registro Entra (no su Object ID ni el de la Enterprise Application). Con `web.deploy=false` se ignora. |
| `model.deploy` | No | `false` en el piloto | `false` (decisión del piloto: su Azure OpenAI existente): Bicep no crea el despliegue ni `preview-infra.ps1` comprueba cuota; basta `model = { deploy: false }`. `agent.env` debe tener `MODEL_ENDPOINT`, `MODEL_DEPLOYMENT_NAME` y `MODEL_RESOURCE_ID`. Las salidas `modelEndpoint`/`modelDeploymentName` quedan vacías. `true` (valor por defecto de `main.bicep`) crea el despliegue con los campos siguientes. |
| `model.format` | Si `deploy` | `OpenAI` | Proveedor del modelo. |
| `model.name` / `model.version` | Si `deploy` | `gpt-5-mini` / `2025-08-07` | `preview-infra.ps1` comprueba que existen en la región con el SKU indicado. |
| `model.deploymentName` | Si `deploy` | `chat` | Nombre del despliegue; llega al agente como `MODEL_DEPLOYMENT_NAME`. |
| `model.sku` | Si `deploy` | `GlobalStandard` | Tipo de despliegue. |
| `model.capacity` | Si `deploy` | `10` | Miles de tokens por minuto. `preview-infra.ps1` compara con la cuota libre. |
| `agentHosting.mode` | No | `container` en el piloto | `container`: crea un ACR y publica la imagen de `docker/agent.Dockerfile` (ODBC Driver 18), necesaria para `TEXT2SQL_EXECUTE=true`. `code` (valor por defecto de `main.bicep`): `remote_build`, sin driver ODBC. |
| `agentHosting.registryName` | Si `container` | `starter01acr` | Único global, 5–50 letras y números. `preview-infra.ps1` comprueba la disponibilidad. Registro sin usuario administrador y con `azureADAuthenticationAsArmPolicy` habilitado (requisito de los Hosted Agents). |
| `agentHosting.registrySku` | No | `Basic` | SKU del registro. Learn no exige uno concreto; con red privada haría falta Premium. |
| `web.deploy` | No | `false` en el piloto | `false`: no se crean App Service, su identidad ni Easy Auth; el agente se consume por API. `true` (valor por defecto de `main.bicep`) exige `authClientId` y `allowedUserObjectIds`. |
| `web.sku` | No | `B1` | Plan Linux. B1 mantiene `alwaysOn`; F1 no lo admite. |
| `web.pythonVersion` | No | `3.13` | `linuxFxVersion` = `PYTHON|<versión>`. |
| `web.title` | No | `Text-to-SQL · pruebas` | Título de la página (`WEB_TITLE`). |
| `web.exposeHealthProbe` | No | `true` | Excluye `/_stcore/health` de Easy Auth para comprobar el arranque sin iniciar sesión. Solo devuelve `ok`. |
| `files.enabled` | No | `true` | Crea Storage privado y habilita archivos (`FILES_MODE=blob`, `WEB_FILES_MODE=blob`). |
| `files.storageAccountName` | Si `enabled` | `starter01files` | Único global, 3–24 minúsculas/números. |
| `files.inputContainer` / `files.outputContainer` | No | `inputs` / `outputs` | Contenedores con `publicAccess: None`. |
| `files.grantPublisherInputWrite` | No | `true` | Permite al publicador subir entradas. |
| `tags` | No | objeto | Etiquetas de todos los recursos. |

### Salidas del despliegue

`resourceGroupName`, `foundryAccountName`, `projectName`, `projectResourceId`, `projectEndpoint`, `modelEndpoint`, `modelDeploymentName`, `agentName`, `webEnabled`, `webAppName`, `webUrl`, `webRedirectUri`, `webIdentityName`, `webIdentityPrincipalId`, `webIdentityClientId`, `filesEnabled`, `storageAccountName`, `storageAccountUrl`, `inputContainer`, `outputContainer`, `agentHostingMode`, `containerRegistryName`, `containerRegistryLoginServer`, `containerRegistryResourceId`, `declaredSubscriptionId`, `declaredTenantId`. Las de la web, el modelo y el registro quedan vacías cuando no se despliegan.

```powershell
az deployment sub show --name foundry-starter-<prefix> --query properties.outputs
```

## Variables del agente

Las fija `publish-agent.ps1` a través de `azure.yaml` (`env`). Las variables `FOUNDRY_*` y `AGENT_*` están **reservadas**: la plataforma inyecta `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME`, `FOUNDRY_AGENT_VERSION`, `FOUNDRY_AGENT_SESSION_ID`, etc. No las declare.

| Variable | Valores | Por defecto | Descripción |
| --- | --- | --- | --- |
| `STARTER_RESPONDER` | `echo`, `model` | `echo` | `model` exige `MODEL_ENDPOINT` y `MODEL_DEPLOYMENT_NAME`; si faltan, cada turno devuelve `model_config`. `publish-agent.ps1` usa `model` salvo `-Responder echo`. |
| `MODEL_ENDPOINT` | URL | — | `https://<cuenta>.openai.azure.com` (salida `modelEndpoint`). Se añade `/openai/v1`. |
| `MODEL_DEPLOYMENT_NAME` | texto | — | Salida `modelDeploymentName`. |
| `MODEL_TOKEN_SCOPE` | URL | `https://cognitiveservices.azure.com/.default` | Audiencia del token. Learn también documenta `https://ai.azure.com/.default` para `/openai/v1`. |
| `MODEL_TIMEOUT_SECONDS` | número | `45` | Timeout por llamada. |
| `MODEL_MAX_OUTPUT_TOKENS` | entero | `2000` | `max_completion_tokens` (en modelos de razonamiento incluye tokens de razonamiento). |
| `MODEL_TEMPERATURE` | número | sin enviar | Algunos modelos de razonamiento no admiten `temperature`. |
| `FILES_MODE` | `none`, `local`, `blob` | `none` | Almacén de archivos. |
| `LOCAL_FILES_DIR` | ruta | `.local-files` | Para `local`: `inputs/` y `outputs/`. |
| `STORAGE_ACCOUNT_URL` | URL | — | Para `blob`: `https://<cuenta>.blob.core.windows.net/`. |
| `INPUT_CONTAINER` / `OUTPUT_CONTAINER` | texto | — | Para `blob`. `OUTPUT_CONTAINER` obligatorio. |
| `PORT` | entero | `8088` | Solo en local (`python agent/main.py`). |
| `PYTHONNOUSERSITE` | `1` | `1` en Foundry | Evita paquetes del usuario en el contenedor. |

Con `FILES_MODE=none`, `publish-agent.ps1` asigna `unused` a las variables de Storage porque Windows PowerShell 5.1 descarta argumentos vacíos; el agente no las lee en ese modo.

Con Text-to-SQL, `STARTER_RESPONDER=echo` significa **generador simulado** (SQL de los ejemplos del núcleo, marcado «Simulación» en cada respuesta); `model` usa el modelo real con `MODEL_*` y el mismo prompt que `text2sql_core.generar_sql`.

## Variables Text-to-SQL

Separadas del alojamiento: describen la vista, los límites y si se ejecuta SQL, no dónde corre el agente. Las `SQL_*` las lee `text2sql_core.Configuracion.desde_entorno` exactamente como en el agente original (en local también desde `.env`); las `TEXT2SQL_*`, `agent/text2sql_adapter/settings.py`. En Foundry llegan desde `agent.env` vía `publish-agent.ps1` y `azure.yaml`.

| Variable | Valores | Por defecto | Descripción |
| --- | --- | --- | --- |
| `SQL_ENGINE` | `sqlserver`, `azuresql` | `sqlserver` | Otro motor se rechaza (`sql_config`). |
| `SQL_VIEW_SCHEMA` | texto | — (obligatoria) | No se asume `dbo`. Sin ella cada turno devuelve `sql_config` (500). |
| `SQL_VIEW_NAME` | texto | — (obligatoria) | Nombre real de la vista (deducido: `vw_chatbot_mom`, por confirmar). |
| `SQL_MAX_ROWS` | 1–1000 | `200` | TOP máximo real. |
| `SQL_DEFAULT_ROWS` | 1–`SQL_MAX_ROWS` | `50` | TOP cuando el SQL no lo indica. |
| `SQL_QUERY_TIMEOUT` | 1–120 | `30` | Timeout de conexión y consulta (s). |
| `TEXT2SQL_EXECUTE` | `true`, `false` | `false` | `false` = genera y valida sin conectarse (igual que `ejecutar=False`). |
| `TEXT2SQL_SQL_BACKEND` | `odbc`, `simulated` | `odbc` | `simulated` solo para pruebas locales; no se publica en `azure.yaml`. |
| `SQL_CONNECTION_STRING` | cadena ODBC | — | Alternativa a las cuatro variables siguientes; tiene prioridad si existe. En Foundry, sin contraseña (`publish-agent.ps1` rechaza `Pwd=`/`Password=`); `ActiveDirectoryMsi` no es compatible con el Hosted Agent (§ Secretos). |
| `SQL_ODBC_DRIVER` | texto | `ODBC Driver 18 for SQL Server` | Solo sin cadena completa. **Local**: el instalado en su equipo (su `.env` usa el 17). **Publicado**: `ODBC Driver 18 for SQL Server`, el único de la imagen; `publish-agent.ps1` rechaza otro en modo contenedor, también dentro de `SQL_CONNECTION_STRING`. |
| `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER` | texto | — | Autenticación SQL. Local: `.env`. Foundry: `agent.env`. `SQL_SERVER` es solo el hostname. |
| `SQL_PASSWORD` | texto | — | Local: `.env`. Foundry: **nunca** en `agent.env`; `set-sql-secret.ps1` la guarda en la conexión `SQL_PASSWORD_CONNECTION` del proyecto, y la plataforma la inyecta al iniciar la sesión. |
| `AZURE_OPENAI_*` | texto | — | **Solo la CLI original** (`python agent/text2sql_core.py`). El agente usa `MODEL_*` con token Entra. |

### `agent.env` (publicación)

Copia de `agent.env.example`, ignorada por git. Su `agent.env` ya existe: se generó desde `.env` con los valores no secretos (vista, límites, `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER` y `MODEL_*`), con `TEXT2SQL_EXECUTE=true`, `SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server` y `SQL_PASSWORD_CONNECTION=sql-credentials`. Falta **`MODEL_RESOURCE_ID`**; `.env` no se modificó.

`publish-agent.ps1 [-AgentSettingsFile <ruta>]` acepta solo estas claves: `SQL_ENGINE`, `SQL_VIEW_SCHEMA`, `SQL_VIEW_NAME`, `SQL_MAX_ROWS`, `SQL_DEFAULT_ROWS`, `SQL_QUERY_TIMEOUT`, `SQL_ODBC_DRIVER`, `TEXT2SQL_EXECUTE`, `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD_CONNECTION` (o, en su lugar, `SQL_CONNECTION_STRING`) y `MODEL_ENDPOINT`, `MODEL_DEPLOYMENT_NAME` y `MODEL_RESOURCE_ID`. Reglas:

- **Modelo**: con `model.deploy=false`, las tres `MODEL_*` son obligatorias. `MODEL_RESOURCE_ID` (`/subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<recurso>`) **no** llega al contenedor: `grant-agent-permissions.ps1 -ModelResourceId` asigna con él Cognitive Services OpenAI User a la identidad del agente (el operador necesita `roleAssignments/write` en ese recurso).
- **SQL**: `TEXT2SQL_EXECUTE=true` exige `agentHosting.mode=container` y una de dos opciones: las cuatro de autenticación SQL juntas, o `SQL_CONNECTION_STRING` sin contraseña (nunca las dos). Si existe `SQL_PASSWORD_CONNECTION`, `publish-agent.ps1` comprueba que la conexión exista antes de publicar.
- **Driver**: en modo contenedor, `SQL_ODBC_DRIVER` debe ser `ODBC Driver 18 for SQL Server`.
- **Rechazos**: claves desconocidas (incluidas `SQL_PASSWORD`, `FOUNDRY_*` y `AGENT_*`), comillas dobles y el marcador `REEMPLAZAR_ESQUEMA`.
- **Variables vacías**: en lugar de publicarse vacías, se quitan del manifiesto, porque Windows PowerShell 5.1 descarta los argumentos vacíos.

### Secretos y autenticación SQL en Foundry

La frase anterior «Foundry no admite contraseñas» era inexacta. Era una **restricción de esta implementación**: la versión anterior de `publish-agent.ps1` rechazaba contraseñas porque cada valor de `agent.env` acaba en el ambiente azd (`.build/agent-publish/.azure/<env>/.env`, en texto plano) y en la definición de la versión del agente. No es una **política de la plataforma**. Microsoft Learn (*Configure environment variables for a hosted agent*, 27-10-2026; *Deploy a hosted agent*, 10-11-2026) dice:

- No hay prohibición. La recomendación es no meter secretos en la imagen ni en `azure.yaml`, y guardarlos en una **conexión del proyecto** (tipo CustomKeys), referenciada como `${{connections.<nombre>.credentials.<campo>}}`.
- Foundry guarda el valor en un Key Vault que administra la propia plataforma y lo inyecta como variable de entorno normal al iniciar cada sesión.
- La API nunca devuelve el secreto: el GET de la conexión responde `credentials: null`.
- La conexión debe existir antes de publicar la versión; si no, la variable llega vacía.

Por eso se **conserva su autenticación SQL** (usuario y contraseña de Azure SQL, la misma que usa en local) y solo cambia dónde vive la contraseña:

| Dónde | Contraseña SQL |
| --- | --- |
| Local | `.env` (sin cambios) |
| `agent.env`, `azure.yaml`, ambiente azd, imagen, logs | Nunca. `publish-agent.ps1` rechaza `SQL_PASSWORD` y `Pwd=` |
| Foundry | Conexión CustomKeys `sql-credentials` del proyecto (campo `SQL_PASSWORD`), creada con `set-sql-secret.ps1` mediante `PUT` de ARM (`api-version=2025-04-01-preview`, el que documenta Learn). La contraseña se pide oculta o se lee de `.env` (`-FromDotEnv`), viaja en un archivo temporal que se borra y no se muestra |

Requisitos concretos:

- El operador necesita Foundry Project Manager en el proyecto (`connections/write`); Bicep se lo concede a `publisherObjectId`.
- El usuario SQL debe tener solo `SELECT` sobre la vista.
- La red de Azure SQL debe aceptar la salida del Hosted Agent (README, comprobación 4).
- Si cambia la contraseña, ejecute `set-sql-secret.ps1` y publique una versión nueva.

Sin verificar: Learn no indica si la identidad del agente necesita algún rol para que se resuelva el marcador. Lo confirma la invocación de prueba.

**Identidad administrada (sin contraseña): no se adopta ahora**, por incompatibilidad técnica y no por política:

- El modo `Authentication=ActiveDirectoryMsi` del driver ODBC obtiene el token del servicio de metadatos de la VM (IMDS, `169.254.169.254`).
- El Hosted Agent expone su identidad con `IDENTITY_ENDPOINT`/`IDENTITY_HEADER` (Learn, *Hosted agent token service*), igual que App Service.
- Para App Service, Learn (*mssql-django*, despliegue en App Service) documenta que, en un contenedor propio, ese modo «no llega a IMDS y la conexión se bloquea hasta el tiempo de espera».
- La alternativa sin contraseña sería pedir el token con `azure-identity` y pasarlo a `pyodbc` (`SQL_COPT_SS_ACCESS_TOKEN`), lo que cambia `text2sql_core._conectar`. Requisitos: ese cambio de código, `CREATE USER [<agentName>] FROM EXTERNAL PROVIDER` y `GRANT SELECT` en la base, y que la identidad del agente sea estable entre versiones. Queda para una etapa posterior.

### Dependencia `openai`

El agente original declara `openai<3`; esta carpeta usa `openai>=3.49,<4` (la de la base). El agente no llama a `generar_sql`, y `openai.AzureOpenAI` existe en 3.49, pero la CLI original con openai 3.x **no se ha probado contra un endpoint real**.

## Ejecución local

Precedencia: **variable de la terminal > `.env` > valor por defecto del código**. `scripts/run-local.ps1` carga `.env` sin sobrescribir la terminal (quita las comillas que rodean un valor, como python-dotenv; no admite comentarios al final de la línea) y después fija, según sus parámetros, `STARTER_RESPONDER` (`-Responder`), `TEXT2SQL_EXECUTE`/`TEXT2SQL_SQL_BACKEND` (`-Sql`), `FILES_MODE=local`, `LOCAL_FILES_DIR` y `WEB_*`. `text2sql_core` vuelve a leer `.env` (sin sobrescribir) al construir `Configuracion`, pero solo para `SQL_*`. Por eso `python agent/main.py` lanzado a mano no ve `MODEL_*` de `.env`: use `./scripts/run-local.ps1 -Mode agent`. Las pruebas ignoran `.env` (`PYTHON_DOTENV_DISABLED`).

`./scripts/run-local.ps1 -Check` aplica la misma precedencia y los mismos parámetros, valida con el código del agente y muestra solo el estado de cada variable (`definida`, `vacía`, `marcador`, `no definida`). No muestra valores, salvo el nombre del driver ODBC, ni obtiene tokens o abre conexiones.

| Variable | Tipo | Cuándo |
| --- | --- | --- |
| `SQL_VIEW_SCHEMA`, `SQL_VIEW_NAME` | Obligatoria | Siempre. Con `-Sql simulated`/`none`, si faltan, `run-local.ps1` usa una vista ficticia; con `-Sql odbc` falla |
| `SQL_ENGINE`, `SQL_MAX_ROWS`, `SQL_DEFAULT_ROWS`, `SQL_QUERY_TIMEOUT` | Opcional | Valores por defecto en la tabla anterior |
| `MODEL_ENDPOINT`, `MODEL_DEPLOYMENT_NAME` | Condicional | `-Responder model`. Requiere `az login` y Cognitive Services OpenAI User |
| `MODEL_TOKEN_SCOPE`, `MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_OUTPUT_TOKENS`, `MODEL_TEMPERATURE` | Opcional | `-Responder model` |
| `SQL_CONNECTION_STRING` **o** `SQL_SERVER` + `SQL_DATABASE` + `SQL_USER` + `SQL_PASSWORD` | Condicional | `-Sql odbc` y `text2sql_core.py --metadatos/--ejecutar`. La cadena tiene prioridad |
| `SQL_ODBC_DRIVER` | Opcional | Sin cadena completa. Por defecto `ODBC Driver 18 for SQL Server`; debe estar instalado (`Get-OdbcDriver`) |
| `WEB_TITLE` y demás `WEB_*` | Opcional | Solo la web |
| `AZURE_OPENAI_*` | Opcional | Solo la CLI original |
| `PORT` | Opcional | Puerto del agente; `run-local.ps1 -AgentPort` lo fija |

## Pruebas reales (pendientes)

Nada de lo siguiente se ha ejecutado. Configuración necesaria:

**Modelo**

| Qué | Dónde | Valor |
| --- | --- | --- |
| Endpoint y despliegue | Local: terminal o `.env`. Foundry: salidas de Bicep o `agent.env` | `MODEL_ENDPOINT=https://<cuenta>.openai.azure.com`, `MODEL_DEPLOYMENT_NAME=<despliegue>` |
| Permiso de inferencia | Recurso del modelo | **Cognitive Services OpenAI User** para su usuario (local) y para la identidad del agente (`grant-agent-permissions.ps1` lo hace en la cuenta Foundry y, con modelo externo, en `MODEL_RESOURCE_ID`; sin él, asígnelo a mano en ese recurso) |
| Opcionales | Terminal / `agent.env` no | `MODEL_TOKEN_SCOPE`, `MODEL_TIMEOUT_SECONDS`, `MODEL_MAX_OUTPUT_TOKENS`, `MODEL_TEMPERATURE` (no se envía si está vacío, como en el original) |

**Azure SQL**

| Qué | Valor |
| --- | --- |
| Motor, esquema y vista confirmados | `SQL_ENGINE`, `SQL_VIEW_SCHEMA`, `SQL_VIEW_NAME` (y los tipos reales con `python agent/text2sql_core.py --metadatos`) |
| Autenticación | La existente (usuario SQL). Local: `.env`. Foundry: `SQL_SERVER`/`SQL_DATABASE`/`SQL_USER` en `agent.env` y la contraseña en la conexión del proyecto (§ Secretos) |
| Permisos en la base | El usuario SQL solo con `SELECT ON [<esquema>].[<vista>]`, sin roles amplios. Si hay seguridad por filas, una identidad por usuario: fuera del alcance actual |
| Red | Local: su IP en el firewall (ya funciona). Foundry: «Allow Azure services and resources» o red privada (Learn no documenta IP de salida fijas) |
| Driver | Local: ODBC Driver 17 (`.env`). Contenedor: ODBC Driver 18, comprobado en la imagen. Paso 6 de README § Probar: la imagen contra la base real |
| Activación | `TEXT2SQL_EXECUTE=true` (ya en `agent.env`) solo con `agentHosting.mode=container` |

## Variables de la web

Bicep las define como *app settings*; en local se fijan en la terminal (`.env.example`).

| Variable | Valores | Obligatoria | Descripción |
| --- | --- | --- | --- |
| `WEB_AGENT_MODE` | `inprocess`, `local-http`, `foundry` | Sí, sin valor por defecto | En App Service (`WEBSITE_SITE_NAME` presente) solo `foundry`. |
| `WEB_AUTH` | `easyauth`, `developer` | No (`easyauth`) | `developer` usa `az login` y solo funciona fuera de App Service. |
| `PROJECT_ENDPOINT` | URL | `foundry` | `https://<cuenta>.services.ai.azure.com/api/projects/<proyecto>`. |
| `HOSTED_AGENT_NAME` | texto | `foundry` | Nombre del agente. |
| `AGENT_API_VERSION` | texto | No (`v1`) | `api-version` del endpoint. |
| `EXPECTED_TENANT_ID` | GUID | `foundry` | Se compara con el claim `tid`. |
| `ALLOWED_USER_OBJECT_IDS` | GUID[,GUID] | `foundry` | Se compara con el claim `oid`. |
| `AZURE_CLIENT_ID` | GUID | En App Service | Client ID de `<prefix>-web-id` para `ManagedIdentityCredential`. |
| `OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID` | GUID | En App Service | Client ID de `<prefix>-web-id`; Easy Auth lo usa en lugar de un secreto. |
| `WEB_FILES_MODE` | `none`, `local`, `blob` | No (`none`) | `local` prohibido en App Service. |
| `STORAGE_ACCOUNT_URL` / `OUTPUT_CONTAINER` | URL / texto | Con `blob` | Lectura con la identidad de la web. |
| `LOCAL_FILES_DIR` | ruta | Con `local` | Misma carpeta que el agente local. |
| `LOCAL_AGENT_URL` | URL | No | `local-http`: solo `http://localhost` o `http://127.0.0.1`. |
| `LOCAL_AGENT_DIR` | ruta | No | `inprocess`: carpeta `agent/` (por defecto, la vecina de `web/`). |
| `AGENT_TIMEOUT_SECONDS` | 5–600 | No (`120`) | Timeout de lectura de la invocación. |
| `AGENT_MAX_RETRIES` | 0–5 | No (`2`) | Reintentos automáticos de errores transitorios. |
| `WEB_TITLE` | texto | No | Título. |
| `WEBSITES_PORT` | `8000` | App Service | Puerto de Streamlit. |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` | App Service | Activa Oryx en ZIP deploy. `WEBSITE_RUN_FROM_PACKAGE` no debe existir. |

Comando de inicio (Bicep y `publish-web.ps1`):

```
python -m streamlit run "${APP_PATH:-/home/site/wwwroot}/web/streamlit_app.py" --server.address 0.0.0.0 --server.port 8000 --server.headless true --server.enableCORS true --server.enableXsrfProtection true --browser.gatherUsageStats false
```

## Ambiente azd (lo prepara `publish-agent.ps1`)

Carpeta: `.build/agent-publish/.azure/starter-<prefix>/`. Variables: `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_AI_PROJECT_ID` (proyecto existente; no se ejecuta `azd provision`), `FOUNDRY_PROJECT_ENDPOINT` (solo para `ai-project.endpoint` del manifiesto; no llega al contenedor) y las variables del agente anteriores.

En modo contenedor, además: `AZURE_CONTAINER_REGISTRY_ENDPOINT` y `AZURE_CONTAINER_REGISTRY_RESOURCE_ID` (salidas de Bicep). Según Learn, con ambos azd usa ese registro y no crea otro.

`azure.yaml`:

- **Versiones**: `requiredVersions` declara azd `>=1.27.1`, `azure.ai.agents >=1.0.0-beta.8` (host `azure.ai.agent`) y `azure.ai.projects >=1.0.0-beta.4` (host `azure.ai.project`). Son los mínimos de la referencia de `azure.yaml` de Learn (actualizada el 29-10-2026) y de *Author azure.yaml*. Otra página (*Manage hosted agents*) cita azd 1.23.0 para instalar la extensión; prevalece 1.27.1, que es lo que exige el manifiesto. `publish-agent.ps1` comprueba azd y que ambas extensiones estén instaladas; la versión de cada extensión la valida azd con `requiredVersions`.
- **Modo código**: `codeConfiguration` (`runtime: python_3_13`, `entryPoint: main.py`, `dependencyResolution: remote_build`).
- **Modo contenedor**: `package.py --image` sustituye `codeConfiguration` por `image: <registro>.azurecr.io/text2sql-agent@sha256:<digest>`. Learn indica no combinar ambos y recomienda fijar por digest.
- **Recursos**: `container.resources` admite `cpu` de 0.25 a 4.0 y `memory` de 0.5 a 8 Gi (piloto: 0.5 / 1 Gi).
- **Autorización**: `authorizationSchemes` es `Entra` por defecto (`publish-agent.ps1 -AuthScheme`), el alias de `AzureRbac` con el que azd lo declara.
- **Contraseña SQL**: `SQL_PASSWORD` solo aparece como `${{connections.<SQL_PASSWORD_CONNECTION>.credentials.SQL_PASSWORD}}`.

## Archivos de entrada

```powershell
az storage blob upload --auth-mode login --account-name <storageAccountName> --container-name inputs --name datos.txt --file .\datos.txt
```

Requiere Storage Blob Data Contributor en `inputs` (Bicep lo concede al publicador). El agente Text-to-SQL no lee archivos de entrada; solo escribe `outputs/<request_id>/resultado.json` (el `to_dict()` de `RespuestaAgente`, que puede contener filas de la vista).

## Dependencias

- `agent/requirements.txt` y `web/requirements.txt`: dependencias directas con rangos compatibles.
- `*/requirements.lock.txt`: versiones exactas resueltas para Linux x86_64 y Python 3.13 (`scripts/lock-dependencies.ps1`, con `uv`). Los paquetes publicados usan el lock si existe.
- `requirements-dev.txt`: entorno local (incluye `pytest`, `httpx` y `uv`).
- `docker/agent.Dockerfile`: la imagen que se publica en modo contenedor (ODBC Driver 18 e instala `agent/requirements.lock.txt`). `build-agent-image.ps1` la construye y la prueba.
- `httpx` (corregido):
  - **Fallo, reproducido en la imagen**: `azure-ai-agentserver-core` activa `microsoft-opentelemetry`, que carga `opentelemetry-instrumentation-openai-v2`. Ese paquete hace `from httpx import URL` pero no declara `httpx`, y `openai` 3.x solo depende de `httpx2`. Al arrancar se registraba `Exception occurred when instrumenting: openai ... ModuleNotFoundError: No module named 'httpx'`. El agente respondía, pero las llamadas al modelo no se trazaban.
  - **Por qué no se detectó en local**: el entorno de desarrollo ya tenía `httpx` (`requirements-dev.txt`) y lo ocultaba.
  - **Corrección mínima**: `httpx>=0.28,<0.29` en `agent/requirements.txt` y lock regenerado. Solo se añaden `httpx==0.28.1` y `httpcore==1.0.9`; las demás versiones y el lock de la web no cambian.
  - **Verificación**: la imagen arranca sin el error («Tracing configured successfully»). Una llamada `chat.completions` contra un servidor falso genera el span `chat` con los tokens y devuelve la respuesta intacta.
