param foundryName string
param location string
param tags object
param webIdentityPrincipalId string
param workerIdentityPrincipalId string
param userImpersonationRoleDefinitionId string
param agentSubnetId string
param modelProfile 'gpt-5.6-terra-medium-v1'
param modelCapacity int

var projectName = 'eda-project'
var deploymentName = 'gpt-5.6-terra'
var foundryAgentConsumerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'
)
var foundryUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
)

#disable-next-line use-recent-api-versions
resource account 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: union(tags, {
    modelProfile: modelProfile
  })
  properties: {
    customSubDomainName: foundryName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ]
  }
}

#disable-next-line use-recent-api-versions
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource projectFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, foundryUserRoleDefinitionId)
  scope: account
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRoleDefinitionId
  }
}

#disable-next-line use-recent-api-versions
resource terra 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.6-terra'
      version: '2026-07-09'
    }
    versionUpgradeOption: 'NoAutoUpgrade'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    project
  ]
}

resource workerCognitiveServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, workerIdentityPrincipalId, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: account
  properties: {
    principalId: workerIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'a97b65f3-24c7-4388-baec-2e87135dc908'
    )
  }
}

resource webCognitiveServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, webIdentityPrincipalId, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: account
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'a97b65f3-24c7-4388-baec-2e87135dc908'
    )
  }
}

resource webFoundryAgentConsumer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, webIdentityPrincipalId, foundryAgentConsumerRoleDefinitionId)
  scope: project
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryAgentConsumerRoleDefinitionId
  }
}

resource webHostedUserImpersonation 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, webIdentityPrincipalId, userImpersonationRoleDefinitionId)
  scope: project
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: userImpersonationRoleDefinitionId
  }
}

output accountId string = account.id
output accountName string = account.name
output projectId string = project.id
output projectName string = project.name
output projectPrincipalId string = project.identity.principalId
output projectEndpoint string = 'https://${foundryName}.services.ai.azure.com/api/projects/${projectName}'
output resourceEndpoint string = 'https://${foundryName}.openai.azure.com'
output deploymentName string = deploymentName
output modelProfile string = modelProfile