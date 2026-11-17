# Diagnóstico

Primero ejecute las comprobaciones de solo lectura; cada una indica la etapa que falla:

```powershell
./scripts/configure-auth.ps1 -CheckOnly
./scripts/grant-agent-permissions.ps1 -CheckOnly
./scripts/check-availability.ps1            # añada -InvokeAgent para una invocación real
```

Registros útiles:

```powershell
az webapp log tail -g <rg> -n <prefix>-web                         # arranque y errores de Streamlit
az webapp log download -g <rg> -n <prefix>-web --log-file web-logs.zip
az webapp log deployment show -g <rg> -n <prefix>-web               # compilación Oryx
azd ai agent show --no-prompt                                       # desde .build/agent-publish
```

Los registros del agente (portal de Foundry > agente > *Logs*, o Application Insights del proyecto) contienen solo metadatos: `request_id`, código de error y tiempos. Nunca el mensaje del usuario.

## Sesión y destino

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `La sesión apunta a la suscripción …` | `az` está en otra suscripción o tenant | `az login --tenant <tenantId>`; `az account set --subscription <subscriptionId>` |
| `Edite …: subscriptionId debe ser un GUID real` | Parámetros de ejemplo sin editar | Complete `pilot.bicepparam` |
| `No existe el despliegue foundry-starter-<prefix>` | Infraestructura no aplicada o prefijo distinto | `deploy-infra.ps1` con el mismo archivo de parámetros |
| `La sesión azd no está activa` | Token de azd vencido | `azd auth login --tenant-id <tenantId>` (o `azd config set auth.useAzCliAuth true` para reutilizar `az login`) |
| `AADSTS53001` / dispositivo no conforme | Acceso condicional | Ejecute desde un dispositivo que cumpla la política |
| Texto con caracteres extraños en Windows PowerShell | Script guardado sin BOM | Los scripts deben ser UTF-8 con BOM (lo comprueba `tests/test_scripts.py`) |

## Autenticación (Entra / Easy Auth)

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `AADSTS50011` redirect URI mismatch | Falta la Redirect URI web | `configure-auth.ps1` (la añade) o *Authentication > Web*: salida `webRedirectUri` |
| Callback 401 y `AADSTS700054: response_type 'id_token' is not enabled` | ID tokens deshabilitado | `configure-auth.ps1` o *Authentication > Implicit grant and hybrid flows > ID tokens* |
| `AADSTS50105` el usuario no está asignado | *Assignment required = Yes* y el usuario sin asignar | `configure-auth.ps1` lo asigna; o *Enterprise applications > Users and groups* |
| `AADSTS700016` aplicación no encontrada | `authClientId` es el Object ID o de otro tenant | Use **Application (client) ID** de *App registrations* |
| Error AADSTS de aserción de cliente al volver de Entra (callback 401/500) | Credencial federada ausente o con sujeto erróneo | El sujeto es el **principalId** (Object ID) de `<prefix>-web-id`, no su clientId; emisor `https://login.microsoftonline.com/<tenantId>/v2.0`; audiencia `api://AzureADTokenExchange`. Compruebe que `OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID` contiene el **clientId** de esa identidad |
| `No se pudo crear la credencial federada` | El operador no administra el registro | Propietario del registro o rol Application/Cloud Application Administrator; o créela en *Certificates & secrets > Federated credentials > Other issuer* con los valores anteriores |
| La página dice «No se pudo verificar tu identidad Entra» | Falta `X-MS-CLIENT-PRINCIPAL` (Easy Auth desactivado) o `tid` distinto | `configure-auth.ps1 -CheckOnly`; `EXPECTED_TENANT_ID` = tenant del usuario |
| «Tu cuenta no está autorizada» | Usuario asignado en Entra pero no en `allowedUserObjectIds` | Añada su Object ID y vuelva a `deploy-infra.ps1` |
| Cualquier usuario del tenant inicia sesión | *Assignment required = No* | `configure-auth.ps1` |
| El registro admite cuentas de varios tenants | `signInAudience` distinto de `AzureADMyOrg` | Cambie *Supported account types* a un solo tenant |

