<#
.SYNOPSIS
  Vista previa de infraestructura: valida destino, proveedores, modelo y cuota, y ejecuta what-if.
  No crea ni modifica recursos.
.EXAMPLE
  ./scripts/preview-infra.ps1 -ParametersFile ./infra/parameters/pilot.bicepparam
#>
param([string]$ParametersFile)
. (Join-Path $PSScriptRoot 'infra-checks.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-AzureContext $p | Out-Null
Test-InfraPrerequisites $p

Write-Step 'what-if (sin cambios)'
& az deployment sub what-if --name (Get-DeploymentName $p) --location $p.location `
  --template-file (Join-Path $script:StarterRoot 'infra/main.bicep') --parameters $p.ParametersFile
if ($LASTEXITCODE -ne 0) { throw 'Falló what-if. Revise permisos, cuotas, SKU y disponibilidad regional.' }
Write-Host ''
Write-Host "Revise los cambios. Para aplicarlos: ./scripts/deploy-infra.ps1 -ParametersFile '$($p.ParametersFile)'" -ForegroundColor Green
if (Test-WebDeploy $p) { Write-Host "Redirect URI que Easy Auth usará: https://$($p.prefix)-web.azurewebsites.net/.auth/login/aad/callback" }
