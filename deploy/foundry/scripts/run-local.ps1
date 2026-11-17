<#
.SYNOPSIS
  Ejecuta el agente en local (con o sin Streamlit). Por defecto, sin Azure, sin modelo y sin base de datos.
.DESCRIPTION
  -Mode inprocess   : la web importa agent/ y lo ejecuta en el mismo proceso (siempre STARTER_RESPONDER=echo).
  -Mode local-http  : arranca además agent/main.py (protocolo invocations real) en el puerto 8088.
  -Mode agent       : solo agent/main.py en primer plano, sin Streamlit (invóquelo con scripts/invoke-local.ps1).
  -Responder echo   : generador simulado con los ejemplos de text2sql_core (por defecto).
  -Responder model  : modelo real con MODEL_ENDPOINT/MODEL_DEPLOYMENT_NAME y su sesión de az login (local-http o agent).
  -Sql simulated    : genera, valida y ejecuta contra una base simulada (por defecto).
  -Sql none         : genera y valida, sin ejecutar (TEXT2SQL_EXECUTE=false).
  -Sql odbc         : ejecuta contra la base de .env (SQL_CONNECTION_STRING o SQL_SERVER/...). Solo con autorización.
  -Check            : solo valida la configuración de ese modo (sin valores, sin red) y termina.
  Precedencia: variables de la terminal > .env > valores por defecto. .env se carga sin sobrescribir la terminal.
  Los parámetros -Responder y -Sql fijan STARTER_RESPONDER y TEXT2SQL_* para esta ejecución.
  Los archivos generados quedan en .local-files/outputs.
.EXAMPLE
  ./scripts/run-local.ps1
  ./scripts/run-local.ps1 -Check
  ./scripts/run-local.ps1 -Mode agent
  ./scripts/run-local.ps1 -Mode local-http -Responder model -Sql none
#>
param(
  [ValidateSet('inprocess', 'local-http', 'agent')][string]$Mode = 'inprocess',
  [ValidateSet('echo', 'model')][string]$Responder = 'echo',
  [ValidateSet('simulated', 'none', 'odbc')][string]$Sql = 'simulated',
  [int]$WebPort = 8501,
  [int]$AgentPort = 8088,
  [switch]$Check
)
. (Join-Path $PSScriptRoot 'common.ps1')
$python = Get-PythonPath
$root = $script:StarterRoot
if ($Responder -eq 'model' -and $Mode -eq 'inprocess') { throw '-Responder model requiere -Mode local-http o -Mode agent (inprocess usa siempre el generador simulado).' }

$dotenv = Join-Path $root '.env'
if (Test-Path -LiteralPath $dotenv) {
  foreach ($line in Get-Content -LiteralPath $dotenv -Encoding UTF8) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith('#') -or $text.IndexOf('=') -lt 1) { continue }
    $key = $text.Substring(0, $text.IndexOf('=')).Trim()
    $value = $text.Substring($text.IndexOf('=') + 1).Trim()
    # Igual que python-dotenv: las comillas que rodean el valor no forman parte de él.
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not (Test-Path "Env:$key")) { Set-Item "Env:$key" $value }
  }
  Write-Ok '.env cargado (sin sobrescribir variables de la terminal)'
}

$files = Join-Path $root '.local-files'
New-Item -ItemType Directory -Force -Path (Join-Path $files 'inputs'), (Join-Path $files 'outputs') | Out-Null
foreach ($name in @('WEBSITE_SITE_NAME', 'PROJECT_ENDPOINT', 'HOSTED_AGENT_NAME')) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
$env:WEB_AGENT_MODE = $Mode
$env:WEB_FILES_MODE = 'local'
$env:LOCAL_FILES_DIR = $files
$env:STARTER_RESPONDER = $Responder
$env:FILES_MODE = 'local'
if (-not $env:WEB_TITLE) { $env:WEB_TITLE = 'Text-to-SQL · pruebas' }
switch ($Sql) {
  'simulated' { $env:TEXT2SQL_EXECUTE = 'true'; $env:TEXT2SQL_SQL_BACKEND = 'simulated' }
  'none' { $env:TEXT2SQL_EXECUTE = 'false'; $env:TEXT2SQL_SQL_BACKEND = 'odbc' }
  'odbc' {
    $env:TEXT2SQL_EXECUTE = 'true'; $env:TEXT2SQL_SQL_BACKEND = 'odbc'
    Write-Warn 'Se ejecutarán consultas reales contra la base configurada. Use una cuenta de solo lectura sobre la vista autorizada.'
  }
}
if (-not $env:SQL_VIEW_SCHEMA -or -not $env:SQL_VIEW_NAME) {
  if ($Sql -eq 'odbc' -and -not $Check) { throw 'Defina SQL_VIEW_SCHEMA y SQL_VIEW_NAME (en .env o en la terminal) para -Sql odbc.' }
  if ($Sql -ne 'odbc') {
    if (-not $env:SQL_VIEW_SCHEMA) { $env:SQL_VIEW_SCHEMA = 'simulado' }
    if (-not $env:SQL_VIEW_NAME) { $env:SQL_VIEW_NAME = 'vw_chatbot_mom' }
    Write-Warn 'Vista de prueba (simulada): defina SQL_VIEW_SCHEMA y SQL_VIEW_NAME reales en .env'
  }
}
if ($Check) {
  # Valida exactamente el entorno que usaría esta ejecución; no imprime valores.
  Write-Step "Comprobando configuración: modo $Mode · generador $Responder · SQL $Sql"
  & $python (Join-Path $root 'scripts/check_local_config.py')
  exit $LASTEXITCODE
}
Write-Ok "Generador: $Responder · SQL: $Sql · vista [$($env:SQL_VIEW_SCHEMA)].[$($env:SQL_VIEW_NAME)]"

if ($Mode -eq 'agent') {
  # Solo el agente (dependencias de agent/requirements.txt), sin la interfaz Streamlit.
  $env:PORT = [string]$AgentPort
  Write-Ok "Agente local en http://localhost:$AgentPort/invocations (Ctrl+C para terminar)"
  # 0099000001: suministro sintético (dato de prueba).
  Write-Ok "Pruebe en otra consola: ./scripts/invoke-local.ps1 -Message 'Datos del medidor del suministro 0099000001'"
  Push-Location (Join-Path $root 'agent')
  try { & $python main.py } finally { Pop-Location }
  exit $LASTEXITCODE
}

$agent = $null
try {
  if ($Mode -eq 'local-http') {
    $env:PORT = [string]$AgentPort
    $env:LOCAL_AGENT_URL = "http://localhost:$AgentPort"
    $agent = Start-Process -FilePath $python -ArgumentList @('main.py') -WorkingDirectory (Join-Path $root 'agent') -PassThru -NoNewWindow
    Wait-LocalAgent -Url $env:LOCAL_AGENT_URL -Process $agent
    Write-Ok "Agente local listo en http://localhost:$AgentPort/invocations (PID $($agent.Id))"
  }
  Write-Ok "Streamlit en http://localhost:$WebPort (Ctrl+C para terminar)"
  # Solo localhost: en modo local la página no tiene autenticación.
  & $python -m streamlit run (Join-Path $root 'web/streamlit_app.py') --server.address localhost --server.port $WebPort --server.headless true --browser.gatherUsageStats false
} finally {
  if ($agent -and -not $agent.HasExited) { Stop-Process -Id $agent.Id -Force }
}
