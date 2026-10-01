param enabled bool
param location string
param tags object
param vaultName string
param provisioningIdentityName string
param signingCertificateName string
param cacheWrapKeyName string
param webIdentityPrincipalId string
param acceptancePrincipalId string
param forceUpdateTag string
param privateEndpointsSubnetId string
param privateDnsZoneId string
param certificateProvisioningEnabled bool
param signingCertificateReady bool
param certificateBootstrapPrincipalId string = ''

var certificateReadRoleId = guid(subscription().id, resourceGroup().id, 'eda-fabric-certificate-read')
var certificateProvisionRoleId = guid(subscription().id, resourceGroup().id, 'eda-fabric-certificate-provision')
var signingRoleId = guid(subscription().id, resourceGroup().id, 'eda-fabric-sign')
var cacheCryptoRoleId = guid(subscription().id, resourceGroup().id, 'eda-fabric-cache-crypto')
var certificateReady = certificateProvisioningEnabled || signingCertificateReady

resource certificateReadRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (enabled) {
  name: certificateReadRoleId
  properties: {
    roleName: 'EDA Fabric certificate read'
    description: 'Read public Fabric OAuth certificate metadata only.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/certificates/read'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource certificateProvisionRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (enabled) {
  name: certificateProvisionRoleId
  properties: {
    roleName: 'EDA Fabric certificate provision'
    description: 'Create the one self-signed Fabric OAuth certificate.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/certificates/create/action'
          'Microsoft.KeyVault/vaults/certificates/read'
          'Microsoft.KeyVault/vaults/keys/create/action'
          'Microsoft.KeyVault/vaults/keys/read'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource signingRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (enabled) {
  name: signingRoleId
  properties: {
    roleName: 'EDA Fabric OAuth sign'
    description: 'Sign OAuth client assertions with the certificate backing key only.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/keys/sign/action'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource cacheCryptoRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (enabled) {
  name: cacheCryptoRoleId
  properties: {
    roleName: 'EDA Fabric cache wrap and unwrap'
    description: 'Wrap and unwrap Fabric OAuth cache data-encryption keys only.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/keys/wrap/action'
          'Microsoft.KeyVault/vaults/keys/unwrap/action'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = if (enabled) {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-10-01' = if (enabled) {
  name: 'pe-${vaultName}-vault'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'key-vault'
        properties: {
          privateLinkServiceId: vault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-10-01' = if (enabled) {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'key-vault'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

resource cacheWrapKey 'Microsoft.KeyVault/vaults/keys@2024-11-01' existing = {
  parent: vault
  name: cacheWrapKeyName
}

resource provisioningIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = if (enabled && certificateProvisioningEnabled) {
  name: provisioningIdentityName
  location: location
  tags: tags
}

resource provisionCertificateAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateProvisioningEnabled) {
  name: guid(vault.id, provisioningIdentity.id, certificateProvisionRole.id)
  scope: vault
  properties: {
    principalId: provisioningIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: certificateProvisionRole.id
  }
}

resource certificateScript 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (enabled && certificateProvisioningEnabled) {
  name: 'create-${signingCertificateName}'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${provisioningIdentity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.76.0'
    cleanupPreference: 'OnSuccess'
    retentionInterval: 'P1D'
    timeout: 'PT15M'
    forceUpdateTag: forceUpdateTag
    environmentVariables: [
      {
        name: 'VAULT_NAME'
        value: vault.name
      }
      {
        name: 'CERTIFICATE_NAME'
        value: signingCertificateName
      }
      {
        name: 'CACHE_WRAP_KEY_NAME'
        value: cacheWrapKeyName
      }
    ]
    scriptContent: '''
      set -euo pipefail
      if ! az keyvault key show --vault-name "$VAULT_NAME" --name "$CACHE_WRAP_KEY_NAME" --only-show-errors >/dev/null 2>&1; then
        for attempt in $(seq 1 18); do
          if az keyvault key create --vault-name "$VAULT_NAME" --name "$CACHE_WRAP_KEY_NAME" --kty RSA --size 3072 --ops wrapKey unwrapKey --only-show-errors >/dev/null; then
            break
          fi
          sleep 10
        done
        az keyvault key show --vault-name "$VAULT_NAME" --name "$CACHE_WRAP_KEY_NAME" --only-show-errors >/dev/null
      fi
      if az keyvault certificate show --vault-name "$VAULT_NAME" --name "$CERTIFICATE_NAME" --only-show-errors >/dev/null 2>&1; then
        exit 0
      fi
      policy="$(az keyvault certificate get-default-policy --output json)"
      for attempt in $(seq 1 18); do
        if az keyvault certificate create --vault-name "$VAULT_NAME" --name "$CERTIFICATE_NAME" --policy "$policy" --only-show-errors >/dev/null; then
          exit 0
        fi
        sleep 10
      done
      exit 1
    '''
  }
  dependsOn: [
    provisionCertificateAssignment
  ]
}

resource bootstrapCertificateAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && !certificateReady && !empty(certificateBootstrapPrincipalId)) {
  name: guid(vault.id, certificateBootstrapPrincipalId, certificateProvisionRole.id)
  scope: vault
  properties: {
    principalId: certificateBootstrapPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: certificateProvisionRole.id
  }
}

resource signingKey 'Microsoft.KeyVault/vaults/keys@2024-11-01' existing = {
  parent: vault
  name: signingCertificateName
}

resource webCertificateRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateReady) {
  name: guid(vault.id, webIdentityPrincipalId, certificateReadRole.id)
  scope: vault
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: certificateReadRole.id
  }
}

resource acceptanceCertificateRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateReady) {
  name: guid(vault.id, acceptancePrincipalId, certificateReadRole.id)
  scope: vault
  properties: {
    principalId: acceptancePrincipalId
    roleDefinitionId: certificateReadRole.id
  }
}

resource webSigning 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateReady) {
  name: guid(signingKey.id, webIdentityPrincipalId, signingRole.id)
  scope: signingKey
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: signingRole.id
  }
  dependsOn: [
    certificateScript
  ]
}

resource webCacheCrypto 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && certificateReady) {
  name: guid(cacheWrapKey.id, webIdentityPrincipalId, cacheCryptoRole.id)
  scope: cacheWrapKey
  properties: {
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: cacheCryptoRole.id
  }
}

output vaultUri string = enabled ? vault!.properties.vaultUri : ''
output signingCertificateName string = enabled ? signingCertificateName : ''
output cacheWrapKeyName string = enabled ? cacheWrapKey.name : ''
