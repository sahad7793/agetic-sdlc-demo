targetScope = 'resourceGroup'

@description('Azure region for this environment.')
param location string

@description('Environment name, either staging or production.')
@allowed([
  'staging'
  'production'
])
param environmentName string

@description('Globally unique Container App name.')
param containerAppName string

@description('Globally unique Container Apps environment name.')
param containerAppsEnvironmentName string

@description('Globally unique Azure SQL logical server name.')
param sqlServerName string

@description('Azure SQL database name.')
param sqlDatabaseName string = 'taskmanagement'

@description('Name of the user-assigned managed identity used by the Container App.')
param managedIdentityName string

@description('Image to run. Provisioning uses a public placeholder; the delivery workflow replaces it with an immutable ACR digest.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Object ID of the Entra administrator for Azure SQL.')
param sqlAdministratorObjectId string

@description('User principal name of the Entra administrator for Azure SQL.')
param sqlAdministratorLogin string

@description('Email address that receives observability alert notifications for this environment.')
param alertEmail string

@description('Cost-allocation tag identifying the owning cost center/GL code for this environment. No default: the operator must supply the organization\'s actual value.')
param costCenter string

@description('Cost-allocation tag identifying the owning team or individual accountable for this environment\'s spend. No default: the operator must supply the organization\'s actual value.')
param owner string

@description('Free-form tags applied to all environment resources, in addition to the required cost-allocation tags below.')
param tags object = {
  environment: environmentName
}

// Cost-governance allocation tags (costCenter/owner) are enforced here so every
// environment resource always carries them, regardless of what the caller's
// free-form `tags` parameter contains. See docs/agentic-sdlc.md > Cost governance.
var resourceTags = union(tags, {
  costCenter: costCenter
  owner: owner
})

var workspaceName = '${containerAppName}-logs'
var applicationInsightsName = '${containerAppName}-appi'

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: resourceTags
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: resourceTags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: resourceTags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: resourceTags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource sqlServer 'Microsoft.Sql/servers@2022-05-01-preview' = {
  name: sqlServerName
  location: location
  tags: resourceTags
  properties: {
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'User'
      login: sqlAdministratorLogin
      sid: sqlAdministratorObjectId
      tenantId: subscription().tenantId
      azureADOnlyAuthentication: true
    }
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2022-05-01-preview' = {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  tags: resourceTags
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 2
  }
  properties: {
    autoPauseDelay: 60
    minCapacity: json('0.5')
    collation: 'SQL_Latin1_General_CP1_CI_AS'
  }
}

resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2022-05-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: resourceTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'taskmanagement-api'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'ASPNETCORE_ENVIRONMENT'
              value: 'Production'
            }
            {
              name: 'Database__UseAzureSql'
              value: 'true'
            }
            {
              name: 'ConnectionStrings__TaskManagement'
              value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDatabase.name};Authentication=Active Directory Managed Identity;User Id=${managedIdentity.properties.clientId};Encrypt=True;TrustServerCertificate=False;'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

module observability 'observability.bicep' = {
  name: '${containerAppName}-observability'
  params: {
    location: location
    environmentName: environmentName
    containerAppId: containerApp.id
    containerAppName: containerApp.name
    applicationInsightsId: applicationInsights.id
    healthCheckUrl: 'https://${containerApp.properties.configuration.ingress.fqdn}/health'
    alertEmail: alertEmail
    tags: resourceTags
  }
}

output containerAppName string = containerApp.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output managedIdentityName string = managedIdentity.name
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
output sqlServerName string = sqlServer.name
output sqlDatabaseName string = sqlDatabase.name
output actionGroupName string = observability.outputs.actionGroupName
output applicationInsightsName string = applicationInsights.name
output logAnalyticsWorkspaceName string = logAnalytics.name
