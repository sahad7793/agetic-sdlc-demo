targetScope = 'resourceGroup'

@description('Location for the shared container registry.')
param location string

@description('Name of the Azure Container Registry.')
param containerRegistryName string

@description('Principal ID of the staging GitHub Actions service principal.')
param stagingDeploymentPrincipalId string

@description('Resource group name for the staging environment (used to construct resource IDs for the shared observability workbook).')
param stagingResourceGroupName string

@description('Container App name for the staging environment.')
param stagingContainerAppName string

@description('Resource group name for the production environment (used to construct resource IDs for the shared observability workbook).')
param productionResourceGroupName string

@description('Container App name for the production environment.')
param productionContainerAppName string

@description('Azure region to store the shared observability workbook in.')
param workbookLocation string = location

@description('Tags applied to every shared resource.')
param tags object = {}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  sku: {
    name: 'Basic'
  }
  tags: tags
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: {
      quarantinePolicy: {
        status: 'disabled'
      }
      trustPolicy: {
        type: 'Notary'
        status: 'disabled'
      }
    }
  }
}

resource stagingAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, stagingDeploymentPrincipalId, 'acrpush')
  scope: containerRegistry
  properties: {
    principalId: stagingDeploymentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '8311e382-0749-4cb8-b61a-304f252e45ec'
    )
  }
}

output containerRegistryName string = containerRegistry.name
output containerRegistryLoginServer string = containerRegistry.properties.loginServer
output containerRegistryId string = containerRegistry.id

// Resource IDs are constructed deterministically from the same naming convention used in
// infra/environment.bicep ('${containerAppName}-appi') so the workbook can be deployed
// independently of, and before or after, the per-environment deployments.
var stagingApplicationInsightsId = resourceId(
  stagingResourceGroupName,
  'Microsoft.Insights/components',
  '${stagingContainerAppName}-appi'
)
var productionApplicationInsightsId = resourceId(
  productionResourceGroupName,
  'Microsoft.Insights/components',
  '${productionContainerAppName}-appi'
)
var stagingContainerAppId = resourceId(
  stagingResourceGroupName,
  'Microsoft.App/containerApps',
  stagingContainerAppName
)
var productionContainerAppId = resourceId(
  productionResourceGroupName,
  'Microsoft.App/containerApps',
  productionContainerAppName
)

module observabilityWorkbook 'workbook.bicep' = {
  name: 'taskmanagement-observability-workbook'
  scope: resourceGroup()
  params: {
    location: workbookLocation
    stagingApplicationInsightsId: stagingApplicationInsightsId
    productionApplicationInsightsId: productionApplicationInsightsId
    stagingContainerAppId: stagingContainerAppId
    productionContainerAppId: productionContainerAppId
    tags: tags
  }
}

output observabilityWorkbookName string = observabilityWorkbook.outputs.workbookName
