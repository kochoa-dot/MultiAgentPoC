param location string
param prefix string
param tenantId string
param agentName string
param allowedUserObjectIds array
param publisherObjectId string
param publisherPrincipalType string
param authClientId string
param model object
param web object
param files object
param agentHosting object
param tags object

var filesEnabled = files.enabled
// model.deploy=false: no se crea despliegue; el agente usa un modelo externo (MODEL_ENDPOINT en agent.env).
var deployModel = model.?deploy ?? true
// web.deploy=false: sin App Service ni Easy Auth; el agente se consume solo por API.
var deployWeb = web.?deploy ?? true
// agentHosting.mode=container: imagen propia (ODBC Driver 18) en un ACR del grupo de recursos.
var containerHosting = (agentHosting.?mode ?? 'code') == 'container'
var registryName = containerHosting ? agentHosting.registryName : 'unused'
var storageName = filesEnabled ? files.storageAccountName : 'unused'
var accountName = '${prefix}-foundry'
var projectName = '${prefix}-project'
var webAppName = '${prefix}-web'
var projectEndpoint = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
// Comando de inicio. Oryx extrae la compilación en $APP_PATH (/tmp/<id>); sin compilación
// el código queda en /home/site/wwwroot. streamlit_app.py añade la raíz a sys.path.
var startupCommand = 'python -m streamlit run "\${APP_PATH:-/home/site/wwwroot}/web/streamlit_app.py" --server.address 0.0.0.0 --server.port 8000 --server.headless true --server.enableCORS true --server.enableXsrfProtection true --browser.gatherUsageStats false'

// Roles integrados (IDs estables aunque cambien los nombres visibles).
var roles = {
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d' // antes «Azure AI User»
  foundryAgentConsumer: 'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'
  foundryProjectManager: 'eadc314b-1a2d-4efa-be10-5d325db5065e'
  blobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
}

// ---------------------------------------------------------------- Foundry
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true // solo Entra ID; sin claves
    publicNetworkAccess: 'Enabled' // piloto. Red privada: ver ARCHITECTURE.md
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {}
}

// Con deploy=false basta { deploy: false }: el acceso seguro (.?) evita exigir campos que no se usan.
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployModel) {
  parent: account
  name: model.?deploymentName ?? 'unused'
  sku: { name: model.?sku ?? 'GlobalStandard', capacity: model.?capacity ?? 1 }
  properties: {
    model: { format: model.?format ?? 'OpenAI', name: model.?name ?? '', version: model.?version ?? '' }
  }
}

// ---------------------------------------------------------------- Registro de contenedores (opcional)
// Solo autenticación Entra: sin usuario administrador. azureADAuthenticationAsArmPolicy habilitado es
// requisito de los Hosted Agents (Learn, «Hosted agent permissions»).
resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = if (containerHosting) {
  name: registryName
  location: location
  tags: tags
  sku: { name: agentHosting.?registrySku ?? 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled' // piloto; red privada: ver ARCHITECTURE.md
    policies: {
      azureADAuthenticationAsArmPolicy: { status: 'enabled' }
    }
  }
}

// ---------------------------------------------------------------- Storage (opcional)
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (filesEnabled) {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false // ningún contenedor puede ser anónimo
    allowSharedKeyAccess: false // sin claves ni SAS de cuenta: solo RBAC Entra
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled' // tráfico autenticado; red privada como extensión
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = if (filesEnabled) {
  parent: storage
  name: 'default'
}

resource inputContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (filesEnabled) {
  parent: blobService
  name: files.inputContainer
  properties: { publicAccess: 'None' }
}

resource outputContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (filesEnabled) {
  parent: blobService
  name: files.outputContainer
  properties: { publicAccess: 'None' }
}

// ---------------------------------------------------------------- Web
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = if (deployWeb) {
  name: '${prefix}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: web.sku }
  properties: { reserved: true }
}

// Identidad exclusiva de la web: invoca el agente, lee salidas y firma Easy Auth (FIC).
resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deployWeb) {
  name: '${prefix}-web-id'
  location: location
  tags: tags
}

var baseSettings = [
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' } // ZIP + Oryx instala requirements.txt
  { name: 'AZURE_CLIENT_ID', value: webIdentity!.properties.clientId }
  { name: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID', value: webIdentity!.properties.clientId }
  { name: 'WEB_AGENT_MODE', value: 'foundry' }
  { name: 'WEB_AUTH', value: 'easyauth' }
  { name: 'WEB_TITLE', value: web.title }
  { name: 'PROJECT_ENDPOINT', value: projectEndpoint }
  { name: 'HOSTED_AGENT_NAME', value: agentName }
  { name: 'EXPECTED_TENANT_ID', value: tenantId }
  { name: 'ALLOWED_USER_OBJECT_IDS', value: join(allowedUserObjectIds, ',') }
  { name: 'WEB_FILES_MODE', value: filesEnabled ? 'blob' : 'none' }
]
var fileSettings = filesEnabled ? [
  { name: 'STORAGE_ACCOUNT_URL', value: storage!.properties.primaryEndpoints.blob }
  { name: 'OUTPUT_CONTAINER', value: files.outputContainer }
] : []

resource webSite 'Microsoft.Web/sites@2024-04-01' = if (deployWeb) {
  name: webAppName
  location: location
  tags: tags
  kind: 'app,linux'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${webIdentity!.id}': {} } }
  properties: {
    serverFarmId: plan!.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|${web.pythonVersion}'
      appCommandLine: startupCommand
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      webSocketsEnabled: true // Streamlit usa WebSocket (/_stcore/stream)
      appSettings: concat(baseSettings, fileSettings)
    }
  }
}

