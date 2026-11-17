// Piloto: Foundry + proyecto + modelo (opcional), Streamlit en App Service Linux con Easy Auth
// (opcional), ACR para la imagen del agente (opcional), identidades administradas, RBAC mínimo
// y Blob privado opcional.
// El Hosted Agent NO se crea aquí: se publica después con scripts/publish-agent.ps1
// y sus permisos se asignan con scripts/grant-agent-permissions.ps1.
targetScope = 'subscription'

@description('Suscripción destino. Los scripts comprueban que la sesión de Azure CLI coincide.')
param subscriptionId string

@description('Tenant de Entra ID donde están el registro de aplicación y los usuarios.')
param tenantId string

param resourceGroupName string
param location string

@description('Prefijo en minúsculas (3-12). Determina nombres globales: <prefix>-foundry, <prefix>-web.')
@minLength(3)
@maxLength(12)
param prefix string

@description('Nombre del Hosted Agent que publicará azd. La web lo invoca por este nombre.')
param agentName string = '${prefix}-agent'

@description('Object IDs de los usuarios autorizados en la página (piloto: uno).')
@minLength(1)
param allowedUserObjectIds array

@description('Object ID de quien publica el agente (Foundry Project Manager). Vacío: no se asigna.')
param publisherObjectId string = ''

@allowed(['User', 'ServicePrincipal', 'Group'])
param publisherPrincipalType string = 'User'

@description('Application (client) ID del registro Entra de un solo tenant usado por Easy Auth.')
param authClientId string

@description('Despliegue del modelo. capacity está en miles de tokens por minuto. deploy=false: no se crea; el agente usa el modelo externo de agent.env.')
param model object = {
  deploy: true
  format: 'OpenAI'
  name: 'gpt-5-mini'
  version: '2025-08-07'
  deploymentName: 'chat'
  sku: 'GlobalStandard'
  capacity: 10
}

@description('App Service: deploy=false omite la web (agente solo por API); SKU, Python y si /_stcore/health queda fuera de Easy Auth.')
param web object = {
  deploy: true
  sku: 'B1'
  pythonVersion: '3.13'
  title: 'Agente · pruebas'
  exposeHealthProbe: true
}

@description('Archivos privados de entrada/salida. storageAccountName: único global, 3-24 minúsculas y números.')
param files object = {
  enabled: false
  storageAccountName: ''
  inputContainer: 'inputs'
  outputContainer: 'outputs'
  grantPublisherInputWrite: true
}

@description('Alojamiento del agente. code: remote_build sin ODBC. container: imagen docker/agent.Dockerfile (ODBC Driver 18) en un ACR nuevo; registryName único global, 5-50 letras y números.')
param agentHosting object = {
  mode: 'code'
  registryName: ''
  registrySku: 'Basic'
}

param tags object = {
  workload: 'foundry-starter'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources './resources.bicep' = {
  name: 'foundry-starter-resources'
  scope: rg
  params: {
    location: location
    prefix: prefix
    tenantId: tenantId
    agentName: agentName
    allowedUserObjectIds: allowedUserObjectIds
    publisherObjectId: publisherObjectId
    publisherPrincipalType: publisherPrincipalType
    authClientId: authClientId
    model: model
    web: web
    files: files
    agentHosting: agentHosting
    tags: tags
  }
}

output declaredSubscriptionId string = subscriptionId
output declaredTenantId string = tenantId
output resourceGroupName string = rg.name
output foundryAccountName string = resources.outputs.foundryAccountName
output projectName string = resources.outputs.projectName
output projectResourceId string = resources.outputs.projectResourceId
output projectEndpoint string = resources.outputs.projectEndpoint
output modelEndpoint string = resources.outputs.modelEndpoint
output modelDeploymentName string = resources.outputs.modelDeploymentName
output agentName string = agentName
output webEnabled bool = resources.outputs.webEnabled
output webAppName string = resources.outputs.webAppName
output webUrl string = resources.outputs.webUrl
output webRedirectUri string = resources.outputs.webRedirectUri
output webIdentityName string = resources.outputs.webIdentityName
output webIdentityPrincipalId string = resources.outputs.webIdentityPrincipalId
output webIdentityClientId string = resources.outputs.webIdentityClientId
output filesEnabled bool = resources.outputs.filesEnabled
output storageAccountName string = resources.outputs.storageAccountName
output storageAccountUrl string = resources.outputs.storageAccountUrl
output inputContainer string = resources.outputs.inputContainer
output outputContainer string = resources.outputs.outputContainer
output agentHostingMode string = resources.outputs.agentHostingMode
output containerRegistryName string = resources.outputs.containerRegistryName
output containerRegistryLoginServer string = resources.outputs.containerRegistryLoginServer
output containerRegistryResourceId string = resources.outputs.containerRegistryResourceId
