# Funciones compartidas. Se cargan con: . (Join-Path $PSScriptRoot 'common.ps1')
# Compatible con Windows PowerShell 5.1 y PowerShell 7 (sin operadores exclusivos de PS7).
Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

$script:StarterRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:ZeroGuid = '00000000-0000-0000-0000-000000000000'
$script:Roles = @{
  FoundryAgentConsumer  = 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'
  FoundryProjectManager = 'eadc314b-1a2d-4efa-be10-5d325db5065e'
  CognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  BlobDataReader        = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  BlobDataContributor   = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  AcrPull               = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}
# Único driver ODBC de la imagen docker/agent.Dockerfile (el local puede ser otro, p. ej. Driver 17).
$script:ContainerOdbcDriver = 'ODBC Driver 18 for SQL Server'
# Campo de la conexión CustomKeys del proyecto que guarda la contraseña SQL (set-sql-secret.ps1).
$script:SqlSecretField = 'SQL_PASSWORD'
$script:ConnectionsApiVersion = '2025-04-01-preview'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    !!  $Message" -ForegroundColor Yellow }

function Resolve-ParametersFile([string]$ParametersFile) {
  if (-not $ParametersFile) { $ParametersFile = Join-Path $script:StarterRoot 'infra/parameters/pilot.bicepparam' }
  if (-not (Test-Path -LiteralPath $ParametersFile)) {
    throw "No existe $ParametersFile. Copie infra/parameters/pilot.example.bicepparam a infra/parameters/pilot.bicepparam y edítelo."
  }
  return (Resolve-Path -LiteralPath $ParametersFile).Path
}

function Get-BicepPath {
  if ($env:BICEP_PATH -and (Test-Path -LiteralPath $env:BICEP_PATH)) { return $env:BICEP_PATH }
  $command = Get-Command bicep -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  # .azure-local/bin: instalación local al proyecto (AZURE_CONFIG_DIR=.azure-local; az bicep install), ignorada por git.
  foreach ($candidate in @((Join-Path $script:StarterRoot '.azure-local/bin/bicep.exe'), (Join-Path $script:StarterRoot '.azure-local/bin/bicep'),
      (Join-Path $HOME '.azure/bin/bicep.exe'), (Join-Path $HOME '.azure/bin/bicep'))) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  throw 'No se encontró Bicep CLI. Instálelo en el proyecto: $env:AZURE_CONFIG_DIR = "$PWD\.azure-local"; az bicep install --version v0.48.1 (o defina BICEP_PATH).'
}

# Espera a que el agente local responda en /readiness. Si se indica el proceso y termina antes, falla.
function Wait-LocalAgent {
  param([Parameter(Mandatory = $true)][string]$Url, $Process = $null, [int]$TimeoutSeconds = 60)
  $readiness = $Url.TrimEnd('/') + '/readiness'
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($Process -and $Process.HasExited) { throw "El agente terminó antes de estar listo (código $($Process.ExitCode)). Revise la salida anterior." }
    try {
      $response = Invoke-WebRequest -Method Get -Uri $readiness -UseBasicParsing -TimeoutSec 3
      if ([int]$response.StatusCode -eq 200) { return }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  throw "El agente no respondió en $readiness tras $TimeoutSeconds s."
}

function Get-PythonPath {
  foreach ($candidate in @((Join-Path $script:StarterRoot '.venv/Scripts/python.exe'), (Join-Path $script:StarterRoot '.venv/bin/python'))) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  $command = Get-Command python -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  throw 'No se encontró Python. Cree el entorno con: python -m venv .venv'
}

# Ejecuta un comando nativo capturando stdout/stderr sin que stderr detenga el script
# (en Windows PowerShell 5.1, "2>" con ErrorActionPreference=Stop lanza excepciones).
function Invoke-Native {
  param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$Arguments = @())
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $output = & $FilePath @Arguments 2>&1
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
  $stdout = @($output | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ }) -join "`n"
  $stderr = @($output | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() }) -join "`n"
  return [pscustomobject]@{ ExitCode = $code; Out = $stdout; Err = $stderr }
}