## Permisos (RBAC)

| Síntoma | Causa | Solución |
| --- | --- | --- |
| Web: «no tiene permiso para invocar el agente» (403) | `<prefix>-web-id` sin Foundry Agent Consumer | `deploy-infra.ps1`; espere la propagación (hasta ~10 min) |
| Web: «No se encontró el agente publicado» (404) | Agente no publicado o `HOSTED_AGENT_NAME` distinto | `publish-agent.ps1`; compare con `agentName` |
| `model_forbidden` | Identidad del agente sin Cognitive Services OpenAI User o RBAC sin propagar | `grant-agent-permissions.ps1`; espere unos minutos. Si persiste, pruebe `MODEL_TOKEN_SCOPE=https://ai.azure.com/.default`. Solo si su variante usa el endpoint del proyecto para el modelo, evalúe conceder Foundry User a la identidad del proyecto |
| `model_not_found` | `MODEL_DEPLOYMENT_NAME` no existe | Compare con `model.deploymentName` |
| `azd deploy` 403 / no puede crear la versión | El publicador no tiene Foundry Project Manager | `publisherObjectId` en parámetros y `deploy-infra.ps1`. Owner/Contributor de Azure no incluyen las acciones de datos de agentes |
| `No se pudo asignar …` | El operador no puede escribir asignaciones de rol | Owner, User Access Administrator o Role Based Access Control Administrator |
| Permisos correctos que dejan de funcionar tras publicar | La identidad del agente cambió | `publish-agent.ps1` vuelve a asignar; `grant-agent-permissions.ps1` muestra las asignaciones antiguas y las elimina con `-RemoveStale` |
| `check-availability -InvokeAgent` 403 | El operador no tiene Foundry Agent Consumer | Defina `publisherObjectId` o asígnelo manualmente en el proyecto |

## Archivos

| Síntoma | Causa | Solución |
| --- | --- | --- |
| «Los archivos no están habilitados» | `files.enabled = false` | Active y repita `deploy-infra.ps1` → `publish-agent.ps1` → `publish-web.ps1` |
| Agente: `AuthorizationPermissionMismatch` (500 interno) | Identidad del agente sin rol en el contenedor | `grant-agent-permissions.ps1` |
| Web: «no tiene permiso de lectura sobre el contenedor» | `<prefix>-web-id` sin Blob Data Reader en `outputs` | `deploy-infra.ps1` |
| `KeyBasedAuthenticationNotPermitted` | Se intentó usar clave o SAS | Correcto por diseño: use `--auth-mode login` y RBAC |
| «Este archivo no pertenece a la conversación actual» | Se pidió un archivo de otra sesión | Por diseño: solo se descargan archivos devueltos en la sesión |
| `input_not_found` | Nombre distinto al blob en `inputs` | `az storage blob list --auth-mode login --account-name <cuenta> -c inputs` |

## Cuota y rendimiento

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `model_rate_limited`, «El modelo alcanzó su cuota» | Capacidad (TPM/RPM) del despliegue | La web reintenta con `Retry-After`; aumente `model.capacity` (comprueba la cuota `preview-infra.ps1`) |
| `session_quota_exceeded` (429) | Demasiadas sesiones activas | No se reintenta. Use **Nueva conversación** solo cuando haga falta, espere a que expiren o pida aumento |
| `regional_session_quota_exceeded` (429) | Capacidad regional | Reintento automático; si persiste, otra región |
| `Cuota insuficiente` en `preview-infra.ps1` | Capacidad solicitada > libre | Reduzca `model.capacity` o solicite cuota |
| `El modelo … no está disponible en <región>` | Nombre, versión o SKU no ofrecidos | Use una versión de las listadas o cambie `location` |
| Timeout en la web | Turno más largo que `AGENT_TIMEOUT_SECONDS` | **Reintentar** reenvía el mismo turno; aumente el timeout o reduzca `MODEL_MAX_OUTPUT_TOKENS` |
| Primera respuesta lenta | Arranque en frío del sandbox | Normal tras publicar o tras inactividad |
| `Too many agent versions` (400 `UserError`) | Límite de versiones | Elimine versiones antiguas en el portal de Foundry |

