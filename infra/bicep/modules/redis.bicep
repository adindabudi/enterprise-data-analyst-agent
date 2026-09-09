param redisName string
param location string
param profile 'demo' | 'production'
param productDataPublicAccessEnabled bool
param redisSku string
param tags object
param webIdentityPrincipalId string
param workerIdentityPrincipalId string
param privateEndpointsSubnetId string
param privateDnsZoneId string

resource cluster 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: redisName
  location: location
  sku: {
    name: redisSku
  }
  tags: tags
  properties: {
    encryption: {}
    highAvailability: profile == 'production' ? 'Enabled' : 'Disabled'
    minimumTlsVersion: '1.2'
    publicNetworkAccess: productDataPublicAccessEnabled ? 'Enabled' : 'Disabled'
  }
}

resource database 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: cluster
  name: 'default'
  properties: {
    accessKeysAuthentication: 'Disabled'
    clientProtocol: 'Encrypted'
    clusteringPolicy: 'OSSCluster'
    evictionPolicy: 'VolatileLRU'
    port: 10000
  }
}

resource webAccessPolicyAssignment 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01' = {
  parent: database
  name: 'web'
  properties: {
    accessPolicyName: 'default'
    user: {
      objectId: webIdentityPrincipalId
    }
  }
}

resource workerAccessPolicyAssignment 'Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments@2025-07-01' = {
  parent: database
  name: 'worker'
  properties: {
    accessPolicyName: 'default'
    user: {
      objectId: workerIdentityPrincipalId
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-10-01' = if (!productDataPublicAccessEnabled) {
  name: 'pe-${redisName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'redis'
        properties: {
          privateLinkServiceId: cluster.id
          groupIds: [
            'redisEnterprise'
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
        name: 'redis'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output clusterId string = cluster.id
output hostname string = '${redisName}.${location}.redis.azure.net'
output port int = 10000