# Igual, pero muestra la salida en vivo (docker build/push escriben el progreso en stderr). Devuelve el código.
function Invoke-NativeStreaming {
  param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$Arguments = @())
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $FilePath @Arguments 2>&1 | ForEach-Object {
      $line = "$_"  # las líneas vacías de stderr llegan como RemoteException sin texto
      if ($line -ne 'System.Management.Automation.RemoteException') { Write-Host "    $line" }
    }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Invoke-Az {
  param([Parameter(Mandatory = $true)][string[]]$Arguments, [string]$ErrorMessage = 'Falló Azure CLI.', [switch]$AllowFailure)
  $result = Invoke-Native -FilePath 'az' -Arguments $Arguments
  if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
    throw "$ErrorMessage`n$($result.Err)"
  }
  return $result
}

function Invoke-AzJson {
  param([Parameter(Mandatory = $true)][string[]]$Arguments, [string]$ErrorMessage = 'Falló Azure CLI.', [switch]$AllowFailure)
  $result = Invoke-Az -Arguments ($Arguments + @('--output', 'json', '--only-show-errors')) -ErrorMessage $ErrorMessage -AllowFailure:$AllowFailure
  if ($result.ExitCode -ne 0 -or -not $result.Out.Trim()) { return $null }
  return ($result.Out | ConvertFrom-Json)
}

# Lee una propiedad que puede no existir (Set-StrictMode lanza error al acceder a propiedades ausentes).
function Get-Prop($Object, [string]$Name) {
  if ($null -eq $Object) { return $null }
  $property = $Object.PSObject.Properties[$Name]
  if ($property) { return $property.Value }
  return $null
}

