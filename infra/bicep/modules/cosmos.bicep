param accountName string
param location string
param profile 'demo' | 'production'
param productDataPublicAccessEnabled bool
param tags object
param cosmosMaxThroughput int
param webIdentityPrincipalId string
param workerIdentityPrincipalId string
param acceptancePrincipalId string = ''
param privateEndpointsSubnetId string
param privateDnsZoneId string
param fabricEnabled bool = false

var cosmosDataContributorRoleDefinitionId = '00000000-0000-0000-0000-000000000002'

resource account 'Microsoft.DocumentDB/databaseAccounts@2025-04-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  tags: tags
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    enableAutomaticFailover: true
    publicNetworkAccess: productDataPublicAccessEnabled ? 'Enabled' : 'Disabled'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: profile == 'production'
      }
    ]
    backupPolicy: profile == 'production' ? {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous30Days'
      }
    } : {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 8
        backupStorageRedundancy: 'Geo'
      }
    }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15' = {
  parent: account
  name: 'enterprise-data-analyst'
  properties: {
    resource: {
      id: 'enterprise-data-analyst'
    }
  }
}

resource workspace 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'workspace'
  properties: {
    resource: {
      id: 'workspace'
      partitionKey: {
        paths: [
          '/tenantId'
          '/ownerObjectId'
          '/sessionId'
        ]
        kind: 'MultiHash'
        version: 2
      }
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: cosmosMaxThroughput
      }
    }
  }
}

resource auth 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'auth'
  properties: {
    resource: {
      id: 'auth'
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
      }
      defaultTtl: -1
    }
  }
}

resource runtime 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'runtime'
  properties: {
    resource: {
      id: 'runtime'
      partitionKey: {
        paths: [
          '/id'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource fabricAuth 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = if (fabricEnabled) {
  parent: database
  name: 'fabricAuth'
  properties: {
    resource: {
      id: 'fabricAuth'
      partitionKey: {
        paths: [
          '/tenantId'
          '/ownerObjectId'
        ]
        kind: 'MultiHash'
        version: 2
      }
      defaultTtl: -1
    }
  }
}

resource webCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: account
  name: guid(account.id, webIdentityPrincipalId, cosmosDataContributorRoleDefinitionId)
  properties: {
    principalId: webIdentityPrincipalId
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${cosmosDataContributorRoleDefinitionId}'
    scope: account.id
  }
}

resource workerCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: account
  name: guid(account.id, workerIdentityPrincipalId, cosmosDataContributorRoleDefinitionId)
  properties: {
    principalId: workerIdentityPrincipalId
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${cosmosDataContributorRoleDefinitionId}'
    scope: account.id
  }
}

resource acceptanceCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = if (!empty(acceptancePrincipalId)) {
  parent: account
  name: guid(account.id, acceptancePrincipalId, cosmosDataContributorRoleDefinitionId)
  properties: {
    principalId: acceptancePrincipalId
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${cosmosDataContributorRoleDefinitionId}'
    scope: account.id
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-10-01' = if (!productDataPublicAccessEnabled) {
  name: 'pe-${accountName}-sql'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'cosmos-sql'
        properties: {
          privateLinkServiceId: account.id
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (!productDataPublicAccessEnabled) {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos-sql'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output accountId string = account.id
output accountName string = account.name
output endpoint string = account.properties.documentEndpoint