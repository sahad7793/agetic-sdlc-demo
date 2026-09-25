targetScope = 'resourceGroup'

@description('Azure region to store the workbook definition in (does not affect which resources it queries).')
param location string

@description('Resource ID of the staging Application Insights component.')
param stagingApplicationInsightsId string

@description('Resource ID of the production Application Insights component.')
param productionApplicationInsightsId string

@description('Resource ID of the staging Container App.')
param stagingContainerAppId string

@description('Resource ID of the production Container App.')
param productionContainerAppId string

@description('Tags applied to the workbook.')
param tags object = {}

var workbookDisplayName = 'Task Management API — Observability'
var serializedData = replace(
  replace(
    replace(
      replace(
        loadTextContent('workbook-content.json'),
        '{0}',
        stagingApplicationInsightsId
      ),
      '{1}',
      productionApplicationInsightsId
    ),
    '{2}',
    stagingContainerAppId
  ),
  '{3}',
  productionContainerAppId
)

resource workbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid('taskmanagement-observability-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: workbookDisplayName
    serializedData: serializedData
    version: '1.0'
    sourceId: stagingApplicationInsightsId
    category: 'workbook'
  }
}

output workbookName string = workbook.name
output workbookId string = workbook.id
