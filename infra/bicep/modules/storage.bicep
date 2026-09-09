param accountName string
param location string
param productDataPublicAccessEnabled bool
param tags object
param defenderScanCapGb int
param webIdentityPrincipalId string
param workerIdentityPrincipalId string
param privateEndpointsSubnetId string
param privateDnsZoneId string

var blobDataContributorRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

var quarantineBlobTagsReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  quarantineBlobTagsReaderRole.name
)

resource storage 'Microsoft.Storage/storageAccounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: productDataPublicAccessEnabled ? 'Enabled' : 'Disabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-06-01' = {
  parent: storage
  name: 'default'
  properties: {
    changeFeed: {
      enabled: false
    }
    isVersioningEnabled: true
    deleteRetentionPolicy: {
      enabled: true
      days: 30
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

resource quarantine 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blobService
  name: 'quarantine'
  properties: {
    publicAccess: 'None'
  }
}

resource sessions 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-06-01' = {
  parent: blobService
  name: 'sessions'
  properties: {
    publicAccess: 'None'
  }
}

resource defender 'Microsoft.Security/defenderForStorageSettings@2025-07-01-preview' = {
  scope: storage
  name: 'current'
  properties: {
    isEnabled: true
    overrideSubscriptionLevelSettings: true
    malwareScanning: {
      blobScanResultsOptions: 'BlobIndexTags'
      automatedResponse: 'BlobSoftDelete'
      onUpload: {
        isEnabled: true
        capGBPerMonth: defenderScanCapGb
      }
    }
    sensitiveDataDiscovery: {
      isEnabled: false
    }
  }
}

resource quarantineBlobTagsReaderRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'eda-quarantine-blob-tags-reader')
  properties: {
    roleName: 'EDA Quarantine Blob Tags Reader (${accountName})'
    description: 'Read Defender scan result tags on quarantined uploads without changing tags or blob content.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource webQuarantineBlobTagsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(quarantine.id, webIdentityPrincipalId, quarantineBlobTagsReaderRoleDefinitionId)
  scope: quarantine
  properties: {
    principalId: webIdentityPrincipalId
    roleDefinitionId: quarantineBlobTagsReaderRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

resource webBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, webIdentityPrincipalId, blobDataContributorRoleDefinitionId)
  scope: storage
  properties: {
    principalId: webIdentityPrincipalId
    roleDefinitionId: blobDataContributorRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

resource workerBlobDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workerIdentityPrincipalId, blobDataContributorRoleDefinitionId)
  scope: storage
  properties: {
    principalId: workerIdentityPrincipalId
    roleDefinitionId: blobDataContributorRoleDefinitionId
    principalType: 'ServicePrincipal'
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-10-01' = if (!productDataPublicAccessEnabled) {
  name: 'pe-${accountName}-blob'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'storage-blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [
            'blob'
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
        name: 'storage-blob'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output accountId string = storage.id
output accountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob