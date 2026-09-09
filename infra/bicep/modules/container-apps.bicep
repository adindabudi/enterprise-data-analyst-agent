param apiImage string
param apiMaxReplicas int
param apiMinReplicas int
@secure()
param appInsightsConnectionString string
param acaSubnetId string
param blobAccountUrl string
param containerRegistryLoginServer string
param cosmosDatabase string
param cosmosEndpoint string
param cosmosWorkspaceContainer string
param entraClientId string
param entraTenantId string
param environmentName string
param foundryModelDeployment string
param foundryProjectEndpoint string
param hostedAgentName string
param deploymentId string
param documentsEnabled bool
param fabricEnabled bool
@allowed([
  ''
  'semantic_model'
  'ontology'
])
param fabricProvider string
param fabricTenantId string
param fabricClientId string
param fabricKeyVaultUrl string
param fabricSigningCertificateName string
param fabricCacheWrapKeyName string
param fabricSemanticModelsJson string
@secure()
param fabricOntologiesJson string
param location string
@secure()
param logAnalyticsSharedKey string
param logAnalyticsWorkspaceId string
param modelProfile 'gpt-5.6-terra-medium-v1'
param powerBiProjectEnabled bool
param profile 'demo' | 'production'
param redisUrl string
param suffix string
param tags object
param webIdentityId string
param webIdentityClientId string
param workerIdentityId string
param workerIdentityClientId string
param workerImage string

var environmentNameForResource = 'cae-eda-${environmentName}-${suffix}'
var apiName = 'api-eda-${environmentName}-${suffix}'
var cleanupName = 'cleanup-eda-${environmentName}-${suffix}'
var fabricAcceptanceName = 'fabric-acc-${environmentName}-${suffix}'
var apiOrigin = 'https://${apiName}.${environment.properties.defaultDomain}'

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: environmentNameForResource
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
    }
    zoneRedundant: profile == 'production'
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource api 'Microsoft.App/containerApps@2026-01-01' = {
  name: apiName
  location: location
  tags: union(tags, {
    'azd-service-name': 'api'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${webIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistryLoginServer
          identity: webIdentityId
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        stickySessions: {
          affinity: 'none'
        }
      }
    }
    template: {
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
      }
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: concat([
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'EDA_APP_ENV'
              value: 'production'
            }
            {
              name: 'EDA_MANAGED_IDENTITY_CLIENT_ID'
              value: webIdentityClientId
            }
            {
              name: 'EDA_COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'EDA_COSMOS_DATABASE'
              value: cosmosDatabase
            }
            {
              name: 'EDA_COSMOS_WORKSPACE_CONTAINER'
              value: cosmosWorkspaceContainer
            }
            {
              name: 'EDA_COSMOS_AUTH_CONTAINER'
              value: 'auth'
            }
            {
              name: 'EDA_COSMOS_RUNTIME_CONTAINER'
              value: 'runtime'
            }
            {
              name: 'EDA_BLOB_ACCOUNT_URL'
              value: blobAccountUrl
            }
            {
              name: 'EDA_BLOB_QUARANTINE_CONTAINER'
              value: 'quarantine'
            }
            {
              name: 'EDA_BLOB_SESSIONS_CONTAINER'
              value: 'sessions'
            }
            {
              name: 'EDA_REDIS_URL'
              value: redisUrl
            }
            {
              name: 'EDA_FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'EDA_FOUNDRY_MODEL_DEPLOYMENT'
              value: foundryModelDeployment
            }
            {
              name: 'EDA_HOSTED_AGENT_NAME'
              value: hostedAgentName
            }
            {
              name: 'EDA_HOSTED_AGENT_ENABLED'
              value: 'true'
            }
            {
              name: 'EDA_MODEL_PROFILE'
              value: modelProfile
            }
            {
              name: 'EDA_FOUNDRY_HOSTING'
              value: 'azure'
            }
            {
              name: 'EDA_PUBLIC_ORIGIN'
              value: apiOrigin
            }
            {
              name: 'EDA_ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'EDA_ENTRA_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'EDA_COOKIE_SECURE'
              value: 'true'
            }
            {
              name: 'EDA_DEPLOYMENT_ID'
              value: deploymentId
            }
            {
              name: 'EDA_WORKER_IMAGE_DIGEST'
              value: last(split(workerImage, '@'))
            }
            {
              name: 'EDA_COSMOS_FABRIC_AUTH_CONTAINER'
              value: 'fabricAuth'
            }
            {
              name: 'DOCUMENTS_ENABLED'
              value: string(documentsEnabled)
            }
            {
              name: 'POWERBI_PROJECT_ENABLED'
              value: string(powerBiProjectEnabled)
            }
          ], fabricEnabled ? concat([
            {
              name: 'FABRIC_ENABLED'
              value: 'true'
            }
            {
              name: 'FABRIC_PROVIDER'
              value: fabricProvider
            }
            {
              name: 'FABRIC_TENANT_ID'
              value: fabricTenantId
            }
            {
              name: 'FABRIC_CLIENT_ID'
              value: fabricClientId
            }
            {
              name: 'FABRIC_KEY_VAULT_URL'
              value: fabricKeyVaultUrl
            }
            {
              name: 'FABRIC_SIGNING_CERTIFICATE_NAME'
              value: fabricSigningCertificateName
            }
            {
              name: 'FABRIC_CACHE_WRAP_KEY_NAME'
              value: fabricCacheWrapKeyName
            }
          ], fabricProvider == 'ontology' ? [
            {
              name: 'FABRIC_ONTOLOGIES_JSON'
              value: fabricOntologiesJson
            }
          ] : []) : [])
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/health/platform-ready'
                port: 8000
              }
              periodSeconds: 5
              failureThreshold: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/platform-ready'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
    }
  }
}

