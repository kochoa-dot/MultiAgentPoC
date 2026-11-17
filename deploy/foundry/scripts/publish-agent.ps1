<#
.SYNOPSIS
  Publica una versión nueva del Hosted Agent (código o contenedor) y asigna sus permisos.
.DESCRIPTION
  1. Valida agent.env y los parámetros antes de tocar Azure.
  2. Comprueba azd, las extensiones azure.ai.agents y azure.ai.projects, y las sesiones azd y Azure CLI.
  3. agentHosting.mode=container: construye, prueba y sube la imagen con build-agent-image.ps1 -Push
     (o usa -Image <registro>.azurecr.io/text2sql-agent@sha256:...). agentHosting.mode=code: remote_build.
  4. Genera .build/agent-publish/ (azure.yaml renderizado + agent/ plano) sin modificar archivos
     versionados, prepara el ambiente azd con las salidas de Bicep y ejecuta «azd deploy».
  5. Espera a que la versión quede activa y ejecuta grant-agent-permissions.ps1.
  Repetirlo publica otra versión del mismo agente (mismo nombre).

  agent.env (copia de agent.env.example, ignorada por git) no admite secretos:
  - Modelo: con model.deploy=false son obligatorios MODEL_ENDPOINT, MODEL_DEPLOYMENT_NAME y
    MODEL_RESOURCE_ID (este no llega al contenedor: sirve para asignar Cognitive Services OpenAI User).
  - SQL (solo si TEXT2SQL_EXECUTE=true, que exige agentHosting.mode=container por el driver ODBC):
      a) SQL_SERVER, SQL_DATABASE, SQL_USER y SQL_PASSWORD_CONNECTION (conexión CustomKeys creada con
         set-sql-secret.ps1; Foundry inyecta SQL_PASSWORD al iniciar la sesión), o
      b) SQL_CONNECTION_STRING sin contraseña.
  - SQL_ODBC_DRIVER debe ser «ODBC Driver 18 for SQL Server» en modo contenedor (el único de la imagen),
    aunque el .env local use otro.
  -AuthScheme Entra (por defecto) es el alias de AzureRbac con el que azd declara el esquema.
.EXAMPLE
  ./scripts/publish-agent.ps1
  ./scripts/publish-agent.ps1 -Responder echo   # generador simulado, para aislar problemas de inferencia
  ./scripts/publish-agent.ps1 -Image starter01acr.azurecr.io/text2sql-agent@sha256:<digest>
#>
param(
  [string]$ParametersFile,
  [string]$AgentSettingsFile,
  [ValidateSet('model', 'echo')][string]$Responder = 'model',
  [ValidateSet('AzureRbac', 'Entra')][string]$AuthScheme = 'Entra',
  [string]$Image,
  [int]$ActivationTimeoutSeconds = 900
)
. (Join-Path $PSScriptRoot 'common.ps1')