resource webAuth 'Microsoft.Web/sites/config@2024-04-01' = if (deployWeb) {
  parent: webSite
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true, runtimeVersion: '~1' }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
      excludedPaths: web.exposeHealthProbe ? ['/_stcore/health'] : []
    }
    httpSettings: { requireHttps: true }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: authClientId
          // Sin secreto: Easy Auth firma con la identidad administrada (credencial federada).
          clientSecretSettingName: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
      }
    }
    login: { tokenStore: { enabled: false } }
  }
}

// ---------------------------------------------------------------- RBAC
// Identidad del proyecto sobre la cuenta: rol recomendado por Learn para el acceso del proyecto a los
// modelos de la cuenta (Hosted agent permissions). Se asigna también con model.deploy=false.
resource projectFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, roles.foundryUser)
  scope: account
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.foundryUser)
    description: 'Identidad del proyecto: acceso a los modelos de la cuenta'
  }
}

resource webAgentConsumer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployWeb) {
  name: guid(project.id, webIdentity!.id, roles.foundryAgentConsumer)
  scope: project
  properties: {
    principalId: webIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.foundryAgentConsumer)
    description: 'Streamlit invoca el Hosted Agent'
  }
}

resource webOutputReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (filesEnabled && deployWeb) {
  name: guid(outputContainer.id, webIdentity!.id, roles.blobDataReader)
  scope: outputContainer
  properties: {
    principalId: webIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataReader)
    description: 'Streamlit descarga archivos generados'
  }
}

resource publisherProjectManager 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(publisherObjectId)) {
  name: guid(project.id, publisherObjectId, roles.foundryProjectManager)
  scope: project
  properties: {
    principalId: publisherObjectId
    principalType: publisherPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.foundryProjectManager)
    description: 'Publicación de versiones del Hosted Agent'
  }
}

// Permite la invocación de prueba del operador (check-availability.ps1 -InvokeAgent, WEB_AUTH=developer).
resource publisherAgentConsumer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(publisherObjectId)) {
  name: guid(project.id, publisherObjectId, roles.foundryAgentConsumer)
  scope: project
  properties: {
    principalId: publisherObjectId
    principalType: publisherPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.foundryAgentConsumer)
    description: 'Invocación de prueba del operador'
  }
}

// Imagen del agente. azd concede AcrPull a la identidad del agente al publicar (Learn, «Deploy a hosted
// agent from a private Azure Container Registry»); otras páginas de Learn documentan la lectura por la
// identidad del proyecto, así que se concede a ambas (grant-agent-permissions.ps1 cubre la del agente).
resource projectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (containerHosting) {
  name: guid(registry.id, project.id, roles.acrPull)
  scope: registry
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    description: 'Identidad del proyecto: descarga de la imagen del Hosted Agent'
  }
}

// El operador sube la imagen probada en local (build-agent-image.ps1 -Push).
resource publisherAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (containerHosting && !empty(publisherObjectId)) {
  name: guid(registry.id, publisherObjectId, roles.acrPush)
  scope: registry
  properties: {
    principalId: publisherObjectId
    principalType: publisherPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPush)
    description: 'Subida de la imagen del Hosted Agent'
  }
}

resource publisherInputWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (filesEnabled && !empty(publisherObjectId) && files.grantPublisherInputWrite) {
  name: guid(inputContainer.id, publisherObjectId, roles.blobDataContributor)
  scope: inputContainer
  properties: {
    principalId: publisherObjectId
    principalType: publisherPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    description: 'Carga de archivos de entrada'
  }
}

output foundryAccountName string = account.name
output projectName string = project.name
output projectResourceId string = project.id
output projectEndpoint string = projectEndpoint
// Vacías con model.deploy=false: publish-agent.ps1 exige entonces MODEL_ENDPOINT/MODEL_DEPLOYMENT_NAME en agent.env.
output modelEndpoint string = deployModel ? 'https://${account.properties.customSubDomainName}.openai.azure.com' : ''
output modelDeploymentName string = deployModel ? (model.?deploymentName ?? '') : ''
// Vacías con web.deploy=false.
output webEnabled bool = deployWeb
output webAppName string = deployWeb ? webSite!.name : ''
output webUrl string = deployWeb ? 'https://${webSite!.properties.defaultHostName}' : ''
output webRedirectUri string = deployWeb ? 'https://${webSite!.properties.defaultHostName}/.auth/login/aad/callback' : ''
output webIdentityName string = deployWeb ? webIdentity!.name : ''
output webIdentityPrincipalId string = deployWeb ? webIdentity!.properties.principalId : ''
output webIdentityClientId string = deployWeb ? webIdentity!.properties.clientId : ''
output agentHostingMode string = containerHosting ? 'container' : 'code'
output containerRegistryName string = containerHosting ? registry!.name : ''
output containerRegistryLoginServer string = containerHosting ? registry!.properties.loginServer : ''
output containerRegistryResourceId string = containerHosting ? registry!.id : ''
output filesEnabled bool = filesEnabled
output storageAccountName string = filesEnabled ? storage.name : ''
output storageAccountUrl string = filesEnabled ? storage!.properties.primaryEndpoints.blob : ''
output inputContainer string = filesEnabled ? files.inputContainer : ''
output outputContainer string = filesEnabled ? files.outputContainer : ''
