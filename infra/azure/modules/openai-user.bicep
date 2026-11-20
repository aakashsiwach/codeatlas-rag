// "Cognitive Services OpenAI User" for one principal on one existing Azure OpenAI resource. A module, so the
// resource can live in another resource group than the app.

@description('Name of the existing Azure OpenAI (or Azure AI Services) resource in this resource group.')
param openAiName string

@description('Object id of the managed identity that calls the model.')
param principalId string

var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiName
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, principalId, openAiUserRole)
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