# Lee agent.env: solo claves conocidas, sin contraseñas ni variables reservadas.
function Read-AgentSettings([string]$Path) {
  if (-not $Path) { $Path = Join-Path $script:StarterRoot 'agent.env' }
  if (-not (Test-Path -LiteralPath $Path)) { throw "No existe $Path. Copie agent.env.example a agent.env y edítelo." }
  $values = [ordered]@{
    SQL_ENGINE = 'sqlserver'; SQL_VIEW_SCHEMA = ''; SQL_VIEW_NAME = ''; SQL_MAX_ROWS = '200'; SQL_DEFAULT_ROWS = '50'
    SQL_QUERY_TIMEOUT = '30'; SQL_ODBC_DRIVER = $script:ContainerOdbcDriver; SQL_CONNECTION_STRING = ''
    SQL_SERVER = ''; SQL_DATABASE = ''; SQL_USER = ''; SQL_PASSWORD_CONNECTION = ''
    TEXT2SQL_EXECUTE = 'false'; MODEL_ENDPOINT = ''; MODEL_DEPLOYMENT_NAME = ''; MODEL_RESOURCE_ID = ''
  }
  foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith('#')) { continue }
    $index = $text.IndexOf('=')
    if ($index -lt 1) { throw "Línea no válida en ${Path}: se espera CLAVE=valor." }
    $key = $text.Substring(0, $index).Trim()
    $value = $text.Substring($index + 1).Trim()
    if ($key -eq 'SQL_PASSWORD') {
      throw "SQL_PASSWORD no se admite en ${Path}: guárdela con ./scripts/set-sql-secret.ps1 y defina SQL_PASSWORD_CONNECTION."
    }
    if (-not $values.Contains($key)) { throw "$key no se admite en $Path (no incluya secretos ni variables FOUNDRY_*/AGENT_*)." }
    if ($value.Contains('"')) { throw "$key no puede contener comillas dobles." }
    $values[$key] = $value
  }
  if (-not $values['SQL_VIEW_SCHEMA'] -or $values['SQL_VIEW_SCHEMA'] -eq 'REEMPLAZAR_ESQUEMA' -or -not $values['SQL_VIEW_NAME']) {
    throw "Defina SQL_VIEW_SCHEMA y SQL_VIEW_NAME confirmados en $Path."
  }
  foreach ($key in @('SQL_MAX_ROWS', 'SQL_DEFAULT_ROWS', 'SQL_QUERY_TIMEOUT')) {
    if ($values[$key] -notmatch '^\d+$') { throw "$key debe ser un entero en $Path." }
  }
  if (@('true', 'false') -notcontains $values['TEXT2SQL_EXECUTE']) { throw "TEXT2SQL_EXECUTE debe ser true o false en $Path." }

  # SQL: cadena de conexión sin contraseña, o autenticación SQL con la contraseña en una conexión del proyecto.
  $connectionString = $values['SQL_CONNECTION_STRING']
  if ($connectionString -match '(?i)(^|;)\s*(pwd|password)\s*=') {
    throw 'SQL_CONNECTION_STRING no debe contener contraseñas: use SQL_SERVER/SQL_DATABASE/SQL_USER con SQL_PASSWORD_CONNECTION.'
  }
  if ($connectionString -match '(?i)ActiveDirectoryMsi') {
    Write-Warn ('Authentication=ActiveDirectoryMsi: el driver ODBC busca IMDS y el Hosted Agent expone IDENTITY_ENDPOINT; ' +
      'Learn documenta que en ese caso la conexión se bloquea hasta el tiempo de espera (ver TROUBLESHOOTING.md).')
  }
  $sqlAuthKeys = @('SQL_SERVER', 'SQL_DATABASE', 'SQL_USER', 'SQL_PASSWORD_CONNECTION')
  $sqlAuthSet = @($sqlAuthKeys | Where-Object { $values[$_] })
  if ($sqlAuthSet.Count -gt 0 -and $sqlAuthSet.Count -lt $sqlAuthKeys.Count) {
    throw "Autenticación SQL incompleta en ${Path}: defina juntos $($sqlAuthKeys -join ', ')."
  }
  if ($sqlAuthSet.Count -gt 0 -and $connectionString) { throw 'Defina SQL_CONNECTION_STRING o la autenticación SQL (SQL_SERVER...), no ambas.' }
  if ($values['SQL_SERVER'] -and $values['SQL_SERVER'] -notmatch '^[A-Za-z0-9_.-]+$') {
    throw 'SQL_SERVER debe ser solo el hostname (p. ej. <servidor>.database.windows.net), sin protocolo ni puerto.'
  }
  if ($values['SQL_PASSWORD_CONNECTION'] -and $values['SQL_PASSWORD_CONNECTION'] -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{2,32}$') {
    throw 'SQL_PASSWORD_CONNECTION: nombre de conexión de 3-33 letras, números, _ o -.'
  }
  if ($values['TEXT2SQL_EXECUTE'] -eq 'true' -and -not $connectionString -and $sqlAuthSet.Count -eq 0) {
    throw 'TEXT2SQL_EXECUTE=true requiere SQL_SERVER, SQL_DATABASE, SQL_USER y SQL_PASSWORD_CONNECTION (o SQL_CONNECTION_STRING).'
  }

  if ([bool]$values['MODEL_ENDPOINT'] -ne [bool]$values['MODEL_DEPLOYMENT_NAME']) { throw 'MODEL_ENDPOINT y MODEL_DEPLOYMENT_NAME se definen juntos o ninguno.' }
  if ($values['MODEL_ENDPOINT'] -and $values['MODEL_ENDPOINT'] -notmatch '^https://\S+$') { throw "MODEL_ENDPOINT debe usar HTTPS (https://<recurso>.openai.azure.com) en $Path." }
  if ($values['MODEL_RESOURCE_ID']) {
    if (-not $values['MODEL_ENDPOINT']) { throw 'MODEL_RESOURCE_ID solo se usa junto con MODEL_ENDPOINT (modelo externo).' }
    if ($values['MODEL_RESOURCE_ID'] -notmatch '^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+$') {
      throw 'MODEL_RESOURCE_ID debe ser /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<cuenta>.'
    }
  }
  return $values
}

