<#
.SYNOPSIS
  Comprueba disponibilidad y controles de seguridad del piloto publicado (solo lectura, salvo -InvokeAgent).
.DESCRIPTION
  Agente : versión activa, identidad y permisos (grant-agent-permissions -CheckOnly).
  Web    : sitio Running, último despliegue correcto, Streamlit responde en /_stcore/health
           y la raíz exige inicio de sesión (redirección a Entra o 401).
  Blob   : sin acceso anónimo ni claves compartidas; contenedores privados; listado anónimo rechazado.
  Web y Blob solo si están desplegados (web.deploy, files.enabled).
  -InvokeAgent: envía un turno por la API (-Message, por defecto un texto de prueba) con la identidad del
  operador (requiere Foundry Agent Consumer en el proyecto; Bicep la concede a publisherObjectId) y
  muestra el inicio de la respuesta. Es la única opción que ejecuta el agente.
.EXAMPLE
  ./scripts/check-availability.ps1 -InvokeAgent
  ./scripts/check-availability.ps1 -InvokeAgent -Message 'Datos del medidor del suministro 0099000001'
  # 0099000001 es un suministro sintético (dato de prueba); contra la base real use uno existente.
#>
param([string]$ParametersFile, [switch]$InvokeAgent, [string]$Message = 'Prueba de disponibilidad', [int]$StartupTimeoutSeconds = 300)
. (Join-Path $PSScriptRoot 'common.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p
$failures = @()

function Get-Status([string]$Url) {
  try {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $false
    $request.Timeout = 30000
    $response = $request.GetResponse()
  } catch [System.Net.WebException] {
    $response = $_.Exception.Response
    if (-not $response) { return [pscustomobject]@{ Status = 0; Location = ''; Body = '' } }
  }
  try {
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
    $body = $reader.ReadToEnd()
    return [pscustomobject]@{ Status = [int]$response.StatusCode; Location = [string]$response.Headers['Location']; Body = $body }
  } finally { $response.Close() }
}

Write-Step 'Agente'
$agent = Get-HostedAgent $outputs
if (-not $agent) { $failures += 'agente no publicado' }
else {
  $latest = Get-AgentLatestVersion $agent
  $status = [string](Get-Prop $latest 'status')
  if ($status -eq 'active') { Write-Ok "Versión $(Get-Prop $latest 'version') activa" } else { $failures += "versión del agente en estado '$status'" }
  try { & (Join-Path $PSScriptRoot 'grant-agent-permissions.ps1') -ParametersFile $p.ParametersFile -CheckOnly }
  catch { $failures += "permisos del agente: $($_.Exception.Message)" }
}

if (-not (Test-WebDeploy $p)) {
  Write-Step 'Web'
  Write-Warn 'web.deploy=false: sin web; el agente se consume por API.'
} else {
Write-Step 'Web'
$state =(Invoke-Az -Arguments @('webapp', 'show', '--resource-group', $outputs.resourceGroupName, '--name', $outputs.webAppName, '--query', 'state', '--output', 'tsv')).Out.Trim()
if ($state -eq 'Running') { Write-Ok 'Sitio Running' } else { $failures += "sitio en estado $state" }
$deployment = @(Invoke-AzJson -Arguments @('webapp', 'log', 'deployment', 'list', '--resource-group', $outputs.resourceGroupName, '--name', $outputs.webAppName) -AllowFailure) | Where-Object { $_ } | Select-Object -First 1
if ($deployment -and [int](Get-Prop $deployment 'status') -eq 4) { Write-Ok "Último despliegue correcto ($($deployment.id))" } else { $failures += 'no hay un despliegue web correcto' }

if (Get-Prop $p.web 'exposeHealthProbe') {
  $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
  $health = $null
  do {
    $health = Get-Status "$($outputs.webUrl)/_stcore/health"
    if ($health.Status -eq 200) { break }
    Write-Host "    /_stcore/health -> HTTP $($health.Status); esperando arranque..."
    Start-Sleep -Seconds 15
  } while ((Get-Date) -lt $deadline)
  if ($health.Status -eq 200) { Write-Ok 'Streamlit arrancó (/_stcore/health = 200)' }
  else { $failures += "Streamlit no responde (HTTP $($health.Status)); revise: az webapp log tail -g $($outputs.resourceGroupName) -n $($outputs.webAppName)" }
} else { Write-Warn 'exposeHealthProbe=false: no se puede comprobar el arranque sin iniciar sesión.' }

$root = Get-Status "$($outputs.webUrl)/"
if (($root.Status -in @(301, 302)) -and $root.Location -match 'login\.microsoftonline\.com|/\.auth/login') {
  Write-Ok 'La raíz exige inicio de sesión Entra (redirección)'
} elseif ($root.Status -eq 401) { Write-Ok 'La raíz exige autenticación (401)' }
else { $failures += "la raíz respondió HTTP $($root.Status) sin exigir inicio de sesión" }
}  # fin web.deploy

if ($outputs.filesEnabled) {
  Write-Step 'Blob privado'
  $account = Invoke-AzJson -Arguments @('storage', 'account', 'show', '--resource-group', $outputs.resourceGroupName, '--name', $outputs.storageAccountName)
  if ($account.allowBlobPublicAccess -eq $false) { Write-Ok 'allowBlobPublicAccess = false' } else { $failures += 'allowBlobPublicAccess no es false' }
  if ($account.allowSharedKeyAccess -eq $false) { Write-Ok 'allowSharedKeyAccess = false' } else { $failures += 'allowSharedKeyAccess no es false' }
  foreach ($container in @($outputs.inputContainer, $outputs.outputContainer)) {
    $info = Invoke-AzJson -Arguments @('storage', 'container-rm', 'show', '--resource-group', $outputs.resourceGroupName,
      '--storage-account', $outputs.storageAccountName, '--name', $container)
    if ($info.publicAccess -in @('None', $null)) { Write-Ok "$container sin acceso público" } else { $failures += "$container con acceso público $($info.publicAccess)" }
    $anonymous = Get-Status "$($outputs.storageAccountUrl)$container`?restype=container&comp=list"
    if ($anonymous.Status -in @(401, 403, 404, 409)) { Write-Ok "Listado anónimo de $container rechazado (HTTP $($anonymous.Status))" }
    else { $failures += "listado anónimo de $container devolvió HTTP $($anonymous.Status)" }
  }
}

if ($InvokeAgent) {
  Write-Step 'Invocación de prueba (identidad del operador)'
  $url = "$($outputs.projectEndpoint)/agents/$($outputs.agentName)/endpoint/protocols/invocations?api-version=v1"
  $body = @{ contract = 'starter.v1'; request_id = [guid]::NewGuid().ToString('N'); message = $Message; state = @{} } | ConvertTo-Json -Compress
  try {
    $response = Invoke-WebRequest -Method Post -Uri $url -Headers @{ Authorization = "Bearer $(Get-FoundryToken)" } `
      -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing -TimeoutSec 180
    $text = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
    $result = $null
    if ([string]$response.Headers['Content-Type'] -like 'text/event-stream*') {
      # Igual que web/client.py: último evento SSE con reply o error.
      foreach ($line in ($text -split "`r?`n")) {
        if (-not $line.StartsWith('data:')) { continue }
        try { $event = $line.Substring(5).Trim() | ConvertFrom-Json } catch { continue }
        if ((Get-Prop $event 'reply') -or (Get-Prop $event 'error')) { $result = $event }
      }
    } else { $result = $text | ConvertFrom-Json }
    $reply = [string](Get-Prop $result 'reply')
    if ($reply) {
      Write-Ok "Respuesta recibida; sesión $($response.Headers['x-agent-session-id'])"
      Write-Host ('    ' + $reply.Substring(0, [Math]::Min(400, $reply.Length)).Replace("`n", "`n    "))
    } else { $failures += "la invocación no devolvió reply: $text" }
  } catch {
    $invokeError = $_
    # Windows PowerShell 5.1 descarta el cuerpo en respuestas 4xx/5xx: se lee para ver el error del agente.
    $detail = ''
    $errorResponse = $invokeError.Exception.Response
    if ($errorResponse) {
      try {
        $errorReader = New-Object System.IO.StreamReader($errorResponse.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $detail = $errorReader.ReadToEnd()
        if ($detail.Length -gt 1500) { $detail = $detail.Substring(0, 1500) + '...' }
      } catch { $detail = '' }
    }
    $message = "la invocación falló: $($invokeError.Exception.Message)"
    if ($detail) { $message += "`n   Respuesta del agente: $detail" }
    $failures += "$message`n   Si es 403, asigne Foundry Agent Consumer al operador; si es 500 con model_forbidden, espere la propagación RBAC."
  }
}

Write-Host ''
if ($failures.Count -gt 0) { throw "Comprobaciones fallidas:`n - " + ($failures -join "`n - ") }
Write-Host "Agente (API): POST $($outputs.projectEndpoint)/agents/$($outputs.agentName)/endpoint/protocols/invocations?api-version=v1" -ForegroundColor Green
if (Test-WebDeploy $p) {
  Write-Host "Piloto disponible: $($outputs.webUrl)" -ForegroundColor Green
  Write-Host 'Pendiente manual: iniciar sesión con el usuario autorizado y con otra cuenta (debe ser rechazada).'
}
