param containerRegistryName string
param location string
param tags object
param webIdentityPrincipalId string
param workerIdentityPrincipalId string
param sessionInitIdentityPrincipalId string

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource webAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webIdentityPrincipalId, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: webIdentityPrincipalId
    roleDefinitionId: acrPullRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

resource workerAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, workerIdentityPrincipalId, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: workerIdentityPrincipalId
    roleDefinitionId: acrPullRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

resource sessionInitAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, sessionInitIdentityPrincipalId, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: sessionInitIdentityPrincipalId
    roleDefinitionId: acrPullRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

output registryId string = registry.id
output loginServer string = registry.properties.loginServer
