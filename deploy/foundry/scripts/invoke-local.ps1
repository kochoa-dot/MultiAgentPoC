<#
.SYNOPSIS
  Invoca el agente local por HTTP (POST /invocations, contrato starter.v1) de forma reproducible.
.DESCRIPTION
  Espera a GET /readiness y envía {contract, request_id, message, state}, el mismo cuerpo que la web.
  Con -StateFile guarda el "state" y la sesión devueltos y los reenvía en la llamada siguiente
  (conversación de varios turnos); -NewConversation empieza de cero.
  No usa Azure ni credenciales propias: el agente debe estar arrancado con
  ./scripts/run-local.ps1 -Mode agent (o -Mode local-http). Sirve igual contra el contenedor local.
  Código de salida: 0 con HTTP 200; 1 en cualquier otro caso (se muestra error.code del contrato).
.EXAMPLE
  ./scripts/invoke-local.ps1 -Message 'Datos del medidor del suministro 0099000001'   # suministro sintético (dato de prueba)
  ./scripts/invoke-local.ps1 -Message '/historial' -StateFile .build/conversacion.json
  ./scripts/invoke-local.ps1 -Message 'hola' -Url http://localhost:8089 -Raw
#>
param(
  [Parameter(Mandatory = $true)][string]$Message,
  [string]$Url = 'http://localhost:8088',
  [string]$StateFile,
  [string]$SessionId,
  [string]$RequestId,
  [switch]$NewConversation,
  [switch]$Raw,
  [int]$TimeoutSeconds = 120
)
. (Join-Path $PSScriptRoot 'common.ps1')

# Solo el agente local: los agentes publicados exigen token y se invocan con check-availability.ps1.
if ($Url -notmatch '^http://(localhost|127\.0\.0\.1)(:\d+)?/?$') { throw '-Url solo admite http://localhost o http://127.0.0.1.' }
Wait-LocalAgent -Url $Url -TimeoutSeconds 30

$state = @{}
if ($StateFile -and -not $NewConversation -and (Test-Path -LiteralPath $StateFile)) {
  $saved = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
  if (Get-Prop $saved 'state') { $state = $saved.state }
  if (-not $SessionId) { $SessionId = [string](Get-Prop $saved 'session_id') }
}
if (-not $RequestId) { $RequestId = [guid]::NewGuid().ToString('N') }
$body = [ordered]@{ contract = 'starter.v1'; request_id = $RequestId; message = $Message; state = $state } |
  ConvertTo-Json -Depth 30 -Compress

$uri = $Url.TrimEnd('/') + '/invocations'
if ($SessionId) { $uri += '?agent_session_id=' + [uri]::EscapeDataString($SessionId) }

# HttpClient: no lanza excepciones con 4xx/5xx y lee el cuerpo como UTF-8 en PowerShell 5.1 y 7.
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
$previousEncoding = [Console]::OutputEncoding
try {
  [Console]::OutputEncoding = [Text.Encoding]::UTF8
  $content = New-Object System.Net.Http.StringContent($body, [Text.Encoding]::UTF8, 'application/json')
  $response = $client.PostAsync($uri, $content).GetAwaiter().GetResult()
  $status = [int]$response.StatusCode
  $text = [Text.Encoding]::UTF8.GetString($response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
  $values = $null
  $session = $null
  if ($response.Headers.TryGetValues('x-agent-session-id', [ref]$values)) { $session = @($values)[0] }

  if ($Raw) { Write-Output $text }
  $result = $null
  try { $result = $text | ConvertFrom-Json } catch { $result = $null }
  if ($status -eq 200 -and $result) {
    if (-not $Raw) {
      Write-Output ([string]$result.reply)
      foreach ($file in @(Get-Prop $result 'files')) { if ($file) { Write-Output "  archivo: $($file.id) ($($file.size) bytes)" } }
    }
    if ($StateFile) {
      $folder = Split-Path -Parent $StateFile
      if ($folder -and -not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
      [ordered]@{ session_id = $session; state = $result.state } | ConvertTo-Json -Depth 30 |
        Set-Content -LiteralPath $StateFile -Encoding UTF8
    }
    Write-Host "HTTP 200 · request_id $RequestId · sesión $session" -ForegroundColor Green
    exit 0
  }
  $errorInfo = $null
  if ($result) { $errorInfo = Get-Prop $result 'error' }
  if ($errorInfo) {
    Write-Host "HTTP $status · $($errorInfo.code): $($errorInfo.message) (retryable=$($errorInfo.retryable), retry_after_ms=$($errorInfo.retry_after_ms))" -ForegroundColor Yellow
  } else {
    Write-Host "HTTP $status · respuesta sin el formato del contrato" -ForegroundColor Yellow
    if (-not $Raw) { Write-Output $text }
  }
  exit 1
} finally {
  [Console]::OutputEncoding = $previousEncoding
  $client.Dispose()
}
