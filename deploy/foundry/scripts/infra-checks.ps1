# Comprobaciones previas compartidas por preview-infra.ps1 y deploy-infra.ps1 (solo lectura).
. (Join-Path $PSScriptRoot 'common.ps1')

function Test-InfraPrerequisites($p) {
  Write-Step 'Proveedores de recursos'
  $providers = @('Microsoft.CognitiveServices', 'Microsoft.Authorization')
  if (Test-WebDeploy $p) { $providers += @('Microsoft.Web', 'Microsoft.ManagedIdentity') }
  if ((Get-FilesSettings $p).enabled) { $providers += 'Microsoft.Storage' }
  $hosting = Get-AgentHosting $p
  if ($hosting.mode -eq 'container') { $providers += 'Microsoft.ContainerRegistry' }
  foreach ($provider in $providers) {
    $state = (Invoke-Az -Arguments @('provider', 'show', '--namespace', $provider, '--query', 'registrationState', '--output', 'tsv')).Out.Trim()
    if ($state -ne 'Registered') { throw "Proveedor $provider en estado '$state'. Ejecute: az provider register --namespace $provider --wait" }
    Write-Ok $provider
  }

  if (-not (Test-ModelDeploy $p)) {
    Write-Step 'Modelo'
    Write-Warn 'model.deploy=false: no se crea despliegue ni se comprueba cuota. Use MODEL_ENDPOINT/MODEL_DEPLOYMENT_NAME/MODEL_RESOURCE_ID en agent.env.'
  } else {
  Write-Step "Modelo $($p.model.name) $($p.model.version) ($($p.model.sku)) en $($p.location)"
  $models =Invoke-AzJson -Arguments @('cognitiveservices', 'model', 'list', '--location', $p.location) -AllowFailure
  if ($null -eq $models) {
    Write-Warn 'No se pudo listar el catálogo de modelos de la región; what-if no valida cuota. Compruebe en el portal.'
  } else {
    $match = @($models | Where-Object {
        $model = Get-Prop $_ 'model'
        (Get-Prop $model 'name') -eq $p.model.name -and (Get-Prop $model 'version') -eq $p.model.version -and
        @(@(Get-Prop $model 'skus') | ForEach-Object { Get-Prop $_ 'name' }) -contains $p.model.sku })
    if ($match.Count -eq 0) {
      $versions = @($models | ForEach-Object { Get-Prop $_ 'model' } | Where-Object { (Get-Prop $_ 'name') -eq $p.model.name } |
          ForEach-Object { Get-Prop $_ 'version' } | Sort-Object -Unique) -join ', '
      throw "El modelo $($p.model.name) $($p.model.version) con SKU $($p.model.sku) no está disponible en $($p.location). Versiones listadas: $versions"
    }
    $lifecycle = Get-Prop (Get-Prop $match[0] 'model') 'lifecycleStatus'
    if ($lifecycle -and $lifecycle -notin @('GenerallyAvailable', 'Stable')) { Write-Warn "Estado del modelo: $lifecycle" }
    Write-Ok 'Modelo disponible en la región'
  }
  $usage = Invoke-AzJson -Arguments @('cognitiveservices', 'usage', 'list', '--location', $p.location) -AllowFailure
  if ($usage) {
    $quota = @($usage | Where-Object { (Get-Prop (Get-Prop $_ 'name') 'value') -eq "OpenAI.$($p.model.sku).$($p.model.name)" }) | Select-Object -First 1
    if ($quota) {
      # Si el despliegue ya existe, su capacidad ya está contada en currentValue (re-ejecución segura).
      $current = Invoke-AzJson -Arguments @('cognitiveservices', 'account', 'deployment', 'show', '--resource-group', $p.resourceGroupName,
        '--name', "$($p.prefix)-foundry", '--deployment-name', $p.model.deploymentName) -AllowFailure
      $already = 0
      if ($current) { $already = [double](Get-Prop (Get-Prop $current 'sku') 'capacity') }
      $needed = [double]$p.model.capacity - $already
      $free = [double]$quota.limit - [double]$quota.currentValue
      if ($needed -gt 0 -and $free -lt $needed) {
        throw "Cuota insuficiente: $($quota.name.value) libre $free, adicional requerida $needed. Reduzca model.capacity o pida aumento."
      }
      Write-Ok "Cuota suficiente: libre $free, adicional requerida $([Math]::Max($needed, 0))"
    } else { Write-Warn 'No se encontró la cuota del modelo en el listado; se validará al desplegar.' }
  }
  }  # fin model.deploy

  Write-Step 'Estado de Foundry (evita solapar operaciones)'
  $state = Invoke-Az -Arguments @('cognitiveservices', 'account', 'show', '--resource-group', $p.resourceGroupName,
    '--name', "$($p.prefix)-foundry", '--query', 'properties.provisioningState', '--output', 'tsv') -AllowFailure
  if ($state.ExitCode -eq 0) {
    $value = $state.Out.Trim()
    if ($value -in @('Creating', 'Updating', 'Deleting', 'Accepted')) { throw "Foundry está en estado $value. Espere y repita." }
    if ($value -eq 'Failed') { throw "Foundry quedó en Failed. Revise: az deployment group operation list -g $($p.resourceGroupName) -n foundry-starter-resources" }
    Write-Ok "Foundry existente en estado $value"
  } else { Write-Ok 'Foundry se creará' }

  if ($hosting.mode -eq 'container') {
    Write-Step "Registro de contenedores $($hosting.registryName)"
    $registry = Invoke-AzJson -Arguments @('acr', 'show', '--name', $hosting.registryName, '--resource-group', $p.resourceGroupName) -AllowFailure
    if (-not $registry) {
      $check = Invoke-AzJson -Arguments @('acr', 'check-name', '--name', $hosting.registryName)
      if (-not $check.nameAvailable) { throw "agentHosting.registryName no disponible: $($check.message)" }
      Write-Ok 'Nombre disponible'
    } else { Write-Ok 'Registro existente en el grupo de recursos' }
  }

  $files = Get-FilesSettings $p
  if ($files.enabled) {
    Write-Step "Nombre de Storage $($files.storageAccountName)"
    $existing = Invoke-AzJson -Arguments @('storage', 'account', 'show', '--name', $files.storageAccountName, '--resource-group', $p.resourceGroupName) -AllowFailure
    if (-not $existing) {
      $check = Invoke-AzJson -Arguments @('storage', 'account', 'check-name', '--name', $files.storageAccountName)
      if (-not $check.nameAvailable) { throw "storageAccountName no disponible: $($check.message)" }
      Write-Ok 'Nombre disponible'
    } else { Write-Ok 'Cuenta existente en el grupo de recursos' }
  }
}
