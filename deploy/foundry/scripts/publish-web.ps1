<#
.SYNOPSIS
  Publica la interfaz Streamlit en App Service Linux con compilación Oryx (ZIP de código fuente).
.DESCRIPTION
  Distingue tres hitos que no son equivalentes:
    1. ZIP recibido      -> az webapp deploy aceptó el paquete.
    2. Compilación lista -> el despliegue nuevo termina con estado 4 y el registro muestra Oryx.
    3. App disponible    -> Streamlit arranca; lo comprueba check-availability.ps1.
  Preparación (idempotente):
    - Elimina WEBSITE_RUN_FROM_PACKAGE (montaría el ZIP de solo lectura y omitiría Oryx).
    - SCM_DO_BUILD_DURING_DEPLOYMENT=true (Oryx crea el entorno e instala requirements.txt).
    - Comando de inicio con $APP_PATH (Oryx ejecuta desde /tmp/<id>, no desde wwwroot).
.EXAMPLE
  ./scripts/publish-web.ps1
#>
param([string]$ParametersFile, [int]$BuildTimeoutSeconds = 900)
. (Join-Path $PSScriptRoot 'common.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-WebDeployed $p
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p
$rg = $outputs.resourceGroupName
$app = $outputs.webAppName
$startup = 'python -m streamlit run "${APP_PATH:-/home/site/wwwroot}/web/streamlit_app.py" --server.address 0.0.0.0 --server.port 8000 --server.headless true --server.enableCORS true --server.enableXsrfProtection true --browser.gatherUsageStats false'

function Get-LatestDeployment {
  $list = Invoke-AzJson -Arguments @('webapp', 'log', 'deployment', 'list', '--resource-group', $rg, '--name', $app) -AllowFailure
  return @($list) | Where-Object { $_ } | Select-Object -First 1
}

Write-Step "App Service $app"
$latest = Get-LatestDeployment
if ($latest -and [int](Get-Prop $latest 'status') -in @(0, 1, 2)) {
  throw "El despliegue $($latest.id) sigue en curso (estado $($latest.status)). Espere a que termine; dos ZIP simultáneos dejan el sitio inconsistente."
}
$state = (Invoke-Az -Arguments @('webapp', 'show', '--resource-group', $rg, '--name', $app, '--query', 'state', '--output', 'tsv')).Out.Trim()
if ($state -eq 'QuotaExceeded') { throw 'App Service está en QuotaExceeded. Revise el SKU del plan (F1 tiene cuotas diarias de CPU).' }
if ($state -ne 'Running') {
  Write-Warn "Estado $state; iniciando"
  Invoke-Az -Arguments @('webapp', 'start', '--resource-group', $rg, '--name', $app) -ErrorMessage 'No se pudo iniciar App Service.' | Out-Null
}

Write-Step 'Configuración compatible con ZIP + Oryx'
$settings = Invoke-AzJson -Arguments @('webapp', 'config', 'appsettings', 'list', '--resource-group', $rg, '--name', $app)
$changed = $false
if (@($settings | Where-Object { $_.name -eq 'WEBSITE_RUN_FROM_PACKAGE' }).Count -gt 0) {
  Invoke-Az -Arguments @('webapp', 'config', 'appsettings', 'delete', '--resource-group', $rg, '--name', $app,
    '--setting-names', 'WEBSITE_RUN_FROM_PACKAGE', '--output', 'none') -ErrorMessage 'No se pudo eliminar WEBSITE_RUN_FROM_PACKAGE.' | Out-Null
  Write-Ok 'WEBSITE_RUN_FROM_PACKAGE eliminado'
  $changed = $true
}
$build = @($settings | Where-Object { $_.name -eq 'SCM_DO_BUILD_DURING_DEPLOYMENT' }) | Select-Object -First 1
if (-not $build -or $build.value -ne 'true') {
  Invoke-Az -Arguments @('webapp', 'config', 'appsettings', 'set', '--resource-group', $rg, '--name', $app,
    '--settings', 'SCM_DO_BUILD_DURING_DEPLOYMENT=true', '--output', 'none') -ErrorMessage 'No se pudo activar Oryx.' | Out-Null
  $changed = $true
}
Write-Ok 'SCM_DO_BUILD_DURING_DEPLOYMENT=true'
$currentStartup = (Invoke-Az -Arguments @('webapp', 'config', 'show', '--resource-group', $rg, '--name', $app, '--query', 'appCommandLine', '--output', 'tsv')).Out.Trim()
if ($currentStartup -ne $startup) {
  Invoke-Az -Arguments @('webapp', 'config', 'set', '--resource-group', $rg, '--name', $app, '--startup-file', $startup, '--output', 'none') `
    -ErrorMessage 'No se pudo fijar el comando de inicio.' | Out-Null
  $changed = $true
}
Write-Ok 'Comando de inicio con $APP_PATH'
$remaining = Invoke-AzJson -Arguments @('webapp', 'config', 'appsettings', 'list', '--resource-group', $rg, '--name', $app)
if (@($remaining | Where-Object { $_.name -eq 'WEBSITE_RUN_FROM_PACKAGE' }).Count -gt 0) { throw 'WEBSITE_RUN_FROM_PACKAGE sigue activo; Azure omitiría Oryx.' }
if ($changed) {
  Write-Host '    Esperando 20 s a que App Service aplique la configuración (reinicio del sitio)...'
  Start-Sleep -Seconds 20
}

Write-Step 'Paquete'
$zip = Join-Path $script:StarterRoot ".build/web-$([guid]::NewGuid().ToString('N')).zip"
& (Get-PythonPath) (Join-Path $script:StarterRoot 'scripts/package.py') web --out $zip
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el ZIP de la web.' }
try {
  $previousId = Get-Prop (Get-LatestDeployment) 'id'
  Write-Step 'Enviando ZIP (asíncrono)'
  & az webapp deploy --resource-group $rg --name $app --src-path $zip --type zip --async true --output none
  if ($LASTEXITCODE -ne 0) { throw 'App Service rechazó el ZIP.' }
  Write-Ok '1/3 ZIP recibido'

  Write-Step 'Esperando la compilación Oryx'
  $deadline = (Get-Date).AddSeconds($BuildTimeoutSeconds)
  $deployment = $null
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    $deployment = Get-LatestDeployment
    if (-not $deployment -or (Get-Prop $deployment 'id') -eq $previousId) { Write-Host '    esperando el registro del despliegue nuevo...'; continue }
    $status = [int](Get-Prop $deployment 'status')
    if ($status -eq 4) { break }
    if ($status -eq 3) {
      & az webapp log deployment show --resource-group $rg --name $app --deployment-id $deployment.id --output table
      throw 'La compilación Oryx falló; revise el registro anterior (requirements.txt, versión de Python).'
    }
    Write-Host "    estado $status (0 pendiente, 1 compilando, 2 desplegando)"
  }
  if (-not $deployment -or [int](Get-Prop $deployment 'status') -ne 4) {
    throw "Tiempo agotado. El despliegue sigue en Azure: az webapp log deployment show -g $rg -n $app"
  }
  $log = Invoke-AzJson -Arguments @('webapp', 'log', 'deployment', 'show', '--resource-group', $rg, '--name', $app, '--deployment-id', $deployment.id)
  if (-not (@($log) | Where-Object { [string](Get-Prop $_ 'message') -like '*oryx*' })) {
    throw 'El despliegue terminó, pero el registro no confirma Oryx. Revise WEBSITE_RUN_FROM_PACKAGE y SCM_DO_BUILD_DURING_DEPLOYMENT.'
  }
  Write-Ok "2/3 Compilación Oryx terminada ($($deployment.id))"
} finally {
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
}
Write-Host ''
Write-Host '3/3 La disponibilidad no está confirmada todavía: Streamlit tarda ~1 min en arrancar.' -ForegroundColor Yellow
Write-Host "Ejecute: ./scripts/check-availability.ps1   ·   Web: $($outputs.webUrl)" -ForegroundColor Green
