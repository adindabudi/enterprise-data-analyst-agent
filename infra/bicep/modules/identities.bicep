param location string
param suffix string
param tags object

#disable-next-line use-recent-api-versions
resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-eda-web-${suffix}'
  location: location
  tags: tags
}

#disable-next-line use-recent-api-versions
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-eda-worker-${suffix}'
  location: location
  tags: tags
}

#disable-next-line use-recent-api-versions
resource sessionInitIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-eda-session-init-${suffix}'
  location: location
  tags: tags
}

output webIdentityId string = webIdentity.id
output webIdentityClientId string = webIdentity.properties.clientId
output webIdentityPrincipalId string = webIdentity.properties.principalId
output workerIdentityId string = workerIdentity.id
output workerIdentityClientId string = workerIdentity.properties.clientId
output workerIdentityPrincipalId string = workerIdentity.properties.principalId
output sessionInitIdentityId string = sessionInitIdentity.id
output sessionInitIdentityClientId string = sessionInitIdentity.properties.clientId
output sessionInitIdentityPrincipalId string = sessionInitIdentity.properties.principalId