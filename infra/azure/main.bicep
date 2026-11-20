// CodeAtlas on Azure Container Apps: the API + UI image (PetClinic index and models baked in), answers from an
// existing Azure OpenAI deployment through a user-assigned managed identity. Deployed into one resource group:
//   az deployment group create -g <rg> -f infra/azure/main.bicep -p infra/azure/main.bicepparam
// See docs/deploy-azure.md for the two-phase deploy (registry first, then the app), the smoke test and teardown.

targetScope = 'resourceGroup'

// == Names and placement ==

@description('Prefix for resource names (letters, digits and hyphens).')
@minLength(3)
@maxLength(12)
param namePrefix string = 'codeatlas'

@description('Region for every resource. Defaults to the resource group\'s region.')
param location string = resourceGroup().location

// == The app ==

@description('Create the Container App. Use false for the first deployment, before the image is in the registry.')
param deployApp bool = true

@description('Tag of the codeatlas image in the registry (the git commit, see docs/deploy-azure.md). Required when deployApp is true.')
param imageTag string = ''

@description('vCPU per replica. With the Consumption profile, memory must be twice the vCPU count in GiB.')
@allowed(['1.0', '2.0', '4.0'])
param cpuCores string = '2.0'

@description('Memory per replica: 2 GiB per vCPU.')
@allowed(['2Gi', '4Gi', '8Gi'])
param memory string = '4Gi'

@description('Minimum replicas. 0 allows scale to zero (no compute charge while idle, a cold start on the next request).')
@minValue(0)
@maxValue(1)
param minReplicas int = 0

@description('Maximum replicas. Each replica has its own copy of the baked-in index, so one is enough for a demo.')
@minValue(1)
@maxValue(3)
param maxReplicas int = 1

@description('Named setup the UI selects by default (fast: vector search; quality: hybrid + reranker + graph).')
@allowed(['fast', 'quality'])
param defaultSetup string = 'fast'

@description('CODEATLAS_LLM_PRICES as an object, e.g. {"azure_openai:<deployment>": {"input_per_mtok": 0, "output_per_mtok": 0}}. Empty: costs show as unpriced.')
param llmPrices object = {}

@description('CodeAtlas API token (bearer token for /api/*). Stored in Key Vault; the app reads it as a Key Vault secret reference.')
@secure()
@minLength(32)
param apiToken string

// == Azure OpenAI (existing resource) ==

@description('Name of an existing Azure OpenAI (or Azure AI Services) resource.')
param openAiName string

@description('Resource group of that resource. Defaults to this resource group.')
param openAiResourceGroup string = resourceGroup().name

@description('Name of the chat model deployment in that resource.')
param openAiDeployment string

@description('Azure OpenAI data-plane API version.')
param openAiApiVersion string = '2024-10-21'

// == Cost control ==

@description('Email address for the budget alerts.')
param budgetAlertEmail string

@description('Monthly budget for this resource group, in the billing currency.')
param budgetAmount int = 10

@description('First day of the budget period. Defaults to the first day of the current month.')
param budgetStartDate string = utcNow('yyyy-MM-01')

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

@description('Log Analytics daily ingestion cap in GB, so a log storm cannot run up a bill.')
param logDailyQuotaGb int = 1

var suffix = uniqueString(resourceGroup().id)
var acrName = take('${replace(namePrefix, '-', '')}${suffix}', 50)
var keyVaultName = take('${take(namePrefix, 9)}-kv-${suffix}', 24)
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
    workspaceCapping: {
      dailyQuotaGb: logDailyQuotaGb
    }
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-identity'
  location: location
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false // pulls use the managed identity, pushes use `az acr login` (Entra ID)
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    softDeleteRetentionInDays: 7 // the minimum; purge it after teardown to reuse the name at once
  }
}

resource apiTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'codeatlas-api-token'
  properties: {
    value: apiToken
    contentType: 'CodeAtlas API bearer token'
  }
}

// Read access to this one secret only, not to the whole vault.
resource secretRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(apiTokenSecret.id, identity.id, roles.keyVaultSecretsUser)
  scope: apiTokenSecret
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiName
  scope: resourceGroup(openAiResourceGroup)
}

// "Cognitive Services OpenAI User" on the Azure OpenAI resource: call deployments, nothing else.
module openAiAccess 'modules/openai-user.bicep' = {
  name: 'codeatlas-openai-user'
  scope: resourceGroup(openAiResourceGroup)
  params: {
    openAiName: openAiName
    principalId: identity.properties.principalId
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: namePrefix
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false // HTTP is redirected to HTTPS: the UI sends the API token on every request
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'api-token'
          keyVaultUrl: apiTokenSecret.properties.secretUri // versionless: picks up a rotated token
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'codeatlas'
          image: '${registry.properties.loginServer}/codeatlas:${imageTag}'
          resources: {
            cpu: json(cpuCores)
            memory: memory
          }
          env: [
            { name: 'CODEATLAS_API_TOKEN', secretRef: 'api-token' }
            { name: 'CODEATLAS_LLM_PROVIDER', value: 'azure_openai' }
            { name: 'CODEATLAS_AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'CODEATLAS_AZURE_OPENAI_DEPLOYMENT', value: openAiDeployment }
            { name: 'CODEATLAS_AZURE_OPENAI_API_VERSION', value: openAiApiVersion }
            { name: 'CODEATLAS_AZURE_OPENAI_AUTH', value: 'entra' } // managed identity only; no key in the cloud
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential: use this identity
            { name: 'CODEATLAS_DEFAULT_SETUP', value: defaultSetup }
            { name: 'CODEATLAS_LLM_PRICES', value: string(llmPrices) }
          ]
          probes: [
            {
              type: 'Startup' // the embedding model loads before /health answers
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 20
              periodSeconds: 15
              failureThreshold: 10
              timeoutSeconds: 5
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
              failureThreshold: 3
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPull
    secretRead
    openAiAccess
  ]
}

// Covers this resource group only. Azure OpenAI token charges are billed to the Azure OpenAI resource's group.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${namePrefix}-monthly'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [budgetAlertEmail]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [budgetAlertEmail]
      }
    }
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output keyVaultName string = vault.name
output identityPrincipalId string = identity.properties.principalId // to remove its Azure OpenAI role at teardown
output appName string = deployApp ? app.name : ''
output url string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
