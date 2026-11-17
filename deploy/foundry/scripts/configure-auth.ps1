<#
.SYNOPSIS
  Comprueba y completa la configuración Entra que Easy Auth necesita (idempotente).
.DESCRIPTION
  Sobre el registro de aplicación (authClientId):
    1. Un solo tenant (signInAudience = AzureADMyOrg). Solo se comprueba.
    2. Redirect URI web = salida webRedirectUri del despliegue.
    3. Emisión de ID tokens (Easy Auth completa el callback con id_token).
    4. Credencial federada: la identidad administrada de la web sustituye al secreto.
  Sobre la Enterprise Application (service principal):
    5. Assignment required = Yes.
    6. Asignación de los usuarios de allowedUserObjectIds (y aviso de asignaciones extra).
  En App Service: Easy Auth apunta al registro y a la identidad correctos.

  Requiere ser propietario del registro o tener el rol Entra Application Administrator /
  Cloud Application Administrator. -CheckOnly no modifica nada.
.EXAMPLE
  ./scripts/configure-auth.ps1 -CheckOnly
  ./scripts/configure-auth.ps1
#>
param([string]$ParametersFile, [switch]$CheckOnly)
. (Join-Path $PSScriptRoot 'common.ps1')

$p = Read-StarterParameters $ParametersFile
Assert-WebDeployed $p
Assert-AzureContext $p | Out-Null
$outputs = Get-StarterOutputs $p
$problems = @()
$graph = 'https://graph.microsoft.com/v1.0'

