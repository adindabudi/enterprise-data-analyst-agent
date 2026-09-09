param actionGroupName string
param alertEmail string
param apiAppId string
param appInsightsId string
param containerAppsEnvironmentId string
param cosmosAccountId string
param location string
param monthlyBudgetAmount int
param redisClusterId string
param tags object

var runbookBaseUrl = 'https://github.com/Azure/enterprise-data-analyst/tree/main/docs/runbooks'

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    groupShortName: take(actionGroupName, 12)
    emailReceivers: [
      {
        name: 'operations'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource monthlyBudget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: 'monthly-operations'
  properties: {
    category: 'Cost'
    amount: monthlyBudgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '2026-01-01T00:00:00Z'
      endDate: '2036-01-01T00:00:00Z'
    }
    notifications: {
      actual50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactGroups: [
          actionGroup.id
        ]
        contactEmails: [
          alertEmail
        ]
      }
      actual75: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 75
        contactGroups: [
          actionGroup.id
        ]
        contactEmails: [
          alertEmail
        ]
      }
      actual90: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 90
        contactGroups: [
          actionGroup.id
        ]
        contactEmails: [
          alertEmail
        ]
      }
      actual100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactGroups: [
          actionGroup.id
        ]
        contactEmails: [
          alertEmail
        ]
      }
      forecasted90: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 90
        thresholdType: 'Forecasted'
        contactGroups: [
          actionGroup.id
        ]
        contactEmails: [
          alertEmail
        ]
      }
    }
  }
}

resource api5xx 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'api-5xx'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  properties: {
    description: 'API server failures exceeded the service objective.'
    severity: 2
    enabled: true
    scopes: [
      apiAppId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'server-errors'
          metricName: 'Requests'
          metricNamespace: 'microsoft.app/containerapps'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Count'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'StatusCode'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource apiP95 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: 'api-p95'
  location: location
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  kind: 'LogAlert'
  properties: {
    displayName: 'api-p95'
    description: 'API p95 duration exceeded 500 milliseconds.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      appInsightsId
    ]
    criteria: {
      allOf: [
        {
          // A metric alert can only aggregate this by Average, which is not the objective.
          query: 'requests | summarize p95=percentile(duration, 95) by bin(timestamp, 5m) | where p95 > 500'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'p95'
          operator: 'GreaterThan'
          threshold: 500
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource workerTaskFailed 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: 'worker-task-failed'
  location: location
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  kind: 'LogAlert'
  properties: {
    displayName: 'worker-task-failed'
    description: 'Opaque task failure count exceeded its threshold.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      appInsightsId
    ]
    criteria: {
      allOf: [
        {
          query: 'customMetrics | where name == "eda.task.failed" | summarize failures=sum(valueSum) by bin(timestamp, 5m) | where failures > 0'
          timeAggregation: 'Count'
          metricMeasureColumn: 'failures'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource cancellationAcknowledgement 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: 'cancel-ack'
  location: location
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  kind: 'LogAlert'
  properties: {
    displayName: 'cancel-ack'
    description: 'Cancellation acknowledgement exceeded two seconds.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      appInsightsId
    ]
    criteria: {
      allOf: [
        {
          query: 'customMetrics | where name == "eda.cancel.ack" | summarize acknowledgementMs=max(valueMax) by bin(timestamp, 5m) | where acknowledgementMs > 2000'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'acknowledgementMs'
          operator: 'GreaterThan'
          threshold: 2000
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource redisCircuitOpen 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'redis-circuit-open'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/redis-outage.md' })
  properties: {
    description: 'Redis stream writer circuit opened.'
    severity: 1
    enabled: true
    scopes: [
      redisClusterId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'circuit-open'
          metricName: 'ConnectedClients'
          metricNamespace: 'Microsoft.Cache/redisEnterprise'
          operator: 'LessThanOrEqual'
          threshold: 0
          timeAggregation: 'Maximum'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource cosmos429 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'cosmos-429'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/storage-restore.md' })
  properties: {
    description: 'Cosmos request throttling exceeded the threshold.'
    severity: 2
    enabled: true
    scopes: [
      cosmosAccountId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'throttles'
          metricName: 'TotalRequests'
          metricNamespace: 'Microsoft.DocumentDB/databaseAccounts'
          operator: 'GreaterThan'
          threshold: 10
          timeAggregation: 'Count'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'StatusCode'
              operator: 'Include'
              values: [
                '429'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource sessionOom 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'session-oom'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  properties: {
    description: 'Session pool memory pressure requires intervention.'
    severity: 1
    enabled: true
    scopes: [
      containerAppsEnvironmentId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'memory-pressure'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/managedEnvironments'
          operator: 'GreaterThan'
          threshold: 1900000000
          timeAggregation: 'Maximum'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'PoolName'
              operator: 'Include'
              values: [
                'eda-sessions'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource sessionCapacity 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'session-capacity'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  properties: {
    description: 'Session pool capacity reached its safe limit.'
    severity: 2
    enabled: true
    scopes: [
      containerAppsEnvironmentId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'capacity'
          metricName: 'Replicas'
          metricNamespace: 'Microsoft.App/managedEnvironments'
          operator: 'GreaterThanOrEqual'
          threshold: 10
          timeAggregation: 'Maximum'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource sessionStop 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'session-stop'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  properties: {
    description: 'Session termination exceeded fifteen seconds.'
    severity: 2
    enabled: true
    scopes: [
      containerAppsEnvironmentId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'termination-duration'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/managedEnvironments'
          operator: 'GreaterThan'
          threshold: 15
          timeAggregation: 'Maximum'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource workerReadyReplicas 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'worker-ready-replicas'
  location: 'global'
  tags: union(tags, { runbook: '${runbookBaseUrl}/rollback.md' })
  properties: {
    description: 'No worker replicas are ready.'
    severity: 1
    enabled: true
    scopes: [
      containerAppsEnvironmentId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ready-worker-replicas'
          metricName: 'Replicas'
          metricNamespace: 'Microsoft.App/managedEnvironments'
          operator: 'LessThanOrEqual'
          threshold: 0
          timeAggregation: 'Minimum'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'ContainerAppName'
              operator: 'Include'
              values: [
                'worker'
              ]
            }
          ]
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output actionGroupId string = actionGroup.id