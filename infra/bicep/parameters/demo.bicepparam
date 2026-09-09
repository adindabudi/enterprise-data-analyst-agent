using '../main.bicep'

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'dev-sea')
param resourceGroupName = readEnvironmentVariable('AZURE_RESOURCE_GROUP', 'rg-eda-dev-sea')
param location = readEnvironmentVariable('AZURE_LOCATION', 'southeastasia')
param principalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param entraClientId = readEnvironmentVariable('AZURE_ENTRA_CLIENT_ID', '')
param modelProfile = readEnvironmentVariable('EDA_MODEL_PROFILE', 'gpt-5.6-terra-medium-v1')
param monthlyBudgetAmount = 100
param monitoringAlertsEnabled = false
param profile = 'demo'
param apiMinReplicas = 0
param apiMaxReplicas = 1
param redisSku = 'Balanced_B0'
param redisHighAvailabilityEnabled = false
param cosmosAutoscaleMaxThroughput = 4000
param cosmosContinuousBackupEnabled = false
param productDataPublicAccessEnabled = true
param logRetentionInDays = 30
param zoneRedundancyEnabled = false
param defenderScanCapGb = 100
param fabricEnabled = readEnvironmentVariable('FABRIC_ENABLED', 'false') == 'true'
param fabricProvider = readEnvironmentVariable('FABRIC_PROVIDER', '')
param fabricTenantId = readEnvironmentVariable('FABRIC_TENANT_ID', '')
param fabricClientId = readEnvironmentVariable('FABRIC_CLIENT_ID', '')
param fabricSemanticModelsJson = readEnvironmentVariable('FABRIC_SEMANTIC_MODELS_JSON', '{}')
param fabricOntologiesJson = readEnvironmentVariable('FABRIC_ONTOLOGIES_JSON', '{}')
param acceptancePrincipalId = readEnvironmentVariable('FABRIC_ACCEPTANCE_PRINCIPAL_ID', '')
param documentsEnabled = readEnvironmentVariable('DOCUMENTS_ENABLED', 'false') == 'true'
param powerBiProjectEnabled = readEnvironmentVariable('POWERBI_PROJECT_ENABLED', 'false') == 'true'