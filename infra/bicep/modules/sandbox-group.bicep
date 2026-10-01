targetScope = 'resourceGroup'

param location string
param sandboxGroupName string
param sandboxSubnetId string
param provisioningPrincipalId string
param sessionInitIdentityResourceId string
param tags object
param webIdentityPrincipalId string

var sandboxDataOwnerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'c24cf47c-5077-412d-a19c-45202126392c'
)

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' = {
  name: sandboxGroupName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sessionInitIdentityResourceId}': {}
    }
  }
  properties: {
    defaultCpu: '2'
    defaultMemory: '4Gi'
    defaultDisk: '40Gi'
    maxSandboxCount: 10
    defaultTimeoutSeconds: 300
  }
}

resource vnetConnection 'Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview' = {
  parent: sandboxGroup
  name: 'default'
  location: location
  properties: {
    subnetId: sandboxSubnetId
  }
}

resource provisioningSandboxDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, provisioningPrincipalId, sandboxDataOwnerRoleDefinitionId)
  scope: sandboxGroup
  properties: {
    principalId: provisioningPrincipalId
    roleDefinitionId: sandboxDataOwnerRoleDefinitionId
  }
}

resource webSandboxDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, webIdentityPrincipalId, sandboxDataOwnerRoleDefinitionId)
  scope: sandboxGroup
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: sandboxDataOwnerRoleDefinitionId
  }
}

output sandboxGroupId string = sandboxGroup.id
output sandboxGroupName string = sandboxGroup.name