function Test-RealGuid([string]$Value) {
  return ($Value -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') -and ($Value -ne $script:ZeroGuid)
}

# Compila el .bicepparam y devuelve los valores como objeto plano.
function Read-StarterParameters([string]$ParametersFile) {
  $file = Resolve-ParametersFile $ParametersFile
  $result = Invoke-Native -FilePath (Get-BicepPath) -Arguments @('build-params', $file, '--stdout')
  if ($result.ExitCode -ne 0) { throw "Parámetros no válidos en ${file}:`n$($result.Err)" }
  $parameters = (($result.Out | ConvertFrom-Json).parametersJson | ConvertFrom-Json).parameters
  $values = [ordered]@{ ParametersFile = $file }
  foreach ($property in $parameters.PSObject.Properties) { $values[$property.Name] = $property.Value.value }
  $p = [pscustomobject]$values
  $problems = @()
  foreach ($key in @('subscriptionId', 'tenantId')) {
    if (-not (Test-RealGuid ([string]$p.$key))) { $problems += "$key debe ser un GUID real" }
  }
  if (Test-WebDeploy $p) {  # el registro Entra y los usuarios solo los usa la web (Easy Auth)
    if (-not (Test-RealGuid ([string]$p.authClientId))) { $problems += 'authClientId debe ser un GUID real (web.deploy=true)' }
    foreach ($id in @($p.allowedUserObjectIds)) {
      if (-not (Test-RealGuid ([string]$id))) { $problems += 'allowedUserObjectIds contiene un GUID no válido o en cero (web.deploy=true)' }
    }
  }
  $hosting = Get-AgentHosting $p
  if (@('code', 'container') -notcontains $hosting.mode) { $problems += "agentHosting.mode debe ser code o container" }
  if ($hosting.mode -eq 'container' -and ([string]$hosting.registryName -notmatch '^[a-zA-Z0-9]{5,50}$')) {
    $problems += 'agentHosting.registryName: 5-50 letras y números (único global)'
  }
  $publisher = ''
  if ($p.PSObject.Properties['publisherObjectId']) { $publisher = [string]$p.publisherObjectId }
  if ($publisher -and -not (Test-RealGuid $publisher)) { $problems += 'publisherObjectId debe ser un GUID real o quedar vacío' }
  if ($p.prefix -notmatch '^[a-z][a-z0-9]{2,11}$') { $problems += 'prefix: 3-12 caracteres, minúsculas y números, empezando por letra' }
  $files = Get-FilesSettings $p
  if ($files.enabled -and ([string]$files.storageAccountName -notmatch '^[a-z0-9]{3,24}$')) {
    $problems += 'files.storageAccountName: 3-24 minúsculas y números'
  }
  if ($problems.Count -gt 0) { throw "Edite ${file}:`n - " + ($problems -join "`n - ") }
  return $p
}

function Get-FilesSettings($p) {
  if ($p.PSObject.Properties['files'] -and $p.files) { return $p.files }
  return [pscustomobject]@{ enabled = $false }
}

# model.deploy (por defecto true): false = Bicep no crea el despliegue y se usa un modelo externo de agent.env.
function Test-ModelDeploy($p) {
  $model = Get-Prop $p 'model'
  if ($model -and $model.PSObject.Properties['deploy']) { return [bool]$model.deploy }
  return $true
}

# web.deploy (por defecto true): false = sin App Service; el agente se consume solo por API.
function Test-WebDeploy($p) {
  $web = Get-Prop $p 'web'
  if ($web -and $web.PSObject.Properties['deploy']) { return [bool]$web.deploy }
  return $true
}

# agentHosting.mode: code (remote_build, sin ODBC) o container (imagen con ODBC Driver 18 en ACR).
function Get-AgentHosting($p) {
  $hosting = Get-Prop $p 'agentHosting'
  $mode = [string](Get-Prop $hosting 'mode')
  if (-not $mode) { $mode = 'code' }
  return [pscustomobject]@{ mode = $mode; registryName = [string](Get-Prop $hosting 'registryName') }
}

function Assert-WebDeployed($p) {
  if (-not (Test-WebDeploy $p)) {
    throw 'web.deploy=false en los parámetros: la web no forma parte de este despliegue. El agente se consume por API (ver README.md).'
  }
}

function Get-ProjectConnectionUrl($p, $outputs, [string]$Name) {
  return "https://management.azure.com/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName)/providers/" +
    "Microsoft.CognitiveServices/accounts/$($outputs.foundryAccountName)/projects/$($outputs.projectName)/connections/$Name" +
    "?api-version=$($script:ConnectionsApiVersion)"
}

# Conexión del proyecto (sin credenciales: la API nunca las devuelve). $null si no existe.
function Get-ProjectConnection($p, $outputs, [string]$Name) {
  return Invoke-AzJson -Arguments @('rest', '--method', 'GET', '--url', (Get-ProjectConnectionUrl $p $outputs $Name)) -AllowFailure
}

function Get-DeploymentName($p) { return "foundry-starter-$($p.prefix)" }

function Assert-AzureContext($p) {
  Write-Step 'Comprobando la sesión de Azure CLI'
  $account = Invoke-AzJson -Arguments @('account', 'show') -ErrorMessage 'No hay sesión de Azure CLI. Ejecute: az login --tenant <tenantId>'
  if (-not $account) { throw 'No hay sesión de Azure CLI. Ejecute: az login --tenant <tenantId>' }
  if ($account.tenantId -ne $p.tenantId -or $account.id -ne $p.subscriptionId) {
    throw ("La sesión apunta a la suscripción $($account.id) del tenant $($account.tenantId), pero los parámetros indican " +
      "$($p.subscriptionId) / $($p.tenantId). Ejecute: az login --tenant $($p.tenantId); az account set --subscription $($p.subscriptionId)")
  }
  Write-Ok "Suscripción '$($account.name)' ($($account.id)), tenant $($account.tenantId), usuario $($account.user.name)"
  return $account
}

# Salidas del despliegue de infraestructura (fuente de nombres y URLs).
function Get-StarterOutputs($p) {
  $name = Get-DeploymentName $p
  $deployment = Invoke-AzJson -Arguments @('deployment', 'sub', 'show', '--name', $name) -AllowFailure
  if (-not $deployment) { throw "No existe el despliegue '$name'. Ejecute antes scripts/deploy-infra.ps1." }
  if ($deployment.properties.provisioningState -ne 'Succeeded') {
    throw "El despliegue '$name' está en estado $($deployment.properties.provisioningState). Revise: az deployment sub show --name $name"
  }
  $values = [ordered]@{}
  foreach ($property in $deployment.properties.outputs.PSObject.Properties) { $values[$property.Name] = $property.Value.value }
  $outputs = [pscustomobject]$values
  if ($outputs.declaredSubscriptionId -ne $p.subscriptionId -or $outputs.declaredTenantId -ne $p.tenantId) {
    throw 'Las salidas del despliegue no corresponden a la suscripción y tenant de los parámetros.'
  }
  return $outputs
}

function Get-AccountScope($p, $outputs) {
  return "/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName)/providers/Microsoft.CognitiveServices/accounts/$($outputs.foundryAccountName)"
}

function Get-ContainerScope($p, $outputs, [string]$Container) {
  return "/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName)/providers/Microsoft.Storage/storageAccounts/$($outputs.storageAccountName)/blobServices/default/containers/$Container"
}

# Asigna un rol si no existe. Seguro al repetirse.
function Set-RoleAssignment {
  param([string]$PrincipalId, [string]$PrincipalType = 'ServicePrincipal', [string]$RoleId, [string]$Scope, [string]$Label, [switch]$CheckOnly)
  $existing = Invoke-AzJson -Arguments @('role', 'assignment', 'list', '--assignee', $PrincipalId, '--scope', $Scope,
    '--role', $RoleId, '--fill-principal-name', 'false') -ErrorMessage "No se pudieron leer asignaciones para $Label."
  $match = @($existing | Where-Object { $_.scope -eq $Scope -or $_.scope -eq $Scope.ToLowerInvariant() })
  if ($match.Count -gt 0) { Write-Ok "$Label ya asignado"; return $true }
  if ($CheckOnly) { Write-Warn "$Label falta"; return $false }
  Invoke-Az -Arguments @('role', 'assignment', 'create', '--assignee-object-id', $PrincipalId,
    '--assignee-principal-type', $PrincipalType, '--role', $RoleId, '--scope', $Scope, '--output', 'none') `
    -ErrorMessage "No se pudo asignar $Label. El operador necesita Microsoft.Authorization/roleAssignments/write (Owner, User Access Administrator o RBAC Administrator) en $Scope." | Out-Null
  Write-Ok "$Label asignado"
  return $true
}

function Get-FoundryToken {
  $result = Invoke-Az -Arguments @('account', 'get-access-token', '--resource', 'https://ai.azure.com', '--query', 'accessToken', '--output', 'tsv') `
    -ErrorMessage 'No se pudo obtener un token para Foundry (https://ai.azure.com).'
  return $result.Out.Trim()
}

# GET de un recurso del plano de datos de Foundry. Devuelve @{ Status; Body }.
function Invoke-FoundryGet([string]$Url) {
  $headers = @{ Authorization = "Bearer $(Get-FoundryToken)" }
  try {
    $response = Invoke-WebRequest -Method Get -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec 60
    return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = ($response.Content | ConvertFrom-Json) }
  } catch {
    $status = 0
    if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    return [pscustomobject]@{ Status = $status; Body = $null }
  }
}

function Get-HostedAgent($outputs) {
  $url = "$($outputs.projectEndpoint)/agents/$($outputs.agentName)?api-version=v1"
  $response = Invoke-FoundryGet $url
  if ($response.Status -eq 404) { return $null }
  if ($response.Status -ne 200) {
    throw "No se pudo consultar el agente (HTTP $($response.Status)). El operador necesita Foundry Project Manager o Foundry User en el proyecto."
  }
  return $response.Body
}

function Get-AgentIdentity($agent) {
  if (-not $agent -or -not $agent.PSObject.Properties['instance_identity'] -or -not $agent.instance_identity) { return $null }
  return $agent.instance_identity
}

function Get-AgentLatestVersion($agent) {
  if ($agent -and $agent.PSObject.Properties['versions'] -and $agent.versions -and
      $agent.versions.PSObject.Properties['latest'] -and $agent.versions.latest) {
    return $agent.versions.latest
  }
  return $null
}

function Get-IdentityRecordPath($p) {
  $folder = Join-Path $script:StarterRoot '.build'
  if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
  return Join-Path $folder "agent-identity-$($p.prefix).json"
}
