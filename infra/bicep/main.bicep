targetScope = 'subscription'

param environmentName string
param resourceGroupName string
param location string
param principalId string
param profile 'demo' | 'production' = 'demo'
param entraClientId string
param modelProfile 'gpt-5.6-terra-medium-v1' = 'gpt-5.6-terra-medium-v1'
param modelCapacity int = 25
param monthlyBudgetAmount int
param monitoringAlertsEnabled bool = false
param budgetActionGroupName string = ''
param budgetAlertEmail string = ''
@minValue(1)
param apiMinReplicas int = 1
param apiMaxReplicas int = 1
param redisSku string = 'Balanced_B0'
param redisHighAvailabilityEnabled bool = false
param cosmosAutoscaleMaxThroughput int = 4000
param cosmosContinuousBackupEnabled bool = false
param productDataPublicAccessEnabled bool = true
param logRetentionInDays int = 30
param zoneRedundancyEnabled bool = false
param defenderScanCapGb int = 100
param apiImage string = 'example.azurecr.io/eda-api@sha256:0000000000000000000000000000000000000000000000000000000000000000'
param workerImage string = 'example.azurecr.io/eda-worker@sha256:0000000000000000000000000000000000000000000000000000000000000000'
param fabricEnabled bool = false
@allowed([
  ''
  'semantic_model'
  'ontology'
])
param fabricProvider string = ''
param fabricTenantId string = ''
param fabricClientId string = ''
param fabricSemanticModelsJson string = '{}'
@secure()
param fabricOntologiesJson string = ''
param fabricSigningCertificateName string = 'fabric-oauth-signing'
param fabricCacheWrapKeyName string = 'fabric-cache-wrap'
param fabricSigningCertificateReady bool = false
param acceptancePrincipalId string = ''
param documentsEnabled bool = false
param powerBiProjectEnabled bool = false

var tags = {
  'azd-env-name': environmentName
  application: 'enterprise-data-analyst'
  environment: profile
  managedBy: 'bicep'
}
var suffix = take(uniqueString(subscription().id, environmentName), 8)
var hostedUserImpersonationRoleName = guid(subscription().id, 'eda-hosted-agent-user-identity-impersonation')
var hostedPowerBiProjectEnabled = powerBiProjectEnabled
  ? fail('Power BI Project Pack requires the retired DTS runtime and is unavailable in the Hosted Agent topology.')
  : false
var configurationHash = uniqueString(
  profile,
  principalId,
  entraClientId,
  modelProfile,
  string(modelCapacity),
  string(monthlyBudgetAmount),
  string(monitoringAlertsEnabled),
  budgetActionGroupName,
  budgetAlertEmail,
  string(apiMinReplicas),
  string(apiMaxReplicas),
  redisSku,
  string(redisHighAvailabilityEnabled),
  string(cosmosAutoscaleMaxThroughput),
  string(cosmosContinuousBackupEnabled),
  string(productDataPublicAccessEnabled),
  string(logRetentionInDays),
  string(zoneRedundancyEnabled),
  string(defenderScanCapGb),
  string(fabricEnabled),
  fabricProvider,
  fabricTenantId,
  fabricClientId,
  fabricSemanticModelsJson,
  fabricOntologiesJson,
  fabricSigningCertificateName,
  fabricCacheWrapKeyName,
  string(fabricSigningCertificateReady),
  acceptancePrincipalId,
  string(documentsEnabled),
  string(powerBiProjectEnabled)
)

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: union(tags, {
    configurationHash: configurationHash
  })
}

resource hostedUserImpersonationRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: hostedUserImpersonationRoleName
  properties: {
    roleName: 'EDA Hosted Agent User Identity Impersonation'
    description: 'Lets the trusted API isolate Hosted Agent state by opaque product user identity.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      subscription().id
    ]
  }
}

module naming 'modules/naming.bicep' = {
  name: 'naming'
  scope: resourceGroup
  params: {
    environmentName: environmentName
    suffix: suffix
  }
}