$agentSettings = Read-AgentSettings $AgentSettingsFile  # se valida antes de consultar Azure
$p = Read-StarterParameters $ParametersFile
$hosting = Get-AgentHosting $p
if (-not (Test-ModelDeploy $p) -and (-not $agentSettings['MODEL_ENDPOINT'] -or -not $agentSettings['MODEL_RESOURCE_ID'])) {
  throw 'model.deploy=false: defina MODEL_ENDPOINT, MODEL_DEPLOYMENT_NAME y MODEL_RESOURCE_ID del modelo existente en agent.env.'
}
if ($hosting.mode -eq 'code' -and $agentSettings['TEXT2SQL_EXECUTE'] -eq 'true') {
  throw 'TEXT2SQL_EXECUTE=true necesita el driver ODBC, que remote_build no instala: use agentHosting.mode=container.'
}
if ($hosting.mode -eq 'container') {
  if ($agentSettings['SQL_ODBC_DRIVER'] -ne $script:ContainerOdbcDriver) {
    throw "SQL_ODBC_DRIVER debe ser '$($script:ContainerOdbcDriver)' en agent.env: es el único driver de la imagen (el .env local puede usar otro)."
  }
  if ($agentSettings['SQL_CONNECTION_STRING'] -match '(?i)driver\s*=\s*\{?([^};]+)' -and $Matches[1].Trim() -ne $script:ContainerOdbcDriver) {
    throw "SQL_CONNECTION_STRING usa el driver '$($Matches[1].Trim())'; la imagen solo tiene '$($script:ContainerOdbcDriver)'."
  }
}
if ($Image -and $hosting.mode -ne 'container') { throw '-Image solo se usa con agentHosting.mode=container.' }
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p
if ([string](Get-Prop $outputs 'agentHostingMode') -ne $hosting.mode) {
  throw "Los parámetros indican agentHosting.mode=$($hosting.mode), pero el despliegue Bicep es '$(Get-Prop $outputs 'agentHostingMode')'. Ejecute deploy-infra.ps1."
}
$modelEndpoint = $outputs.modelEndpoint
$modelDeployment = $outputs.modelDeploymentName
if ($agentSettings['MODEL_ENDPOINT']) {
  $modelEndpoint = $agentSettings['MODEL_ENDPOINT']
  $modelDeployment = $agentSettings['MODEL_DEPLOYMENT_NAME']
  if ($agentSettings['MODEL_RESOURCE_ID']) { Write-Ok "Modelo existente ($modelDeployment): se asignará Cognitive Services OpenAI User en MODEL_RESOURCE_ID." }
  else { Write-Warn "Modelo externo ($modelDeployment) sin MODEL_RESOURCE_ID: asigne a mano Cognitive Services OpenAI User a la identidad del agente en ese recurso (ver CONFIGURATION.md)." }
}
Write-Ok "Text-to-SQL: vista $($agentSettings['SQL_VIEW_SCHEMA']).$($agentSettings['SQL_VIEW_NAME']), ejecutar=$($agentSettings['TEXT2SQL_EXECUTE']), alojamiento=$($hosting.mode)"
$secretConnection = $agentSettings['SQL_PASSWORD_CONNECTION']
if ($secretConnection) {
  # Learn: la conexión debe existir antes de publicar la versión; si no, SQL_PASSWORD llega vacía.
  if (-not (Get-ProjectConnection $p $outputs $secretConnection)) {
    throw "No existe la conexión $secretConnection en el proyecto. Ejecute: ./scripts/set-sql-secret.ps1 -ConnectionName $secretConnection"
  }
  Write-Ok "Contraseña SQL desde la conexión $secretConnection (no pasa por azd)"
}

