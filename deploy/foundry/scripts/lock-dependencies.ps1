<#
.SYNOPSIS
  Regenera agent/requirements.lock.txt y web/requirements.lock.txt para Linux (destino real).
.DESCRIPTION
  Resuelve las dependencias de requirements.txt para Python 3.13 en x86_64-manylinux_2_28
  con uv, de modo que el lock no arrastre paquetes exclusivos de Windows. Los paquetes
  publicados usan el lock; si se borra, se usa requirements.txt (rangos compatibles).
.EXAMPLE
  ./scripts/lock-dependencies.ps1
#>
param([string]$PythonVersion = '3.13')
. (Join-Path $PSScriptRoot 'common.ps1')
$uv = Join-Path $script:StarterRoot '.venv/Scripts/uv.exe'
if (-not (Test-Path -LiteralPath $uv)) { $uv = Join-Path $script:StarterRoot '.venv/bin/uv' }
if (-not (Test-Path -LiteralPath $uv)) { throw 'Instale las dependencias de desarrollo: python -m pip install -r requirements-dev.txt' }
foreach ($component in @('agent', 'web')) {
  $folder = Join-Path $script:StarterRoot $component
  Write-Step "Lock de $component"
  & $uv pip compile (Join-Path $folder 'requirements.txt') --python-version $PythonVersion `
    --python-platform x86_64-manylinux_2_28 --no-header --quiet --custom-compile-command 'scripts/lock-dependencies.ps1' `
    -o (Join-Path $folder 'requirements.lock.txt')
  if ($LASTEXITCODE -ne 0) { throw "No se pudo resolver $component/requirements.txt" }
  Write-Ok "$component/requirements.lock.txt"
}
Write-Host 'Ejecute ./scripts/validate-local.ps1 antes de publicar.' -ForegroundColor Green
