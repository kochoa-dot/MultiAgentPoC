<#
.SYNOPSIS
  Asigna o comprueba los permisos mínimos de la identidad del Hosted Agent (idempotente).
.DESCRIPTION
  La identidad del agente la crea Foundry al publicar; no existe cuando corre Bicep.
  Permisos:
    - Cognitive Services OpenAI User en la cuenta Foundry (inferencia del modelo), salvo model.deploy=false.
    - Cognitive Services OpenAI User en el recurso del modelo externo (-ModelResourceId; publish-agent.ps1
      lo pasa desde MODEL_RESOURCE_ID de agent.env). El operador necesita roleAssignments/write en ese recurso.
    - AcrPull en el registro de contenedores (agentHosting.mode=container; azd también lo asigna al publicar).
    - Storage Blob Data Reader en el contenedor de entradas (si hay archivos).
    - Storage Blob Data Contributor en el contenedor de salidas (si hay archivos).
  Si la identidad cambió respecto a la última ejecución, informa de las asignaciones de la
  identidad anterior y las elimina con -RemoveStale.
  -WebConsumerAtAgentScope añade Foundry Agent Consumer para la web en el ámbito del agente
  (endurecimiento opcional; Bicep ya lo concede en el proyecto).
.EXAMPLE
  ./scripts/grant-agent-permissions.ps1 -CheckOnly
#>
param([string]$ParametersFile, [string]$ModelResourceId, [switch]$CheckOnly, [switch]$RemoveStale, [switch]$WebConsumerAtAgentScope)
. (Join-Path $PSScriptRoot 'common.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p

Write-Step "Identidad del agente $($outputs.agentName)"
$agent = Get-HostedAgent $outputs
if (-not $agent) { throw 'El agente no está publicado. Ejecute publish-agent.ps1.' }
$identity = Get-AgentIdentity $agent
if (-not $identity -or -not (Get-Prop $identity 'principal_id')) { throw 'El agente todavía no expone instance_identity.principal_id.' }
$principal = $identity.principal_id
Write-Ok "principal_id $principal (client_id $(Get-Prop $identity 'client_id'))"

$grants = @()
if (Test-ModelDeploy $p) {
  $grants += @{ Role = $script:Roles.CognitiveServicesOpenAIUser; Scope = (Get-AccountScope $p $outputs); Label = 'Cognitive Services OpenAI User (modelo)' }
}
if ($ModelResourceId) {
  if ($ModelResourceId -notmatch '^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+$') {
    throw '-ModelResourceId debe ser /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<cuenta>.'
  }
  $grants += @{ Role = $script:Roles.CognitiveServicesOpenAIUser; Scope = $ModelResourceId; Label = 'Cognitive Services OpenAI User (modelo externo)' }
} elseif (-not (Test-ModelDeploy $p)) {
  Write-Warn 'model.deploy=false sin -ModelResourceId: el permiso sobre el modelo externo no se comprueba ni se asigna.'
}
if ((Get-AgentHosting $p).mode -eq 'container') {
  # azd lo asigna al publicar (Learn, ACR privado); se comprueba aquí para detectar si falló o cambió la identidad.
  $grants += @{ Role = $script:Roles.AcrPull; Scope = $outputs.containerRegistryResourceId; Label = "AcrPull en $($outputs.containerRegistryName)" }
}
if ($outputs.filesEnabled) {
  $grants += @{ Role = $script:Roles.BlobDataReader; Scope = (Get-ContainerScope $p $outputs $outputs.inputContainer); Label = "Blob Data Reader en $($outputs.inputContainer)" }
  $grants += @{ Role = $script:Roles.BlobDataContributor; Scope = (Get-ContainerScope $p $outputs $outputs.outputContainer); Label = "Blob Data Contributor en $($outputs.outputContainer)" }
}

$missing = 0
foreach ($grant in $grants) {
  if (-not (Set-RoleAssignment -PrincipalId $principal -RoleId $grant.Role -Scope $grant.Scope -Label $grant.Label -CheckOnly:$CheckOnly)) { $missing++ }
}
if ($WebConsumerAtAgentScope -and -not (Test-WebDeploy $p)) { Write-Warn 'web.deploy=false: -WebConsumerAtAgentScope no aplica.' }
elseif ($WebConsumerAtAgentScope) {
  $agentScope = "/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName)/providers/Microsoft.CognitiveServices/accounts/$($outputs.foundryAccountName)/projects/$($outputs.projectName)/agents/$($outputs.agentName)"
  if (-not (Set-RoleAssignment -PrincipalId $outputs.webIdentityPrincipalId -RoleId $script:Roles.FoundryAgentConsumer -Scope $agentScope -Label 'Foundry Agent Consumer (web, ámbito del agente)' -CheckOnly:$CheckOnly)) { $missing++ }
}

$record = Get-IdentityRecordPath $p
if (Test-Path -LiteralPath $record) {
  $previous = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
  if ($previous.principalId -and $previous.principalId -ne $principal) {
    Write-Warn "Identidad anterior: $($previous.principalId). Sus asignaciones ya no son necesarias:"
    foreach ($grant in $grants) {
      $stale = Invoke-AzJson -Arguments @('role', 'assignment', 'list', '--assignee', $previous.principalId, '--scope', $grant.Scope, '--role', $grant.Role, '--fill-principal-name', 'false') -AllowFailure
      foreach ($item in @($stale)) {
        if (-not $item) { continue }
        if ($RemoveStale -and -not $CheckOnly) {
          Invoke-Az -Arguments @('role', 'assignment', 'delete', '--ids', $item.id) -ErrorMessage "No se pudo eliminar $($item.id)" | Out-Null
          Write-Ok "Eliminada: $($grant.Label) de la identidad anterior"
        } else { Write-Warn "  $($grant.Label): $($item.id)  (use -RemoveStale para eliminarla)" }
      }
    }
  }
}
if (-not $CheckOnly) {
  @{ principalId = $principal; clientId = (Get-Prop $identity 'client_id'); recordedAt = (Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $record -Encoding UTF8
}

if ($missing -gt 0) { throw "$missing permiso(s) del agente sin asignar." }
Write-Host ''
Write-Host 'Permisos del agente correctos. Azure RBAC puede tardar unos minutos en propagarse.' -ForegroundColor Green
