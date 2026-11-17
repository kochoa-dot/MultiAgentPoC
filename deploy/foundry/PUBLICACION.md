# Guía de publicación del agente Text-to-SQL en Foundry

Esta guía cubre el procedimiento que se siguió para publicar el agente desde cero en el piloto. Recoge el orden de los pasos, los comandos y el resultado que se espera de cada uno, además de los problemas que surgieron y cómo se resolvieron.

El agente se consume solo por API, no tiene web.

- **No contiene secretos.** Las contraseñas se escriben solo cuando las pide un script o el portal.
- **Los identificadores de suscripción, tenant y usuarios** están en `infra/parameters/pilot.bicepparam`, que git ignora.

Para el detalle de cada variable, consulte [CONFIGURATION.md](CONFIGURATION.md). Para los errores, [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Estado publicado

Los valores reales del entorno no se guardan en el repositorio: se registran en `infra/parameters/pilot.bicepparam` y `agent.env`, que git ignora. Esta tabla usa marcadores.

| Elemento | Valor |
| --- | --- |
| Grupo de recursos | `<grupo-de-recursos>` (`<región>`) |
| Cuenta y proyecto Foundry | `<prefix>-foundry` / `<prefix>-project` |
| Agente | `<prefix>-agent`, alojado como contenedor (versión 2) |
| Imagen | `<prefix>acr.azurecr.io/text2sql-agent@sha256:<digest>` |
| Storage privado | `<prefix>files` (contenedores `inputs` y `outputs`) |
| Modelo (ya existía) | `<recurso-azure-openai>`, despliegue `<despliegue-modelo>` |
| Base (ya existía) | `<servidor-sql>` / `<base-de-datos>`, vista `<esquema_vista>.vw_chatbot_mom` |
| Usuario SQL del agente | `<usuario_sql_agente>`: solo `SELECT` sobre la vista |
| Contraseña SQL | En la conexión `sql-credentials` del proyecto, de tipo CustomKeys |
| Endpoint | `https://<prefix>-foundry.services.ai.azure.com/api/projects/<prefix>-project/agents/<prefix>-agent/endpoint/protocols/invocations?api-version=v1` |

## Requisitos

**Permisos del operador en el grupo de recursos:**
- crear despliegues;
- **Role Based Access Control Administrator**, porque Bicep y los scripts asignan roles.

Sin el segundo, el despliegue falla al crear las asignaciones.

**Herramientas:**
- Azure CLI con sesión en el tenant correcto.
- Bicep local, en `.azure-local\bin\bicep.exe`.
- azd 1.37.3 con las extensiones `azure.ai.agents` y `azure.ai.projects`.
- Docker Desktop.

Antes de los scripts de infraestructura, abra PowerShell en `deploy\foundry` y defina estas variables. Solo afectan a esa consola y evitan que `az` instale otro Bicep:

```powershell
$env:Path = "$PWD\.azure-local\bin;$env:Path"
$env:AZURE_BICEP_USE_BINARY_FROM_PATH = 'true'
$env:AZURE_BICEP_CHECK_VERSION = 'false'
```

## Pasos

### 1. Parámetros de la infraestructura

En `infra/parameters/pilot.bicepparam` se definen la suscripción, el tenant, el grupo de recursos y la región, más estos nombres:

| Parámetro | Valor |
| --- | --- |
| `prefix` | `<prefix>` |
| `agentName` | `<prefix>-agent` |
| `agentHosting.registryName` | `<prefix>acr` |
| `files.storageAccountName` | `<prefix>files` |

Además: `model.deploy=false` (se reutiliza el modelo existente) y `web.deploy=false`.

### 2. Red de Azure SQL

El agente no tiene una IP de salida fija, así que el firewall del servidor necesita la opción «Allow Azure services and resources to access this server».

```powershell
az sql server firewall-rule create -g <grupo-de-recursos> -s <servidor-sql> -n AllowAllWindowsAzureIps --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
```

Esta regla admite conexiones desde cualquier recurso de Azure, también de otros clientes. Por eso es obligatorio el paso 3.

### 3. Usuario SQL de solo lectura para el agente

Este paso se hace en el Query editor de `<base-de-datos>`, con la sesión de administrador.

La contraseña se genera con un gestor de contraseñas y no puede contener comillas simples. Después de ejecutar la instrucción, borre la contraseña del editor y cierre la pestaña sin guardar.

```sql
CREATE USER [<usuario_sql_agente>] WITH PASSWORD = '<CONTRASEÑA>';
GRANT SELECT ON OBJECT::[<esquema_vista>].[vw_chatbot_mom] TO [<usuario_sql_agente>];
```

El usuario no necesita permiso sobre la tabla porque el esquema de la vista y el de la tabla base tienen el mismo dueño.

**Comprobación:** cierre sesión, vuelva a entrar con *SQL authentication* como `<usuario_sql_agente>` y ejecute:

```sql
SELECT
  USER_NAME()                                                     AS usuario_en_bd,
  IS_ROLEMEMBER('db_owner')                                       AS es_db_owner,
  HAS_PERMS_BY_NAME('<esquema_vista>.vw_chatbot_mom', 'OBJECT', 'SELECT')     AS select_vista,
  HAS_PERMS_BY_NAME('<esquema_tabla>.<tabla_base>', 'OBJECT', 'SELECT')  AS select_tabla,
  HAS_PERMS_BY_NAME('<esquema_tabla>.<tabla_base>', 'OBJECT', 'INSERT')  AS insert_tabla,
  HAS_PERMS_BY_NAME('<esquema_tabla>.<tabla_base>', 'OBJECT', 'DELETE')  AS delete_tabla,
  HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE')        AS crear_tabla;
```

**Resultado esperado:** `<usuario_sql_agente>`, `select_vista = 1` y `0` en el resto de columnas.

El Query editor solo muestra el resultado de la última consulta del editor. Ejecute cada consulta por separado.

### 4. Configuración del agente (`agent.env`)

Solo contiene valores que no son secretos:

```
SQL_SERVER=<servidor-sql>.database.windows.net
SQL_DATABASE=<base-de-datos>
SQL_USER=<usuario_sql_agente>
SQL_VIEW_SCHEMA=<esquema_vista>
SQL_VIEW_NAME=vw_chatbot_mom
SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server
SQL_PASSWORD_CONNECTION=sql-credentials
TEXT2SQL_EXECUTE=true
```

`MODEL_ENDPOINT`, `MODEL_DEPLOYMENT_NAME` y `MODEL_RESOURCE_ID` apuntan a `<recurso-azure-openai>`.

`SQL_SERVER` va sin `tcp:` ni puerto. La contraseña **no** va en este archivo.

### 5. Infraestructura

```powershell
./scripts/preview-infra.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam   # solo lectura y what-if
./scripts/deploy-infra.ps1  -ParametersFile ./infra/parameters/pilot.bicepparam
```

**Resultado esperado:**
- **Vista previa:** 13 recursos a crear, y los recursos que ya existían marcados como *Ignore*.
- **Despliegue:** crea la cuenta, el proyecto, el ACR Basic, el Storage y los roles.

### 6. Contraseña SQL en el proyecto

```powershell
./scripts/set-sql-secret.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam
```

Pide la contraseña de `<usuario_sql_agente>` sin mostrarla y la guarda en la conexión `sql-credentials`. Si se repite, reemplaza el valor anterior.

### 7. Imagen del agente

Con Docker Desktop en marcha:

```powershell
./scripts/build-agent-image.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam -Push
```

El script construye la imagen con el ODBC Driver 18 y la prueba en local en modo simulado. Después la sube al ACR e imprime su referencia con la huella (`@sha256:...`), que también guarda en `.build/agent-image-<prefix>.txt`.

### 8. Publicación del agente

```powershell
./scripts/publish-agent.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam -Image <referencia @sha256 del paso 7>
```

**Resultado esperado:**
- «Versión N activa»;
- los permisos de la identidad del agente asignados: *Cognitive Services OpenAI User* en el modelo, *AcrPull* en el registro y los roles de Blob.

Si dice que la sesión de azd no está activa, ejecute `azd auth login --tenant-id <tenantId>` y repita.

### 9. Pruebas

Espere entre 2 y 5 minutos para que los permisos se propaguen y lance estas tres pruebas. El suministro `0099000001` y la ruta `99000001` son **datos de prueba sintéticos**: sustitúyalos por un suministro y una ruta existentes en la vista para obtener filas.

```powershell
./scripts/check-availability.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam -InvokeAgent -Message 'Hola'
./scripts/check-availability.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam -InvokeAgent -Message 'Datos del medidor del suministro 0099000001'
./scripts/check-availability.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam -InvokeAgent -Message 'Cuantos medidores hay en la ruta 99000001'
```

**Resultados esperados:**
1. **`Hola`:** `NO_RESPONDIBLE`, sin aviso de simulación. Confirma que el modelo responde.
2. **Suministro:** «SQL ejecutado · N fila(s)» sobre `[<esquema_vista>].[vw_chatbot_mom]`. Confirma la base, la contraseña y la red.
3. **Conteo:** coincide con un `SELECT COUNT(*)` hecho a mano en el Query editor.

Para validar una respuesta, ejecute en el Query editor el SQL que muestra el agente y compare el número de filas. El script solo muestra los primeros 400 caracteres de la respuesta.

### 10. Contraseña del administrador SQL

Cámbiela en el portal: servidor `<servidor-sql>`, *Overview*, *Reset password*. Así la contraseña que estuvo en archivos y en Foundry durante las pruebas deja de servir.

Si usa `.env` para pruebas locales, ponga ahí también `<usuario_sql_agente>` y su contraseña.

## Consumir el agente

`POST` al endpoint, con un token de Entra para `https://ai.azure.com` y este cuerpo:

```json
{ "contract": "starter.v1", "request_id": "<id único>", "message": "<pregunta>", "state": {} }
```

Para continuar una conversación, reenvíe el `state` que devolvió la respuesta anterior y añada a la URL `&agent_session_id=<cabecera x-agent-session-id>`.

Cada usuario o aplicación que llame al agente necesita el rol **Foundry Agent Consumer** sobre `<prefix>-project`. Lo puede asignar quien tenga RBAC Administrator en el grupo de recursos.

## Mantenimiento

| Cambio | Comandos |
| --- | --- |
| Código del agente | `build-agent-image.ps1 -Push`, después `publish-agent.ps1 -Image <nueva referencia>` |
| Configuración en `agent.env` | `publish-agent.ps1 -Image <referencia actual>` (crea una versión nueva) |
| Contraseña de `<usuario_sql_agente>` | Cambiarla en SQL (`ALTER USER [<usuario_sql_agente>] WITH PASSWORD = '...'`), después `set-sql-secret.ps1` y `publish-agent.ps1 -Image <referencia actual>` |
| Infraestructura | `preview-infra.ps1`, después `deploy-infra.ps1` |

## Empezar de cero

1. **Borre los recursos uno por uno desde el portal**: primero el proyecto Foundry y después la cuenta, el ACR y el Storage. **No use «Delete resource group»**: borraría el modelo y la base, y le quitaría el rol RBAC Administrator, que está asignado sobre el grupo.
2. **Tenga en cuenta el borrado temporal de las cuentas Foundry.** Una cuenta borrada pasa a borrado temporal unas 48 horas y mantiene reservado su nombre. Puede verlo con:
   ```powershell
   az cognitiveservices account list-deleted -o table
   ```
   Espere a que se purgue o use otro `prefix`, junto con nombres nuevos de registro y Storage.
3. **Quite el permiso del agente anterior sobre el modelo.** El agente borrado conserva su rol en el modelo. Bórrelo con:
   ```powershell
   az role assignment delete --assignee <principal_id anterior> --role "Cognitive Services OpenAI User" --scope <id del recurso Azure OpenAI>
   ```
4. Repita los pasos 5 a 9.

## Seguridad de la base: quién puede entrar

Para conectarse a la base hace falta **llegar a ella por la red** y además **autenticarse**:

| Vía | Quién |
| --- | --- |
| Red | Cualquier recurso de Azure (`AllowAllWindowsAzureIps`) y la IP del operador. El resto de Internet está bloqueado |
| Usuario `<usuario_sql_agente>` | Quien conozca su contraseña, guardada solo en `sql-credentials`. Solo puede leer la vista |
| Administrador SQL (`dbo`) | Quien conozca su contraseña, que guarda solo el operador |
| Gestión del grupo de recursos | Quien pueda cambiar el firewall o restablecer la contraseña del administrador |

**Pendientes para que solo el agente pueda entrar:**
- que el agente se conecte con su identidad de Entra en lugar de con contraseña, lo que requiere cambiar el código;
- o una red privada entre Foundry y SQL, que permitiría quitar `AllowAllWindowsAzureIps`.

## Problemas encontrados y solución

| Síntoma | Causa | Solución |
| --- | --- | --- |
| azd: `expanding SQL_PASSWORD: unable to parse variable name` | azd 1.37 expande `${{connections...}}` | `azure.yaml` usa `$${{...}}`, porque `$$` equivale a un `$` literal |
| azd intenta compilar con buildpacks (oryx), sin Dockerfile | Sin indicación, azd construye `project` | `package.py` añade `docker: imagePassthrough: true` y `publish-agent.ps1` define `AZD_AGENT_SKIP_ACR=true` |
| `check-availability` falla sin mostrar el motivo | PowerShell 5.1 descarta el cuerpo de las respuestas 4xx y 5xx | El script ahora lee y muestra el cuerpo del error |
| `az` intenta instalar Bicep | No encuentra el Bicep del proyecto | Defina las variables de entorno de la sección de requisitos |
| JMESPath con `contains(...)` falla en PowerShell | `az.cmd` rompe los paréntesis | Filtre en PowerShell: `(az ... -o json \| ConvertFrom-Json) \| Where-Object {...}` |
| No se puede reutilizar un nombre `*-foundry` | Cuenta en borrado temporal | Espere a la purga o use otro `prefix` |
| El Query editor muestra solo un resultado | Solo muestra el resultado de la última consulta | Seleccione y ejecute cada consulta por separado |