resource cleanup 'Microsoft.App/jobs@2026-01-01' = {
  name: cleanupName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workerIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 2
      scheduleTriggerConfig: {
        cronExpression: '0 2 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: containerRegistryLoginServer
          identity: workerIdentityId
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'cleanup'
          image: workerImage
          command: [
            'eda-worker'
            'cleanup'
            '--before'
            'now'
            '--limit'
            '100'
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'EDA_MANAGED_IDENTITY_CLIENT_ID'
              value: workerIdentityClientId
            }
            {
              name: 'EDA_COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'EDA_COSMOS_DATABASE'
              value: cosmosDatabase
            }
            {
              name: 'EDA_COSMOS_WORKSPACE_CONTAINER'
              value: cosmosWorkspaceContainer
            }
            {
              name: 'EDA_BLOB_ACCOUNT_URL'
              value: blobAccountUrl
            }
            {
              name: 'EDA_BLOB_SESSIONS_CONTAINER'
              value: 'sessions'
            }
          ]
        }
      ]
    }
  }
}

resource fabricAcceptance 'Microsoft.App/jobs@2026-01-01' = if (fabricEnabled) {
  name: fabricAcceptanceName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workerIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: containerRegistryLoginServer
          identity: workerIdentityId
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'fabric-acceptance'
          image: workerImage
          command: [
            'eda-worker'
            'accept-fabric'
            '--provider'
            fabricProvider
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: concat([
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'FABRIC_ACCEPTANCE_MODE'
              value: 'true'
            }
            {
              name: 'EDA_APP_ENV'
              value: 'production'
            }
            {
              name: 'EDA_DEPLOYMENT_ID'
              value: deploymentId
            }
            {
              name: 'EDA_MANAGED_IDENTITY_CLIENT_ID'
              value: workerIdentityClientId
            }
            {
              name: 'EDA_COSMOS_ENDPOINT'
              value: cosmosEndpoint
            }
            {
              name: 'EDA_COSMOS_DATABASE'
              value: cosmosDatabase
            }
            {
              name: 'EDA_COSMOS_WORKSPACE_CONTAINER'
              value: cosmosWorkspaceContainer
            }
            {
              name: 'EDA_COSMOS_AUTH_CONTAINER'
              value: 'auth'
            }
            {
              name: 'EDA_COSMOS_RUNTIME_CONTAINER'
              value: 'runtime'
            }
            {
              name: 'EDA_COSMOS_FABRIC_AUTH_CONTAINER'
              value: 'fabricAuth'
            }
            {
              name: 'EDA_BLOB_ACCOUNT_URL'
              value: blobAccountUrl
            }
            {
              name: 'EDA_BLOB_SESSIONS_CONTAINER'
              value: 'sessions'
            }
            {
              name: 'EDA_REDIS_URL'
              value: redisUrl
            }
            {
              name: 'EDA_FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'EDA_FOUNDRY_MODEL_DEPLOYMENT'
              value: foundryModelDeployment
            }
            {
              name: 'EDA_MODEL_PROFILE'
              value: modelProfile
            }
            {
              name: 'EDA_FOUNDRY_HOSTING'
              value: 'azure'
            }
            {
              name: 'FABRIC_ENABLED'
              value: 'true'
            }
            {
              name: 'FABRIC_PROVIDER'
              value: fabricProvider
            }
            {
              name: 'FABRIC_TENANT_ID'
              value: fabricTenantId
            }
            {
              name: 'FABRIC_CLIENT_ID'
              value: fabricClientId
            }
            {
              name: 'FABRIC_KEY_VAULT_URL'
              value: fabricKeyVaultUrl
            }
            {
              name: 'FABRIC_SIGNING_CERTIFICATE_NAME'
              value: fabricSigningCertificateName
            }
            {
              name: 'FABRIC_CACHE_WRAP_KEY_NAME'
              value: fabricCacheWrapKeyName
            }
          ], fabricProvider == 'semantic_model' ? [
            {
              name: 'FABRIC_SEMANTIC_MODELS_JSON'
              value: fabricSemanticModelsJson
            }
          ] : [
            {
              name: 'FABRIC_ONTOLOGIES_JSON'
              value: fabricOntologiesJson
            }
          ])
        }
      ]
    }
  }
}

output environmentId string = environment.id
output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output apiId string = api.id
output cleanupJobId string = cleanup.id
output fabricAcceptanceJobId string = fabricEnabled ? fabricAcceptance!.id : ''