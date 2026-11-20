// Parameters for main.bicep. Everything personal or secret comes from environment variables, so this file never
// holds a token, an email address or an id. Set them before deploying (docs/deploy-azure.md, step 1):
//   CODEATLAS_API_TOKEN      the API bearer token (32+ characters); stored in Key Vault by the deployment
//   BUDGET_ALERT_EMAIL       where the budget alerts go
//   AZURE_OPENAI_NAME        name of your existing Azure OpenAI resource
//   AZURE_OPENAI_RG          its resource group
//   AZURE_OPENAI_DEPLOYMENT  the chat model deployment in it
//   DEPLOY_APP               false for the first run (registry only), true once the image is pushed
//   IMAGE_TAG                the image tag pushed to the registry
//   MIN_REPLICAS             optional: 1 keeps a replica warm (no cold starts) during a demo; default 0
using 'main.bicep'

param apiToken = readEnvironmentVariable('CODEATLAS_API_TOKEN')
param budgetAlertEmail = readEnvironmentVariable('BUDGET_ALERT_EMAIL')
param openAiName = readEnvironmentVariable('AZURE_OPENAI_NAME')
param openAiResourceGroup = readEnvironmentVariable('AZURE_OPENAI_RG')
param openAiDeployment = readEnvironmentVariable('AZURE_OPENAI_DEPLOYMENT')
param deployApp = bool(readEnvironmentVariable('DEPLOY_APP', 'false'))
param imageTag = readEnvironmentVariable('IMAGE_TAG', '')
param minReplicas = int(readEnvironmentVariable('MIN_REPLICAS', '0'))
