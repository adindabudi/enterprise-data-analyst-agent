using '../main.bicep'

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'production')
param resourceGroupName = readEnvironmentVariable('AZURE_RESOURCE_GROUP', 'rg-eda-production-sea')
param location = readEnvironmentVariable('AZURE_LOCATION', 'southeastasia')
param principalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param entraClientId = readEnvironmentVariable('AZURE_ENTRA_CLIENT_ID', '')
param modelProfile = readEnvironmentVariable('EDA_MODEL_PROFILE', 'gpt-5.6-terra-medium-v1')
param monthlyBudgetAmount = 1000
param monitoringAlertsEnabled = false
param profile = 'production'
param apiMinReplicas = 3
param apiMaxReplicas = 10
param redisSku = 'Balanced_B10'
param redisHighAvailabilityEnabled = true
param cosmosAutoscaleMaxThroughput = 4000
param cosmosContinuousBackupEnabled = true
param productDataPublicAccessEnabled = false
param logRetentionInDays = 90
param zoneRedundancyEnabled = true
param defenderScanCapGb = 1000
param fabricEnabled = readEnvironmentVariable('FABRIC_ENABLED', 'false') == 'true'
param fabricProvider = readEnvironmentVariable('FABRIC_PROVIDER', '')
param fabricTenantId = readEnvironmentVariable('FABRIC_TENANT_ID', '')
param fabricClientId = readEnvironmentVariable('FABRIC_CLIENT_ID', '')
param fabricOntologiesJson = readEnvironmentVariable('FABRIC_ONTOLOGIES_JSON', '{}')
param acceptancePrincipalId = readEnvironmentVariable('FABRIC_ACCEPTANCE_PRINCIPAL_ID', '')
param documentsEnabled = readEnvironmentVariable('DOCUMENTS_ENABLED', 'false') == 'true'
param powerBiProjectEnabled = readEnvironmentVariable('POWERBI_PROJECT_ENABLED', 'false') == 'true'