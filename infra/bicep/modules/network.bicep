param environmentName string
param location string
param privateDataEndpointsEnabled bool
param suffix string
param tags object

var virtualNetworkName = 'vnet-eda-${environmentName}-${suffix}'

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-10-01' = {
  name: virtualNetworkName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/16'
      ]
    }
  }
}

resource acaSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-10-01' = {
  parent: virtualNetwork
  name: 'aca'
  properties: {
    addressPrefix: '10.42.0.0/23'
    delegations: [
      {
        name: 'container-apps-environment'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource privateEndpointsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-10-01' = {
  parent: virtualNetwork
  name: 'private-endpoints'
  properties: {
    addressPrefix: '10.42.2.0/24'
    privateEndpointNetworkPolicies: 'Disabled'
  }
  dependsOn: [
    acaSubnet
  ]
}

resource sandboxSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-10-01' = {
  parent: virtualNetwork
  name: 'sandboxes'
  properties: {
    addressPrefix: '10.42.4.0/23'
    delegations: [
      {
        name: 'sandbox-groups'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
  dependsOn: [
    privateEndpointsSubnet
  ]
}

resource foundryAgentSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-10-01' = {
  parent: virtualNetwork
  name: 'foundry-agents-v2'
  properties: {
    addressPrefix: '10.42.7.0/24'
    delegations: [
      {
        name: 'foundry-agent-environment'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
  dependsOn: [
    sandboxSubnet
  ]
}

resource blobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (privateDataEndpointsEnabled) {
  #disable-next-line no-hardcoded-env-urls
  name: 'privatelink.blob.core.windows.net'
  location: 'global'
  tags: tags
}

resource cosmosPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (privateDataEndpointsEnabled) {
  #disable-next-line no-hardcoded-env-urls
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}

resource redisPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (privateDataEndpointsEnabled) {
  #disable-next-line no-hardcoded-env-urls
  name: 'privatelink.redis.azure.net'
  location: 'global'
  tags: tags
}

resource keyVaultPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (privateDataEndpointsEnabled) {
  #disable-next-line no-hardcoded-env-urls
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource blobPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (privateDataEndpointsEnabled) {
  parent: blobPrivateDnsZone
  name: virtualNetworkName
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource cosmosPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (privateDataEndpointsEnabled) {
  parent: cosmosPrivateDnsZone
  name: virtualNetworkName
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource redisPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (privateDataEndpointsEnabled) {
  parent: redisPrivateDnsZone
  name: virtualNetworkName
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource keyVaultPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (privateDataEndpointsEnabled) {
  parent: keyVaultPrivateDnsZone
  name: virtualNetworkName
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

output virtualNetworkId string = virtualNetwork.id
output acaSubnetId string = acaSubnet.id
output sandboxSubnetId string = sandboxSubnet.id
output foundryAgentSubnetId string = foundryAgentSubnet.id
output privateEndpointsSubnetId string = privateEndpointsSubnet.id
output blobPrivateDnsZoneId string = privateDataEndpointsEnabled ? blobPrivateDnsZone.id : ''
output cosmosPrivateDnsZoneId string = privateDataEndpointsEnabled ? cosmosPrivateDnsZone.id : ''
output redisPrivateDnsZoneId string = privateDataEndpointsEnabled ? redisPrivateDnsZone.id : ''
output keyVaultPrivateDnsZoneId string = privateDataEndpointsEnabled ? keyVaultPrivateDnsZone.id : ''