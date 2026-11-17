<#
.SYNOPSIS
  Construye la imagen del agente (docker/agent.Dockerfile), la prueba en local y, con -Push, la sube al ACR.
.DESCRIPTION
  Sin -Push no usa Azure:
    1. docker build --platform linux/amd64 (la plataforma de los Hosted Agents) con contexto agent/.
    2. Prueba de humo en un contenedor local, con simulaciones (sin modelo ni base de datos):
       - pyodbc ve el driver de la imagen (ODBC Driver 18 for SQL Server);
       - la instrumentación OpenAI de la telemetría se importa (requiere httpx);
       - /readiness responde y una invocación starter.v1 devuelve HTTP 200;
       - el registro de arranque no contiene errores de instrumentación.
  Con -Push (requiere deploy-infra.ps1 con agentHosting.mode=container y AcrPush en el registro):
    3. az acr login (Entra; el registro no tiene usuario administrador), docker tag y docker push
       de la MISMA imagen probada.
    4. Devuelve la referencia fijada por digest (<registro>.azurecr.io/text2sql-agent@sha256:...) y la
       guarda en .build/agent-image-<prefix>.txt. publish-agent.ps1 la usa en el manifiesto.
.EXAMPLE
  ./scripts/build-agent-image.ps1                 # solo local
  ./scripts/build-agent-image.ps1 -Push           # local + subida al ACR del piloto
#>
param(
  [string]$ParametersFile,
  [switch]$Push,
  [string]$Tag,
  [int]$Port = 8089,
  [switch]$SkipSmokeTest
)
. (Join-Path $PSScriptRoot 'common.ps1')

$repository = 'text2sql-agent'
if (-not $Tag) { $Tag = (Get-Date).ToString('yyyyMMdd-HHmmss') }
if ($Tag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') { throw "Etiqueta no válida: $Tag" }
$localImage = "${repository}:$Tag"

# Con -Push se valida el destino antes de construir (falla pronto si falta algo en Azure).
$p = $null; $outputs = $null
if ($Push) {
  $p = Read-StarterParameters $ParametersFile
  if ((Get-AgentHosting $p).mode -ne 'container') { throw 'agentHosting.mode no es container en los parámetros: no hay registro de contenedores.' }
  Assert-AzureContext $p | Out-Null
  $outputs = Get-StarterOutputs $p
  if (-not (Get-Prop $outputs 'containerRegistryLoginServer')) { throw 'El despliegue no tiene registro de contenedores. Ejecute deploy-infra.ps1 con agentHosting.mode=container.' }
}

Write-Step 'Docker'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Instale Docker Desktop (motor Linux).' }
$server = Invoke-Native -FilePath 'docker' -Arguments @('version', '--format', '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}')
if ($server.ExitCode -ne 0) { throw 'El motor de Docker no responde. Inicie Docker Desktop y repita.' }
Write-Ok "Motor $($server.Out.Trim())"

Write-Step "Construyendo $localImage (linux/amd64)"
$code = Invoke-NativeStreaming -FilePath 'docker' -Arguments @('build', '--platform', 'linux/amd64', '-f',
  (Join-Path $script:StarterRoot 'docker/agent.Dockerfile'), '-t', $localImage, (Join-Path $script:StarterRoot 'agent'))
if ($code -ne 0) { throw 'Falló docker build.' }
Write-Ok "Imagen $localImage"

if (-not $SkipSmokeTest) {
  Write-Step 'Prueba de humo (simulada, sin Azure)'
  $probe = "import importlib, pyodbc; d = pyodbc.drivers(); assert '$($script:ContainerOdbcDriver)' in d, d; " +
    "importlib.import_module('opentelemetry.instrumentation.openai_v2'); print('drivers=' + ','.join(d))"
  $check = Invoke-Native -FilePath 'docker' -Arguments @('run', '--rm', '--entrypoint', 'python', $localImage, '-c', $probe)
  if ($check.ExitCode -ne 0) { throw "La imagen no supera la comprobación de driver/telemetría:`n$($check.Out)`n$($check.Err)" }
  Write-Ok "$($check.Out.Trim()); instrumentación OpenAI importable"

  $name = "t2s-smoke-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
  $run = Invoke-Native -FilePath 'docker' -Arguments @('run', '-d', '--name', $name, '-p', "127.0.0.1:${Port}:8088",
    '-e', 'STARTER_RESPONDER=echo', '-e', 'TEXT2SQL_EXECUTE=true', '-e', 'TEXT2SQL_SQL_BACKEND=simulated',
    '-e', 'SQL_VIEW_SCHEMA=simulado', '-e', 'SQL_VIEW_NAME=vw_chatbot_mom', $localImage)
  if ($run.ExitCode -ne 0) { throw "No se pudo iniciar el contenedor de prueba (¿puerto $Port ocupado? use -Port):`n$($run.Err)" }
  try {
    Wait-LocalAgent -Url "http://127.0.0.1:$Port" -TimeoutSeconds 60
    # 0099000001: suministro sintético (dato de prueba).
    & (Join-Path $PSScriptRoot 'invoke-local.ps1') -Url "http://127.0.0.1:$Port" -Message 'Datos del medidor del suministro 0099000001' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'La invocación de prueba no devolvió HTTP 200.' }
    $logs = Invoke-Native -FilePath 'docker' -Arguments @('logs', $name)
    if (($logs.Out + $logs.Err) -match 'Exception occurred when instrumenting') {
      throw 'El arranque registra errores de instrumentación (revise httpx en agent/requirements.lock.txt).'
    }
    Write-Ok 'Invocación simulada HTTP 200 y arranque sin errores de instrumentación'
  } finally {
    Invoke-Native -FilePath 'docker' -Arguments @('rm', '-f', $name) | Out-Null
  }
}

if (-not $Push) {
  Write-Host ''
  Write-Host "Imagen local lista: $localImage. Para subirla: ./scripts/build-agent-image.ps1 -Push" -ForegroundColor Green
  return
}

$loginServer = [string]$outputs.containerRegistryLoginServer
Write-Step "Subiendo a $loginServer"
$login = Invoke-Native -FilePath 'az' -Arguments @('acr', 'login', '--name', $outputs.containerRegistryName, '--only-show-errors')
if ($login.ExitCode -ne 0) { throw "az acr login falló. El operador necesita AcrPush en el registro (Bicep lo concede a publisherObjectId).`n$($login.Err)" }
$remoteImage = "$loginServer/${repository}:$Tag"
if ((Invoke-NativeStreaming -FilePath 'docker' -Arguments @('tag', $localImage, $remoteImage)) -ne 0) { throw 'Falló docker tag.' }
if ((Invoke-NativeStreaming -FilePath 'docker' -Arguments @('push', $remoteImage)) -ne 0) { throw 'Falló docker push.' }
$digests = Invoke-Native -FilePath 'docker' -Arguments @('inspect', '--format', '{{range .RepoDigests}}{{println .}}{{end}}', $remoteImage)
$reference = @($digests.Out -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -like "$loginServer/$repository@sha256:*" }) | Select-Object -First 1
if (-not $reference) { throw 'No se obtuvo el digest de la imagen subida.' }
$record = Join-Path $script:StarterRoot ".build/agent-image-$($p.prefix).txt"
New-Item -ItemType Directory -Force -Path (Split-Path $record) | Out-Null
Set-Content -LiteralPath $record -Value $reference -Encoding ASCII
Write-Ok "Imagen publicada: $reference"
return $reference
