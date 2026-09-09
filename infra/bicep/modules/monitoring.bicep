param environmentName string
param location string
param logRetentionInDays int
param suffix string
param tags object

var workspaceName = 'law-eda-${environmentName}-${suffix}'
var appInsightsName = 'appi-eda-${environmentName}-${suffix}'

#disable-next-line use-recent-api-versions
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableIpMasking: true
  }
}

output workspaceId string = workspace.id
output logAnalyticsWorkspaceId string = workspace.properties.customerId
@secure()
output logAnalyticsSharedKey string = workspace.listKeys().primarySharedKey
output appInsightsId string = appInsights.id
@secure()
output appInsightsConnectionString string = appInsights.properties.ConnectionString