Write-Step 'Azure Developer CLI'
if (-not (Get-Command azd -ErrorAction SilentlyContinue)) { throw 'Instale Azure Developer CLI 1.27.1 o superior: https://aka.ms/azd-install' }
$version = Invoke-Native -FilePath 'azd' -Arguments @('version')
if ($version.Out -notmatch '(\d+\.\d+\.\d+)' -or [version]$Matches[1] -lt [version]'1.27.1') { throw "azd $($version.Out) es anterior a 1.27.1. Actualícelo." }
Write-Ok "azd $($Matches[1])"
$extensions = Invoke-Native -FilePath 'azd' -Arguments @('extension', 'list', '--installed')
# azure.ai.agents aporta host azure.ai.agent; azure.ai.projects aporta host azure.ai.project (azure.yaml).
foreach ($extension in @('azure.ai.agents', 'azure.ai.projects')) {
  if ($extensions.Out -notmatch [regex]::Escape($extension)) { throw "Falta la extensión $extension. Ejecute: azd extension install $extension" }
  Write-Ok "Extensión $extension instalada"
}
$auth = Invoke-Native -FilePath 'azd' -Arguments @('auth', 'status', '--output', 'json')
$authStatus = $null
if ($auth.ExitCode -eq 0 -and $auth.Out.Trim()) { $authStatus = Get-Prop ($auth.Out | ConvertFrom-Json) 'status' }
if ($authStatus -ne 'authenticated') { throw "La sesión azd no está activa. Ejecute: azd auth login --tenant-id $($p.tenantId)" }
Write-Ok 'Sesión azd activa'

if ($hosting.mode -eq 'container' -and -not $Image) {
  $Image = & (Join-Path $PSScriptRoot 'build-agent-image.ps1') -ParametersFile $p.ParametersFile -Push
  if (-not $Image) { throw 'build-agent-image.ps1 no devolvió la referencia de la imagen.' }
}
if ($Image) {
  $Image = [string]@($Image)[-1]
  if (-not $Image.StartsWith("$($outputs.containerRegistryLoginServer)/")) {
    throw "La imagen $Image no está en el registro del piloto ($($outputs.containerRegistryLoginServer))."
  }
  Write-Ok "Imagen $Image"
}

$before = Get-AgentIdentity (Get-HostedAgent $outputs)

Write-Step 'Contexto de publicación'
$stage = Join-Path $script:StarterRoot '.build/agent-publish'
# Variables sin valor: se quitan del manifiesto (Windows PowerShell 5.1 descarta argumentos vacíos).
$omit = @(@('SQL_CONNECTION_STRING', 'SQL_SERVER', 'SQL_DATABASE', 'SQL_USER') | Where-Object { -not $agentSettings[$_] })
$packageArguments = @((Join-Path $script:StarterRoot 'scripts/package.py'), 'agent', '--agent-name', $outputs.agentName,
  '--auth-scheme', $AuthScheme, '--out', $stage)
if ($Image) { $packageArguments += @('--image', $Image) }
if ($secretConnection) { $packageArguments += @('--sql-secret-connection', $secretConnection) }
if ($omit.Count -gt 0) { $packageArguments += @('--omit-env') + $omit }
& (Get-PythonPath) @packageArguments
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el contexto del agente.' }

