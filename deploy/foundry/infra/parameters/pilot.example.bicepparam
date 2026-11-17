// Copie este archivo a infra/parameters/pilot.bicepparam (ignorado por git) y edítelo.
// Contiene identificadores, nunca secretos. Los GUID en cero bloquean los scripts.
// Todos los valores son del ENTORNO DESTINO: no reutilice identificadores de otro entorno o proyecto.
// [PENDIENTE] marca los datos que hay que obtener antes de publicar (ver CONFIGURATION.md).
using '../main.bicep'

param subscriptionId = '00000000-0000-0000-0000-000000000000' // [PENDIENTE] suscripción destino
param tenantId = '00000000-0000-0000-0000-000000000000' // [PENDIENTE] tenant Entra (el mismo del Azure OpenAI y de Azure SQL)
param resourceGroupName = 'rg-t2sql-pilot' // [PENDIENTE] grupo de recursos nuevo (se crea si no existe)
param location = 'eastus2' // [PENDIENTE] región con Hosted Agents

// Determina nombres globales: <prefix>-foundry, <prefix>-project, <prefix>-web.azurewebsites.net.
// [PENDIENTE] elegir uno libre (3-12 minúsculas/números); preview-infra.ps1 lo comprueba.
param prefix = 'starter01'
param agentName = 'starter01-agent'

// Quien ejecuta los scripts de publicación. Recibe Foundry Project Manager, Foundry Agent Consumer
// (invocación de prueba por API) y AcrPush en el registro.
param publisherObjectId = '00000000-0000-0000-0000-000000000000' // [PENDIENTE] Object ID del operador
param publisherPrincipalType = 'User'

// Modelo: se reutiliza el Azure OpenAI existente (decisión tomada). Bicep no crea ningún despliegue;
// MODEL_ENDPOINT, MODEL_DEPLOYMENT_NAME y MODEL_RESOURCE_ID van en agent.env.
param model = {
  deploy: false
}

// Alojamiento por contenedor: misma imagen que se prueba en local (docker/agent.Dockerfile, ODBC Driver 18).
// Crea un Azure Container Registry Basic sin usuario administrador.
param agentHosting = {
  mode: 'container'
  registryName: 'starter01acr' // [PENDIENTE] único global, 5-50 letras y números
  registrySku: 'Basic'
}

// Web (Streamlit) separada del agente. deploy=false: el agente se consume solo por API y no hacen falta
// el registro Entra ni los usuarios. Para publicarla después: deploy=true, complete authClientId y
// allowedUserObjectIds, y repita deploy-infra.ps1, configure-auth.ps1 y publish-web.ps1.
param web = {
  deploy: false
  sku: 'B1'
  pythonVersion: '3.13'
  title: 'Text-to-SQL · pruebas'
  exposeHealthProbe: true
}

// Solo con web.deploy=true. Application (client) ID del registro Entra de un solo tenant (no el Object ID).
param authClientId = '00000000-0000-0000-0000-000000000000'

// Solo con web.deploy=true. Object ID (Entra ID > Users > Object ID) de los usuarios de la página.
param allowedUserObjectIds = [
  '00000000-0000-0000-0000-000000000000'
]

// enabled=true crea Blob privado con contenedores de entrada y salida (el agente adjunta resultado.json).
param files = {
  enabled: true
  storageAccountName: 'starter01files' // [PENDIENTE] único global, 3-24 minúsculas/números
  inputContainer: 'inputs'
  outputContainer: 'outputs'
  grantPublisherInputWrite: true
}

param tags = {
  workload: 'text2sql-foundry'
  environment: 'pilot'
  dataClassification: 'confidential'
}
