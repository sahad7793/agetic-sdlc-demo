targetScope = 'resourceGroup'

@description('Azure region for the Container App and its App Insights component.')
param location string

@description('Environment name, either staging or production.')
@allowed([
  'staging'
  'production'
])
param environmentName string

@description('Resource ID of the Container App to alert on.')
param containerAppId string

@description('Name of the Container App (used to derive alert/action-group names).')
param containerAppName string

@description('Resource ID of the Application Insights component associated with the Container App.')
param applicationInsightsId string

@description('Public HTTPS URL of the /health endpoint to synthetically monitor.')
param healthCheckUrl string

@description('Email address that receives alert notifications for this environment.')
param alertEmail string

@description('Tags applied to all observability resources.')
param tags object = {
  environment: environmentName
}

var actionGroupName = '${containerAppName}-ag'
var webTestName = '${containerAppName}-health-test'

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take(replace('${environmentName}tm', '-', ''), 12)
    enabled: true
    emailReceivers: [
      {
        name: 'primary-oncall'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource http5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${containerAppName}-5xx-rate'
  location: 'global'
  tags: tags
  properties: {
    description: 'Fires when the Container App returns more than 5 HTTP 5xx responses within 5 minutes.'
    severity: 1
    enabled: true
    scopes: [
      containerAppId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Http5xxTotal'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource restartCountAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${containerAppName}-restart-spike'
  location: 'global'
  tags: tags
  properties: {
    description: 'Fires when Container App replicas restart more than 3 times within 15 minutes, indicating crash-looping.'
    severity: 2
    enabled: true
    scopes: [
      containerAppId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'RestartCountTotal'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 3
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource p95LatencyAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${containerAppName}-p95-latency'
  location: location
  tags: tags
  properties: {
    displayName: '${containerAppName} p95 request latency'
    description: 'Fires when p95 request duration exceeds 1500ms over a 15 minute window. Container Apps platform metrics only support avg/min/max/total, so this uses the Application Insights requests log for a true percentile.'
    severity: 2
    enabled: true
    scopes: [
      applicationInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: 'requests | summarize p95Duration = percentile(duration, 95) by bin(timestamp, 5m) | where p95Duration > 1500'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource availabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: webTestName
  location: location
  tags: union(tags, {
    'hidden-link:${applicationInsightsId}': 'Resource'
  })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: webTestName
    Name: webTestName
    Description: 'Synthetic availability check against the /health endpoint.'
    Enabled: true
    Frequency: 300
    Timeout: 30
    Kind: 'standard'
    RetryEnabled: true
    Locations: [
      {
        Id: 'us-fl-mia-edge'
      }
      {
        Id: 'us-tx-sn1-azr'
      }
      {
        Id: 'us-il-ch1-azr'
      }
      {
        Id: 'us-ca-sjc-azr'
      }
      {
        Id: 'emea-nl-ams-azr'
      }
    ]
    Request: {
      RequestUrl: healthCheckUrl
      HttpVerb: 'GET'
      ParseDependentRequests: false
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      SSLCheck: false
    }
  }
}

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${webTestName}-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Fires when the /health synthetic availability test fails from 2 or more locations within 5 minutes.'
    severity: 1
    enabled: true
    scopes: [
      availabilityTest.id
      applicationInsightsId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Insights/components'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: availabilityTest.id
      componentId: applicationInsightsId
      failedLocationCount: 2
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output actionGroupName string = actionGroup.name
output actionGroupId string = actionGroup.id
output http5xxAlertName string = http5xxAlert.name
output restartCountAlertName string = restartCountAlert.name
output p95LatencyAlertName string = p95LatencyAlert.name
output availabilityTestName string = availabilityTest.name
output availabilityAlertName string = availabilityAlert.name