$files = [bool]$outputs.filesEnabled
$unused = 'unused'  # Windows PowerShell 5.1 descarta argumentos vacíos al llamar a ejecutables
$settings = [ordered]@{
  AZURE_SUBSCRIPTION_ID   = $p.subscriptionId
  AZURE_TENANT_ID         = $p.tenantId
  AZURE_RESOURCE_GROUP    = $outputs.resourceGroupName
  AZURE_LOCATION          = $p.location
  AZURE_AI_PROJECT_ID     = $outputs.projectResourceId
  FOUNDRY_PROJECT_ENDPOINT = $outputs.projectEndpoint
  STARTER_RESPONDER       = $Responder
  MODEL_ENDPOINT          = $modelEndpoint
  MODEL_DEPLOYMENT_NAME   = $modelDeployment
  FILES_MODE              = $(if ($files) { 'blob' } else { 'none' })
  STORAGE_ACCOUNT_URL     = $(if ($files) { $outputs.storageAccountUrl } else { $unused })
  INPUT_CONTAINER         = $(if ($files) { $outputs.inputContainer } else { $unused })
  OUTPUT_CONTAINER        = $(if ($files) { $outputs.outputContainer } else { $unused })
}
if ($hosting.mode -eq 'container') {
  # Learn: con ambos valores azd usa este registro y no crea otro.
  $settings['AZURE_CONTAINER_REGISTRY_ENDPOINT'] = $outputs.containerRegistryLoginServer
  $settings['AZURE_CONTAINER_REGISTRY_RESOURCE_ID'] = $outputs.containerRegistryResourceId
  # Learn (azure.yaml reference, «Container builds and prebuilt images»): en despliegue desatendido,
  # AZD_AGENT_SKIP_ACR=true hace que azd use «image:» sin construir; si no, intenta compilar agent/ con buildpacks.
  $settings['AZD_AGENT_SKIP_ACR'] = 'true'
}
foreach ($key in @('SQL_ENGINE', 'SQL_VIEW_SCHEMA', 'SQL_VIEW_NAME', 'SQL_MAX_ROWS', 'SQL_DEFAULT_ROWS', 'SQL_QUERY_TIMEOUT',
    'SQL_ODBC_DRIVER', 'SQL_CONNECTION_STRING', 'SQL_SERVER', 'SQL_DATABASE', 'SQL_USER', 'TEXT2SQL_EXECUTE')) {
  if ($omit -notcontains $key) { $settings[$key] = $agentSettings[$key] }
}
$envName = "starter-$($p.prefix)"
Push-Location $stage
try {
  $select = Invoke-Native -FilePath 'azd' -Arguments @('env', 'select', $envName)
  if ($select.ExitCode -ne 0) {
    $new = Invoke-Native -FilePath 'azd' -Arguments @('env', 'new', $envName, '--subscription', $p.subscriptionId, '--location', $p.location, '--no-prompt')
    if ($new.ExitCode -ne 0) { throw "No se pudo crear el ambiente azd $envName.`n$($new.Err)" }
  }
  foreach ($entry in $settings.GetEnumerator()) {
    $set = Invoke-Native -FilePath 'azd' -Arguments @('env', 'set', $entry.Key, [string]$entry.Value)
    if ($set.ExitCode -ne 0) { throw "Falló azd env set $($entry.Key).`n$($set.Err)" }
  }
  Write-Ok "Ambiente azd $envName (proyecto existente; no se usa azd provision)"

  Write-Step "azd deploy starter-agent -> $($outputs.agentName) ($($hosting.mode))"
  & azd deploy starter-agent --no-prompt
  if ($LASTEXITCODE -ne 0) {
    throw 'Falló la publicación del agente. Revise la salida de azd (compilación remota o descarga de la imagen, permisos Foundry Project Manager y, en modo contenedor, roleAssignments/write en el registro).'
  }
} finally {
  Pop-Location
}

Write-Step 'Esperando a que la versión quede activa'
$deadline = (Get-Date).AddSeconds($ActivationTimeoutSeconds)
$agent = $null
while ((Get-Date) -lt $deadline) {
  $agent = Get-HostedAgent $outputs
  $latest = Get-AgentLatestVersion $agent
  $status = [string](Get-Prop $latest 'status')
  if (-not $latest) { Write-Warn 'La respuesta del agente no incluye versions.latest; no se puede seguir el estado.'; break }
  if ($status -eq 'active') { Write-Ok "Versión $(Get-Prop $latest 'version') activa"; break }
  if ($status -in @('failed', 'deleted', 'deleting')) {
    $errorInfo = Get-Prop $latest 'error'
    throw "La versión $(Get-Prop $latest 'version') quedó en $status. $(if ($errorInfo) { $errorInfo | ConvertTo-Json -Compress })"
  }
  Write-Host "    estado: $status"
  Start-Sleep -Seconds 10
}

$after = Get-AgentIdentity $agent
if (-not $after) { throw 'El agente no expone instance_identity; no se pueden asignar permisos.' }
if ($before -and $before.principal_id -ne $after.principal_id) {
  Write-Warn "La identidad del agente cambió: $($before.principal_id) -> $($after.principal_id). Se reasignan permisos."
}
$grantArguments = @{ ParametersFile = $p.ParametersFile }
if ($agentSettings['MODEL_RESOURCE_ID']) { $grantArguments['ModelResourceId'] = $agentSettings['MODEL_RESOURCE_ID'] }
& (Join-Path $PSScriptRoot 'grant-agent-permissions.ps1') @grantArguments
Write-Host ''
Write-Host 'Agente publicado. Siguiente paso: ./scripts/check-availability.ps1 -InvokeAgent' -ForegroundColor Green
if (Test-WebDeploy $p) { Write-Host 'Web: ./scripts/publish-web.ps1 (las sesiones abiertas antes de esta publicación pueden seguir en la versión anterior).' }