## Arranque de la web

| Síntoma | Causa | Solución |
| --- | --- | --- |
| 503 justo después de publicar | Streamlit aún arrancando (~1 min) | `check-availability.ps1` espera `/_stcore/health` |
| `ModuleNotFoundError: web` | Comando de inicio sin la ruta de la app | Debe usar `${APP_PATH:-/home/site/wwwroot}/web/streamlit_app.py`; `publish-web.ps1` lo corrige |
| «La aplicación no está configurada: …» | App setting ausente o no válido | El mensaje nombra la variable; ver CONFIGURATION.md |
| «En App Service solo se admite WEB_AGENT_MODE=foundry» | Modo local en la nube | Por diseño: no hay respaldo local en Azure |
| La página carga pero se desconecta | WebSockets bloqueados | `webSocketsEnabled: true` (Bicep) |
| `QuotaExceeded` | Plan F1 o cuota de CPU | Use B1 o superior |

## Compilación y publicación

| Síntoma | Causa | Solución |
| --- | --- | --- |
| «El registro no confirma Oryx» | `WEBSITE_RUN_FROM_PACKAGE` o `SCM_DO_BUILD_DURING_DEPLOYMENT` ausente | `publish-web.ps1` corrige ambos; repita |
| Despliegue web en estado 3 | Falla `pip install` | Revise el registro; regenere el lock con `lock-dependencies.ps1` para la versión de Python de `web.pythonVersion` |
| «El despliegue … sigue en curso» | ZIP anterior sin terminar | Espere; no envíe dos ZIP a la vez |
| ZIP recibido pero sitio antiguo | La compilación no terminó | `publish-web.ps1` solo informa éxito con estado 4 y Oryx en el registro |
| Versión del agente `failed` / `CodeError` | Error de importación o dependencia en la compilación remota | `validate-local.ps1` (importa el paquete aislado); revise el campo `error` que imprime `publish-agent.ps1` |
| `azd` rechaza `type: AzureRbac` | Extensión `azure.ai.agents` antigua | `azd extension upgrade azure.ai.agents` o `publish-agent.ps1 -AuthScheme Entra` (alias heredado, mismo comportamiento) |
| `Falta la extensión de agentes` | Extensión no instalada | `azd extension install azure.ai.agents` (o el paquete `microsoft.foundry`) |
| Foundry en `Creating`/`Updating`/`Failed` | Operación previa en curso o fallida | Espere; con `Failed`: `az deployment group operation list -g <rg> -n foundry-starter-resources` |
| Bicep no encontrado | Bicep no está en el `PATH` | `az bicep install` o `$env:BICEP_PATH` |
| Una conversación sigue con la versión anterior | La sesión se creó antes de publicar | **Nueva conversación** |
| `build-agent-image.ps1`: «El motor de Docker no responde» | Docker Desktop parado o en modo Windows | Inicie Docker Desktop (motor Linux) y repita |
| `build-agent-image.ps1`: falla la prueba de humo (driver/telemetría) | Dockerfile o lock modificados | `docker run --rm --entrypoint python <imagen> -c "import pyodbc; print(pyodbc.drivers())"`; regenere el lock con `lock-dependencies.ps1` |
| `az acr login` falla | Falta AcrPush (Bicep la concede a `publisherObjectId`) o Docker parado | Compruebe la asignación en el registro; el registro no tiene usuario administrador a propósito |
| `publish-agent.ps1`: «agentHosting.mode=… pero el despliegue Bicep es …» | Cambió `agentHosting` sin volver a desplegar | `preview-infra.ps1` → `deploy-infra.ps1` |
| `azd deploy` falla al asignar AcrPull o al descargar la imagen | El operador no tiene `roleAssignments/write` en el registro, o la política `azureADAuthenticationAsArmPolicy` está desactivada | Owner o RBAC Administrator en el grupo de recursos; `az acr config authentication-as-arm show -r <registro>` debe dar `enabled` |
| Versión `failed` en modo contenedor | Imagen no linux/amd64 o error al arrancar | `build-agent-image.ps1` construye `linux/amd64` y prueba `/readiness` e `/invocations` antes de subir |
| `configure-auth.ps1` / `publish-web.ps1`: «web.deploy=false…» | La web no se despliega en este piloto | Correcto: el agente se consume por API. Para la web: `web.deploy: true` y README § Web |

