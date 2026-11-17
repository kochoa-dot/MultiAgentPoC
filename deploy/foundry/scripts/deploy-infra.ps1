<#
.SYNOPSIS
  Aplica la infraestructura del piloto (idempotente). Ejecute antes preview-infra.ps1.
.DESCRIPTION
  Usa un nombre de despliegue fijo (foundry-starter-<prefix>) para que los scripts
  posteriores lean sus salidas. Repetirlo converge al mismo estado.
.EXAMPLE
  ./scripts/deploy-infra.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam
#>
param([string]$ParametersFile)
. (Join-Path $PSScriptRoot 'infra-checks.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-AzureContext $p | Out-Null
Test-InfraPrerequisites $p

$name = Get-DeploymentName $p
Write-Step "Aplicando despliegue $name en $($p.location)"
& az deployment sub create --name $name --location $p.location `
  --template-file (Join-Path $script:StarterRoot 'infra/main.bicep') --parameters $p.ParametersFile --output none
if ($LASTEXITCODE -ne 0) {
  throw "Falló el despliegue. Operaciones: az deployment operation sub list --name $name --query `"[?properties.provisioningState=='Failed']`""
}

$outputs = Get-StarterOutputs $p
Write-Ok 'Infraestructura aplicada'
Write-Host ''
Write-Host "Grupo de recursos : $($outputs.resourceGroupName)"
Write-Host "Proyecto Foundry  : $($outputs.projectEndpoint)"
if (Test-ModelDeploy $p) { Write-Host "Modelo            : $($outputs.modelDeploymentName) en $($outputs.modelEndpoint)" }
else { Write-Host 'Modelo            : existente (MODEL_* de agent.env); Bicep no crea despliegue' }
Write-Host "Agente (nombre)   : $($outputs.agentName)  (se publica con publish-agent.ps1, modo $($outputs.agentHostingMode))"
if ($outputs.containerRegistryLoginServer) { Write-Host "Registro          : $($outputs.containerRegistryLoginServer)" }
if (Test-WebDeploy $p) {
  Write-Host "Web               : $($outputs.webUrl)"
  Write-Host "Redirect URI      : $($outputs.webRedirectUri)"
}
if ($outputs.filesEnabled) { Write-Host "Blob              : $($outputs.storageAccountUrl) [$($outputs.inputContainer), $($outputs.outputContainer)]" }
Write-Host ''
Write-Host 'Siguiente paso (agente): ./scripts/set-sql-secret.ps1 si ejecuta SQL; después ./scripts/publish-agent.ps1' -ForegroundColor Green
if (Test-WebDeploy $p) { Write-Host 'Web (aparte): ./scripts/configure-auth.ps1 y ./scripts/publish-web.ps1' }
