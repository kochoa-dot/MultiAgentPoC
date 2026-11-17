<#
.SYNOPSIS
  Validación local sin Azure: pruebas, paquetes, Bicep y sintaxis de scripts.
.EXAMPLE
  ./scripts/validate-local.ps1
#>
param([switch]$SkipTests)
. (Join-Path $PSScriptRoot 'common.ps1')

$root = $script:StarterRoot
$python = Get-PythonPath
$failures = @()

Write-Step "Python: $python"
if (-not $SkipTests) {
  Write-Step 'Pruebas locales (sin credenciales cloud)'
  Push-Location $root
  try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { $failures += 'pytest' } else { Write-Ok 'pytest' }
  } finally { Pop-Location }
}

Write-Step 'Paquetes publicables'
& $python (Join-Path $root 'scripts/package.py') verify
if ($LASTEXITCODE -ne 0) { $failures += 'package.py verify' } else { Write-Ok 'agente y web autónomos' }

Write-Step 'Bicep'
$bicep = Get-BicepPath
$out = Join-Path $root '.build/bicep'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$build = Invoke-Native -FilePath $bicep -Arguments @('build', (Join-Path $root 'infra/main.bicep'), '--outdir', $out)
if ($build.ExitCode -ne 0) { $failures += 'bicep build'; Write-Warn $build.Err } else { Write-Ok 'infra/main.bicep' }
if ($build.Err -match 'Warning') { Write-Warn $build.Err }
foreach ($param in Get-ChildItem -LiteralPath (Join-Path $root 'infra/parameters') -Filter '*.bicepparam') {
  $result = Invoke-Native -FilePath $bicep -Arguments @('build-params', $param.FullName, '--outfile', (Join-Path $out ($param.BaseName + '.parameters.json')))
  if ($result.ExitCode -ne 0) { $failures += "bicep build-params $($param.Name)"; Write-Warn $result.Err } else { Write-Ok $param.Name }
}

Write-Step 'Sintaxis PowerShell'
foreach ($script in Get-ChildItem -LiteralPath (Join-Path $root 'scripts') -Filter '*.ps1') {
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$errors) | Out-Null
  if ($errors -and $errors.Count -gt 0) {
    $failures += $script.Name
    foreach ($item in $errors) { Write-Warn "$($script.Name):$($item.Extent.StartLineNumber) $($item.Message)" }
  } else { Write-Ok $script.Name }
}

if ($failures.Count -gt 0) { throw "Validación local fallida: $($failures -join ', ')" }
Write-Host ''
Write-Host 'Validación local completada. Las comprobaciones en Azure se ejecutan con preview-infra.ps1 y check-availability.ps1.' -ForegroundColor Green