## Text-to-SQL

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `sql_config`: «Configura SQL_VIEW_SCHEMA y SQL_VIEW_NAME…» | Falta la vista (local: `.env`/terminal; Foundry: `agent.env` y republicar) | Defina los nombres confirmados; `dbo` no se asume |
| `sql_config`: «Esta adaptación utiliza T-SQL…» | `SQL_ENGINE` distinto de `sqlserver`/`azuresql` | El núcleo solo admite SQL Server/Azure SQL; otro motor requiere adaptar el núcleo |
| `text2sql_config` | `TEXT2SQL_EXECUTE` o `TEXT2SQL_SQL_BACKEND` con valor no válido | `true`/`false` y `odbc`/`simulated` |
| La respuesta dice «Simulación» en Foundry | Publicado con `-Responder echo` | `publish-agent.ps1` sin `-Responder` (usa `model`) |
| «SQL validado (no ejecutado…)» y nunca hay filas | `TEXT2SQL_EXECUTE=false` (por defecto) | Correcto por diseño hasta que la ejecución esté autorizada |
| «No se obtuvo una consulta válida» + SQL rechazado | El modelo generó algo fuera del alcance (otra tabla, `*`, JOIN, DML…) | Correcto por diseño: el validador del núcleo lo bloquea. Revise el prompt o la pregunta, no el validador |
| «No fue posible ejecutar la consulta. Revisa la configuración y los metadatos.» | La vista real no tiene las columnas pedidas, falta la cadena o el driver ODBC | `python agent/text2sql_core.py --metadatos` (local) y compare con `COLUMNAS_VISTA` |
| «No fue posible ejecutar la consulta. Revisa conexión, permisos…» | Login/permiso denegado, driver o librería ODBC ausente | Registros del agente: tipo de excepción (sin detalles). Compruebe usuario SQL y `GRANT SELECT` |
| Lo mismo solo en Foundry, en local funciona | `SQL_PASSWORD` vacía: la conexión no existía al iniciar la sesión, o su nombre o campo no coincide | `set-sql-secret.ps1 -CheckOnly`; vuelva a publicar (las sesiones existentes conservan el valor anterior) |
| `sql_unavailable` (503) | Timeout o comunicación con Azure SQL | Reintento automático; si persiste, firewall/red privada o `SQL_QUERY_TIMEOUT` |
| `sql_unavailable` solo en Foundry | El firewall de Azure SQL no admite la salida del Hosted Agent (sin IP fijas documentadas) | «Allow Azure services and resources» o red privada (ARCHITECTURE.md § Etapa 2) |
| `publish-agent.ps1`: «SQL_PASSWORD no se admite…» / «no debe contener contraseñas» | Secreto en `agent.env` | `set-sql-secret.ps1` y `SQL_PASSWORD_CONNECTION` (CONFIGURATION.md § Secretos) |
| `publish-agent.ps1`: «SQL_ODBC_DRIVER debe ser 'ODBC Driver 18…'» | Se copió el driver del `.env` local (17) | En `agent.env`, `SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server`; el `.env` local no cambia |
| `publish-agent.ps1`: «No existe la conexión …» | Falta el paso del secreto | `./scripts/set-sql-secret.ps1` antes de publicar |
| `publish-agent.ps1`: «TEXT2SQL_EXECUTE=true necesita el driver ODBC…» | `agentHosting.mode: code` | `agentHosting.mode: container` y `deploy-infra.ps1` |

### Driver ODBC en el Hosted Agent

`pyodbc` se instala desde su wheel Linux, pero la wheel **no incluye** unixODBC ni **Microsoft ODBC Driver 18 for SQL Server**, y `remote_build` solo instala paquetes de `requirements.txt`. Por eso la ejecución SQL en Foundry usa el **modo contenedor**:

