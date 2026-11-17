<#
.SYNOPSIS
  Guarda la contraseña SQL del agente en una conexión CustomKeys del proyecto Foundry (idempotente).
.DESCRIPTION
  El agente conserva la autenticación SQL existente (SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD).
  La contraseña no se escribe en agent.env, azure.yaml, el ambiente azd ni la imagen: Foundry la guarda
  en Key Vault (administrado por la plataforma) y la inyecta al iniciar cada sesión del agente mediante
  ${{connections.<nombre>.credentials.SQL_PASSWORD}} (Learn, «Deploy a hosted agent», Reference project
  connections in environment variables).
  - Crea o reemplaza la conexión con PUT de ARM (category y authType CustomKeys). Requiere
    connections/write en el proyecto (Foundry Project Manager, que Bicep concede a publisherObjectId).
  - La contraseña se pide oculta (Read-Host -AsSecureString) o, con -FromDotEnv, se toma de SQL_PASSWORD
    del .env local. Nunca se muestra ni se pasa por la línea de comandos: viaja en un archivo temporal
    que se borra al terminar.
  - La API no devuelve credenciales (GET responde credentials: null). -CheckOnly solo comprueba que la
    conexión existe.
  Debe existir antes de publicar la versión del agente: si falta al iniciar la sesión, SQL_PASSWORD queda vacía.
  Tras cambiar la contraseña, publique una versión nueva (las sesiones existentes conservan el valor anterior).
.EXAMPLE
  ./scripts/set-sql-secret.ps1                     # pide la contraseña
  ./scripts/set-sql-secret.ps1 -FromDotEnv         # usa SQL_PASSWORD de .env
  ./scripts/set-sql-secret.ps1 -CheckOnly
#>
param(
  [string]$ParametersFile,
  [string]$ConnectionName = 'sql-credentials',
  [switch]$FromDotEnv,
  [switch]$CheckOnly
)
. (Join-Path $PSScriptRoot 'common.ps1')

if ($ConnectionName -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{2,32}$') { throw 'ConnectionName: 3-33 letras, números, _ o -.' }
$p = Read-StarterParameters $ParametersFile
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p
$url = Get-ProjectConnectionUrl $p $outputs $ConnectionName

Write-Step "Conexión $ConnectionName del proyecto $($outputs.projectName)"
$existing = Get-ProjectConnection $p $outputs $ConnectionName
if ($CheckOnly) {
  if (-not $existing) { throw "No existe la conexión $ConnectionName. Ejecute: ./scripts/set-sql-secret.ps1" }
  $properties = Get-Prop $existing 'properties'
  if ((Get-Prop $properties 'authType') -ne 'CustomKeys') { throw "La conexión $ConnectionName no es CustomKeys." }
  Write-Ok "Existe (CustomKeys). La API no devuelve el secreto: compruébelo con la invocación de prueba."
  return
}

function Get-DotEnvPassword {
  $path = Join-Path $script:StarterRoot '.env'
  if (-not (Test-Path -LiteralPath $path)) { throw 'No existe .env.' }
  foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
    $text = $line.Trim()
    if ($text -notmatch '^SQL_PASSWORD\s*=') { continue }
    $value = $text.Substring($text.IndexOf('=') + 1).Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    if ($value) { return $value }
  }
  throw 'SQL_PASSWORD no está definida en .env.'
}

if ($FromDotEnv) {
  $plain = Get-DotEnvPassword
  Write-Ok 'Contraseña tomada de SQL_PASSWORD en .env (no se muestra)'
} else {
  $secure = Read-Host -AsSecureString "Contraseña del usuario SQL del agente"
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
  if (-not $plain) { throw 'Contraseña vacía.' }
}

$keys = @{}
$keys[$script:SqlSecretField] = $plain
$body = @{ properties = @{ category = 'CustomKeys'; authType = 'CustomKeys'; credentials = @{ keys = $keys } } } |
  ConvertTo-Json -Depth 5 -Compress
$folder = Join-Path ([System.IO.Path]::GetTempPath()) "t2s-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $folder | Out-Null
$file = Join-Path $folder 'body.json'
try {
  [System.IO.File]::WriteAllText($file, $body, [System.Text.UTF8Encoding]::new($false))
  $result = Invoke-Native -FilePath 'az' -Arguments @('rest', '--method', 'PUT', '--url', $url, '--headers', 'Content-Type=application/json',
    '--body', "@$file", '--output', 'none', '--only-show-errors')
  if ($result.ExitCode -ne 0) {
    throw "No se pudo guardar la conexión. El operador necesita Foundry Project Manager (connections/write) en el proyecto.`n$($result.Err)"
  }
} finally {
  Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
  $plain = $null; $body = $null; $keys = $null
}
$verb = 'creada'
if ($existing) { $verb = 'actualizada' }
Write-Ok "Conexión $ConnectionName $verb (campo $($script:SqlSecretField))"
Write-Host ''
Write-Host "Use SQL_PASSWORD_CONNECTION=$ConnectionName en agent.env y publique con ./scripts/publish-agent.ps1" -ForegroundColor Green