function Invoke-GraphJson([string]$Method, [string]$Url, $Body) {
  $arguments = @('rest', '--method', $Method, '--url', $Url, '--resource', 'https://graph.microsoft.com')
  $file = $null
  if ($null -ne $Body) {
    $file = Join-Path ([System.IO.Path]::GetTempPath()) "starter-graph-$([guid]::NewGuid().ToString('N')).json"
    [System.IO.File]::WriteAllText($file, ($Body | ConvertTo-Json -Depth 5 -Compress), [System.Text.UTF8Encoding]::new($false))
    $arguments += @('--headers', 'Content-Type=application/json', '--body', "@$file")
  }
  try {
    return Invoke-AzJson -Arguments $arguments -ErrorMessage "Graph $Method $Url falló. Revise sus permisos en Entra."
  } finally {
    if ($file) { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
  }
}

Write-Step "Registro de aplicación $($p.authClientId)"
$app = Invoke-AzJson -Arguments @('ad', 'app', 'show', '--id', $p.authClientId) -AllowFailure
if (-not $app) {
  throw "No existe un registro con Application (client) ID $($p.authClientId) en este tenant. Use el Application ID de App registrations, no el Object ID ni el de Enterprise applications."
}
if ($app.signInAudience -ne 'AzureADMyOrg') {
  $problems += "El registro admite '$($app.signInAudience)'. Cámbielo a un solo tenant (AzureADMyOrg) en Authentication > Supported account types."
} else { Write-Ok 'Un solo tenant' }

$web = Get-Prop $app 'web'
$redirects = @(Get-Prop $web 'redirectUris')
if ($redirects -notcontains $outputs.webRedirectUri) {
  if ($CheckOnly) { $problems += "Falta la Redirect URI web $($outputs.webRedirectUri)" }
  else {
    $all = @($redirects | Where-Object { $_ }) + $outputs.webRedirectUri
    Invoke-Az -Arguments (@('ad', 'app', 'update', '--id', $p.authClientId, '--web-redirect-uris') + $all) `
      -ErrorMessage "No se pudo añadir la Redirect URI. Añádala en Authentication > Web: $($outputs.webRedirectUri)" | Out-Null
    Write-Ok "Redirect URI añadida: $($outputs.webRedirectUri)"
  }
} else { Write-Ok "Redirect URI $($outputs.webRedirectUri)" }

$implicit = Get-Prop $web 'implicitGrantSettings'
if (-not (Get-Prop $implicit 'enableIdTokenIssuance')) {
  if ($CheckOnly) { $problems += 'ID tokens deshabilitado (AADSTS700054 en el callback)' }
  else {
    Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $p.authClientId, '--enable-id-token-issuance', 'true') `
      -ErrorMessage 'No se pudo habilitar ID tokens. Actívelo en Authentication > Implicit grant and hybrid flows.' | Out-Null
    Write-Ok 'ID tokens habilitado'
  }
} else { Write-Ok 'ID tokens habilitado' }

Write-Step 'Credencial federada (sin secretos)'
$ficUrl = "$graph/applications/$($app.id)/federatedIdentityCredentials"
$issuer = "https://login.microsoftonline.com/$($p.tenantId)/v2.0"
$ficName = "$($p.prefix)-web-easyauth"
$current = Invoke-GraphJson GET $ficUrl $null
$named = @($current.value | Where-Object { $_.name -eq $ficName }) | Select-Object -First 1
$equivalent = @($current.value | Where-Object {
    $_.subject -eq $outputs.webIdentityPrincipalId -and $_.issuer -eq $issuer -and @($_.audiences) -contains 'api://AzureADTokenExchange' })
if ($named -and $equivalent -notcontains $named) {
  throw "Existe la credencial '$ficName' con otro sujeto, emisor o audiencia. Revísela en Certificates & secrets > Federated credentials; el sujeto debe ser $($outputs.webIdentityPrincipalId)."
}
if ($equivalent.Count -gt 0) { Write-Ok "Credencial federada para $($outputs.webIdentityName)" }
elseif ($CheckOnly) { $problems += "Falta la credencial federada (sujeto $($outputs.webIdentityPrincipalId))" }
else {
  Invoke-GraphJson POST $ficUrl @{ name = $ficName; issuer = $issuer; subject = $outputs.webIdentityPrincipalId
    audiences = @('api://AzureADTokenExchange'); description = 'Easy Auth con identidad administrada' } | Out-Null
  Write-Ok "Credencial federada creada: $ficName"
}

Write-Step 'Enterprise Application'
$sp = Invoke-AzJson -Arguments @('ad', 'sp', 'show', '--id', $p.authClientId) -AllowFailure
if (-not $sp) {
  if ($CheckOnly) { $problems += 'No existe la Enterprise Application (service principal) del registro' }
  else { $sp = Invoke-AzJson -Arguments @('ad', 'sp', 'create', '--id', $p.authClientId) -ErrorMessage 'No se pudo crear el service principal.'; Write-Ok 'Service principal creado' }
}
if ($sp) {
  if (-not $sp.appRoleAssignmentRequired) {
    if ($CheckOnly) { $problems += 'Assignment required = No: cualquier usuario del tenant podría iniciar sesión' }
    else {
      Invoke-Az -Arguments @('ad', 'sp', 'update', '--id', $sp.id, '--set', 'appRoleAssignmentRequired=true') `
        -ErrorMessage 'No se pudo activar Assignment required. Actívelo en Enterprise applications > Properties.' | Out-Null
      Write-Ok 'Assignment required = Yes'
    }
  } else { Write-Ok 'Assignment required = Yes' }

  $assigned = Invoke-GraphJson GET "$graph/servicePrincipals/$($sp.id)/appRoleAssignedTo" $null
  $principals = @($assigned.value | ForEach-Object { $_.principalId })
  $userRoles = @(@(Get-Prop $app 'appRoles') | Where-Object { $_.isEnabled -and @($_.allowedMemberTypes) -contains 'User' })
  foreach ($user in @($p.allowedUserObjectIds)) {
    if ($principals -contains $user) { Write-Ok "Usuario $user asignado"; continue }
    if ($CheckOnly) { $problems += "Usuario $user sin asignar a la Enterprise Application"; continue }
    if ($userRoles.Count -gt 0) {
      $problems += "El registro define app roles; asigne a $user el rol adecuado en Enterprise applications > Users and groups."
      continue
    }
    Invoke-GraphJson POST "$graph/servicePrincipals/$($sp.id)/appRoleAssignedTo" @{
      principalId = $user; resourceId = $sp.id; appRoleId = $script:ZeroGuid } | Out-Null
    Write-Ok "Usuario $user asignado (acceso predeterminado)"
  }
  $extra = @($assigned.value | Where-Object { @($p.allowedUserObjectIds) -notcontains $_.principalId })
  foreach ($item in $extra) {
    Write-Warn "Asignación adicional: $($item.principalDisplayName) ($($item.principalType) $($item.principalId)). La página la rechazará si no está en allowedUserObjectIds."
  }
}

Write-Step 'Easy Auth en App Service'
$siteId = "/subscriptions/$($p.subscriptionId)/resourceGroups/$($outputs.resourceGroupName)/providers/Microsoft.Web/sites/$($outputs.webAppName)"
$auth = Invoke-AzJson -Arguments @('rest', '--method', 'POST', '--url', "https://management.azure.com$siteId/config/authsettingsV2/list?api-version=2024-04-01")
$authProperties = Get-Prop $auth 'properties'
$registration = Get-Prop (Get-Prop (Get-Prop $authProperties 'identityProviders') 'azureActiveDirectory') 'registration'
if (-not (Get-Prop (Get-Prop $authProperties 'platform') 'enabled') -or
    -not (Get-Prop (Get-Prop $authProperties 'globalValidation') 'requireAuthentication')) {
  $problems += 'Easy Auth no exige autenticación; vuelva a ejecutar deploy-infra.ps1'
} elseif ((Get-Prop $registration 'clientId') -ne $p.authClientId -or
    (Get-Prop $registration 'clientSecretSettingName') -ne 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID') {
  $problems += 'Easy Auth apunta a otro registro o usa secreto; vuelva a ejecutar deploy-infra.ps1'
} else { Write-Ok 'Easy Auth exige inicio de sesión con el registro y la credencial federada' }
$settings = Invoke-AzJson -Arguments @('webapp', 'config', 'appsettings', 'list', '--resource-group', $outputs.resourceGroupName, '--name', $outputs.webAppName)
$fic = @($settings | Where-Object { $_.name -eq 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID' }) | Select-Object -First 1
if (-not $fic -or $fic.value -ne $outputs.webIdentityClientId) {
  $problems += 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID no coincide con el clientId de la identidad de la web'
} else { Write-Ok 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID = clientId de la identidad de la web' }

if ($problems.Count -gt 0) {
  Write-Host ''
  throw "Configuración de autenticación incompleta:`n - " + ($problems -join "`n - ")
}
Write-Host ''
Write-Host 'Autenticación lista. Siguiente paso: ./scripts/publish-web.ps1' -ForegroundColor Green