- `agentHosting.mode: container` publica `docker/agent.Dockerfile` (con `msodbcsql18`), subida a un ACR del piloto y fijada por digest.
- `build-agent-image.ps1` comprueba en local `pyodbc.drivers()` = `ODBC Driver 18 for SQL Server`.
- `publish-agent.ps1` rechaza `TEXT2SQL_EXECUTE=true` con `mode: code`, y cualquier otro driver en modo contenedor.

Local y publicado usan drivers distintos a propósito: `.env` declara el 17, que es el instalado en su equipo, y `agent.env` el 18, que es el de la imagen. El paso 6 de README § Probar ejecuta la imagen contra su base real con el Driver 18.

**`Authentication=ActiveDirectoryMsi` no sirve en el Hosted Agent.** Ese modo del driver ODBC obtiene el token de IMDS (`169.254.169.254`), pero el Hosted Agent expone su identidad con `IDENTITY_ENDPOINT`/`IDENTITY_HEADER`. Para App Service, que expone la identidad igual, Learn documenta que en un contenedor propio la conexión se bloquea hasta el tiempo de espera. Por eso el piloto conserva la autenticación SQL con la contraseña en una conexión del proyecto, y `publish-agent.ps1` avisa si la cadena usa `ActiveDirectoryMsi`. La alternativa sin contraseña requiere un cambio de código (CONFIGURATION.md § Secretos).

### Modo local

| Síntoma | Causa | Solución |
| --- | --- | --- |
| `did not find executable at 'C:\Users\<otro>\…\python.exe'` | `.venv` copiado de otro equipo: guarda rutas absolutas | `Remove-Item -Recurse -Force .venv`; `py -3.13 -m venv .venv`; reinstale `requirements-dev.txt` |
| «la ejecución de scripts está deshabilitada» | Directiva de ejecución de PowerShell | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` (solo esa consola) |
| `-Responder model requiere -Mode local-http o -Mode agent` | En `inprocess` la web fuerza `STARTER_RESPONDER=echo` | `./scripts/run-local.ps1 -Mode agent -Responder model` |
| `model_config` al lanzar `python agent/main.py` a mano | Ese proceso no lee `MODEL_*` de `.env` | `./scripts/run-local.ps1 -Mode agent -Responder model` (carga `.env`) |
| `run-local.ps1 -Check` marca `ERR` | Falta una variable obligatoria del modo o tiene un marcador (`REEMPLAZAR…`, `<…>`) | Complete `.env`; la salida nunca muestra valores |
| `model_forbidden` en local | Su usuario no tiene Cognitive Services OpenAI User o `az login` en otro tenant | Asigne el rol; `az login --tenant <tenantId>` |
| `IM002` / «Data source name not found» | El driver de `SQL_ODBC_DRIVER` (o de la cadena) no está instalado | `Get-OdbcDriver -Platform 64-bit`; ajuste `SQL_ODBC_DRIVER` en `.env` al instalado (17 o 18). En el contenedor: `-e "SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server"` |
| `ModuleNotFoundError: httpx` al instrumentar `openai` | Imagen construida con el lock anterior | Reconstruya con `build-agent-image.ps1` (el lock ya incluye `httpx`) |
| «El agente no respondió en …/readiness» / puerto ocupado | Otro proceso usa el puerto 8088 o el agente no arrancó | `Get-NetTCPConnection -LocalPort 8088`; use `-AgentPort 8090` e `invoke-local.ps1 -Url http://localhost:8090` |
| `No se encontró Bicep CLI` | Bicep no instalado | `$env:AZURE_CONFIG_DIR = "$PWD\.azure-local"; az bicep install --version v0.48.1` (local al proyecto) o `$env:BICEP_PATH` |
| `docker build`: `dockerDesktopLinuxEngine` no encontrado | Docker Desktop no está arrancado | Inicie Docker Desktop y repita |
| Las pruebas ignoran mi `.env` | `tests/conftest.py` fija `PYTHON_DOTENV_DISABLED=1` | Correcto por diseño: las pruebas no dependen de configuración ni secretos locales |