module identities 'modules/identities.bicep' = {
  name: 'identities'
  scope: resourceGroup
  params: {
    location: location
    suffix: suffix
    tags: tags
  }
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: resourceGroup
  params: {
    environmentName: environmentName
    location: location
    privateDataEndpointsEnabled: !productDataPublicAccessEnabled
    suffix: suffix
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: resourceGroup
  params: {
    environmentName: environmentName
    location: location
    logRetentionInDays: logRetentionInDays
    suffix: suffix
    tags: tags
  }
}

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: resourceGroup
  params: {
    containerRegistryName: naming.outputs.names.containerRegistry
    location: location
    tags: tags
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
    sessionInitIdentityPrincipalId: identities.outputs.sessionInitIdentityPrincipalId
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: resourceGroup
  params: {
    accountName: naming.outputs.names.cosmosAccount
    location: location
    profile: profile
    productDataPublicAccessEnabled: productDataPublicAccessEnabled
    tags: tags
    cosmosMaxThroughput: cosmosAutoscaleMaxThroughput
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
    acceptancePrincipalId: acceptancePrincipalId
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    privateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
    fabricEnabled: fabricEnabled
  }
}

module fabricAuth 'modules/fabric-auth.bicep' = if (fabricEnabled) {
  name: 'fabric-auth'
  scope: resourceGroup
  params: {
    acceptancePrincipalId: acceptancePrincipalId
    cacheWrapKeyName: fabricCacheWrapKeyName
    enabled: fabricEnabled
    forceUpdateTag: configurationHash
    location: location
    provisioningIdentityName: 'id-fabric-provision-${suffix}'
    signingCertificateName: fabricSigningCertificateName
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    privateDnsZoneId: network.outputs.keyVaultPrivateDnsZoneId
    certificateProvisioningEnabled: productDataPublicAccessEnabled
    signingCertificateReady: fabricSigningCertificateReady
    certificateBootstrapPrincipalId: !productDataPublicAccessEnabled && !fabricSigningCertificateReady
      ? identities.outputs.webIdentityPrincipalId
      : ''
    tags: tags
    vaultName: naming.outputs.names.keyVault
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: resourceGroup
  params: {
    accountName: naming.outputs.names.storageAccount
    location: location
    productDataPublicAccessEnabled: productDataPublicAccessEnabled
    tags: tags
    defenderScanCapGb: defenderScanCapGb
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    privateDnsZoneId: network.outputs.blobPrivateDnsZoneId
  }
}

module redis 'modules/redis.bicep' = {
  name: 'redis'
  scope: resourceGroup
  params: {
    redisName: naming.outputs.names.redisEnterprise
    location: location
    profile: profile
    productDataPublicAccessEnabled: productDataPublicAccessEnabled
    redisSku: redisSku
    tags: tags
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    privateDnsZoneId: network.outputs.redisPrivateDnsZoneId
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: resourceGroup
  params: {
    foundryName: naming.outputs.names.foundryAccount
    location: location
    tags: tags
    agentSubnetId: network.outputs.foundryAgentSubnetId
    webIdentityPrincipalId: identities.outputs.webIdentityPrincipalId
    workerIdentityPrincipalId: identities.outputs.workerIdentityPrincipalId
    userImpersonationRoleDefinitionId: hostedUserImpersonationRole.id
    modelProfile: modelProfile
    modelCapacity: modelCapacity
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps'
  scope: resourceGroup
  params: {
    acaSubnetId: network.outputs.acaSubnetId
    apiImage: apiImage
    apiMaxReplicas: apiMaxReplicas
    apiMinReplicas: apiMinReplicas
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    blobAccountUrl: storage.outputs.blobEndpoint
    containerRegistryLoginServer: containerRegistry.outputs.loginServer
    cosmosDatabase: 'enterprise-data-analyst'
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosWorkspaceContainer: 'workspace'
    deploymentId: configurationHash
    documentsEnabled: documentsEnabled
    entraClientId: entraClientId
    entraTenantId: tenant().tenantId
    environmentName: environmentName
    foundryModelDeployment: foundry.outputs.deploymentName
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    hostedAgentName: 'enterprise-data-analyst-long-job'
    location: location
    logAnalyticsSharedKey: monitoring.outputs.logAnalyticsSharedKey
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    modelProfile: modelProfile
    powerBiProjectEnabled: hostedPowerBiProjectEnabled
    fabricEnabled: fabricEnabled
    fabricProvider: fabricProvider
    fabricTenantId: fabricTenantId
    fabricClientId: fabricClientId
    fabricKeyVaultUrl: fabricEnabled ? fabricAuth!.outputs.vaultUri : ''
    fabricSigningCertificateName: fabricSigningCertificateName
    fabricCacheWrapKeyName: fabricCacheWrapKeyName
    fabricSemanticModelsJson: fabricSemanticModelsJson
    fabricOntologiesJson: fabricOntologiesJson
    profile: profile
    redisUrl: 'rediss://${redis.outputs.hostname}:${redis.outputs.port}/0'
    suffix: suffix
    tags: tags
    webIdentityId: identities.outputs.webIdentityId
    webIdentityClientId: identities.outputs.webIdentityClientId
    workerIdentityId: identities.outputs.workerIdentityId
    workerIdentityClientId: identities.outputs.workerIdentityClientId
    workerImage: workerImage
  }
}

module budgetsAlerts 'modules/budgets-alerts.bicep' = if (monitoringAlertsEnabled) {
  name: 'budgets-alerts'
  scope: resourceGroup
  params: {
    actionGroupName: budgetActionGroupName
    alertEmail: budgetAlertEmail
    apiAppId: containerApps.outputs.apiId
    appInsightsId: monitoring.outputs.appInsightsId
    containerAppsEnvironmentId: containerApps.outputs.environmentId
    cosmosAccountId: cosmos.outputs.accountId
    location: location
    monthlyBudgetAmount: monthlyBudgetAmount
    redisClusterId: redis.outputs.clusterId
    tags: tags
  }
}

// The static inventory contract is disabled until the later resource modules exist.
#disable-next-line no-deployments-resources
resource referenceTopology 'Microsoft.Resources/deployments@2025-04-01' = if (false) {
  name: 'reference-topology'
  location: location
  properties: {
    mode: 'Incremental'
    expressionEvaluationOptions: {
      scope: 'inner'
    }
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      resources: [
        {
          type: 'Microsoft.App/sandboxGroups'
          apiVersion: '2026-02-01-preview'
          name: 'reference-sandbox-group'
        }
      ]
    }
  }
}

output resourceGroupId string = resourceGroup.id
output modelDeploymentName string = foundry.outputs.deploymentName
output modelProfile string = foundry.outputs.modelProfile
output projectEndpoint string = foundry.outputs.projectEndpoint
output foundryResourceEndpoint string = foundry.outputs.resourceEndpoint
output appUrl string = containerApps.outputs.apiUrl
output sessionPoolPort int = 80
output webIdentityId string = identities.outputs.webIdentityId
output webIdentityClientId string = identities.outputs.webIdentityClientId
output webIdentityPrincipalId string = identities.outputs.webIdentityPrincipalId
output workerIdentityId string = identities.outputs.workerIdentityId
output workerIdentityClientId string = identities.outputs.workerIdentityClientId
output workerIdentityPrincipalId string = identities.outputs.workerIdentityPrincipalId
output sessionInitIdentityId string = identities.outputs.sessionInitIdentityId
output sessionInitIdentityClientId string = identities.outputs.sessionInitIdentityClientId
output sessionInitIdentityPrincipalId string = identities.outputs.sessionInitIdentityPrincipalId
output virtualNetworkId string = network.outputs.virtualNetworkId
output acaSubnetId string = network.outputs.acaSubnetId
output sandboxSubnetId string = network.outputs.sandboxSubnetId
output foundryAgentSubnetId string = network.outputs.foundryAgentSubnetId
output privateEndpointsSubnetId string = network.outputs.privateEndpointsSubnetId
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
output appInsightsId string = monitoring.outputs.appInsightsId
output containerRegistryId string = containerRegistry.outputs.registryId
output containerRegistryLoginServer string = containerRegistry.outputs.loginServer
output cosmosAccountId string = cosmos.outputs.accountId
output cosmosEndpoint string = cosmos.outputs.endpoint
output storageAccountId string = storage.outputs.accountId
output storageBlobEndpoint string = storage.outputs.blobEndpoint
output redisClusterId string = redis.outputs.clusterId
output redisHostname string = redis.outputs.hostname
output redisPort int = redis.outputs.port
output containerAppsEnvironmentId string = containerApps.outputs.environmentId
output apiAppId string = containerApps.outputs.apiId
output cleanupJobId string = containerApps.outputs.cleanupJobId
output fabricAcceptanceJobId string = fabricEnabled ? containerApps.outputs.fabricAcceptanceJobId : ''
output fabricVaultUrl string = fabricEnabled ? fabricAuth!.outputs.vaultUri : ''
output budgetActionGroupId string = monitoringAlertsEnabled ? budgetsAlerts!.outputs.actionGroupId : ''
output deploymentId string = configurationHash
output EDA_DEPLOYMENT_ID string = configurationHash
output AZURE_AI_ACCOUNT_NAME string = foundry.outputs.accountName
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = foundry.outputs.deploymentName
output AZURE_AI_PROJECT_ID string = foundry.outputs.projectId
output AZURE_AI_PROJECT_NAME string = foundry.outputs.projectName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = containerRegistry.outputs.registryId
output AZURE_OPENAI_ENDPOINT string = foundry.outputs.resourceEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FABRIC_KEY_VAULT_URL string = fabricEnabled ? fabricAuth!.outputs.vaultUri : ''
output AZURE_RESOURCE_GROUP string = resourceGroup.name