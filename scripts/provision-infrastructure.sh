#!/usr/bin/env bash
set -euo pipefail

subscription_id='8a58968e-9173-49dd-9dff-f1daeac7bb33'
staging_location='centralus'
production_location='westus2'
shared_location='eastus2'
tenant_id='d2b7e8c1-8c48-4a61-b850-337bd895c756'
sql_administrator_object_id='4812e4b4-e014-4375-9e29-3bf290bfcc23'
sql_administrator_login='sahad@saasberrylabs.com'
alert_email='sahad@saasberrylabs.com'
# Cost-allocation tags (see docs/agentic-sdlc.md > Cost governance). These are
# placeholders: replace with your organization's actual cost-center code and
# accountable owner/team before running this script. There is no safe default.
cost_center_tag='CHANGEME-cost-center'
owner_tag='CHANGEME-owner-or-team'
shared_resource_group='rg-taskmanagement-shared'
container_registry_name='tmapi8a58968e'
staging_resource_group='rg-taskmanagement-staging-centralus'
production_resource_group='rg-taskmanagement-production-westus2'
staging_deployment_principal_id='d0438719-b01f-44c3-b191-da7e925aca4a'
production_deployment_principal_id='b3324232-392c-47b3-8539-094a018a505c'
staging_container_app_name='task-api-stage-8a58968e'
production_container_app_name='task-api-prod-west-8a58968e'

az account set --subscription "$subscription_id"

provisioner_ip=$(curl --fail --silent --show-error https://api.ipify.org)

az group create --name "$shared_resource_group" --location "$shared_location" --tags application=taskmanagement >/dev/null

az group create --name "$staging_resource_group" --location "$staging_location" --tags application=taskmanagement >/dev/null
az group create --name "$production_resource_group" --location "$production_location" --tags application=taskmanagement >/dev/null

az role assignment create \
  --assignee-object-id "$staging_deployment_principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$subscription_id/resourceGroups/$staging_resource_group" >/dev/null
az role assignment create \
  --assignee-object-id "$production_deployment_principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$subscription_id/resourceGroups/$production_resource_group" >/dev/null

az deployment group create \
  --resource-group "$shared_resource_group" \
  --template-file infra/shared.bicep \
  --parameters \
    location="$shared_location" \
    containerRegistryName="$container_registry_name" \
    stagingDeploymentPrincipalId="$staging_deployment_principal_id" \
    stagingResourceGroupName="$staging_resource_group" \
    stagingContainerAppName="$staging_container_app_name" \
    productionResourceGroupName="$production_resource_group" \
    productionContainerAppName="$production_container_app_name" \
    workbookLocation="$shared_location" \
    costCenter="$cost_center_tag" \
    owner="$owner_tag" \
    tags="{\"application\":\"taskmanagement\",\"component\":\"registry\"}" \
  --output none

container_registry_id=$(az acr show --name "$container_registry_name" --resource-group "$shared_resource_group" --query id --output tsv)
container_registry_login_server=$(az acr show --name "$container_registry_name" --resource-group "$shared_resource_group" --query loginServer --output tsv)

provision_environment() {
  local environment_name="$1"
  local resource_group="$2"
  local environment_location="$3"
  local container_app_name="$4"
  local managed_environment_name="$5"
  local sql_server_name="$6"
  local managed_identity_name="$7"

  az deployment group create \
    --resource-group "$resource_group" \
    --template-file infra/environment.bicep \
    --parameters \
      location="$environment_location" \
      environmentName="$environment_name" \
      containerAppName="$container_app_name" \
      containerAppsEnvironmentName="$managed_environment_name" \
      sqlServerName="$sql_server_name" \
      managedIdentityName="$managed_identity_name" \
      sqlAdministratorObjectId="$sql_administrator_object_id" \
      sqlAdministratorLogin="$sql_administrator_login" \
      alertEmail="$alert_email" \
      costCenter="$cost_center_tag" \
      owner="$owner_tag" \
      tags="{\"application\":\"taskmanagement\",\"environment\":\"$environment_name\"}" \
    --output none

  local identity_principal_id
  identity_principal_id=$(az identity show --name "$managed_identity_name" --resource-group "$resource_group" --query principalId --output tsv)
  az role assignment create \
    --assignee-object-id "$identity_principal_id" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$container_registry_id" >/dev/null
}

provision_environment staging "$staging_resource_group" "$staging_location" "$staging_container_app_name" 'task-cae-stage-8a58968e' 'tasksqlstage8a58968e' 'task-api-stage-identity'
provision_environment production "$production_resource_group" "$production_location" "$production_container_app_name" 'task-cae-prod-west-8a58968e' 'tasksqlprodwu8a58968e' 'task-api-prod-west-identity'

for environment in staging production; do
  if [ "$environment" = staging ]; then
    resource_group="$staging_resource_group"
    identity_name='task-api-stage-identity'
    sql_server_name='tasksqlstage8a58968e'
    container_app_name='task-api-stage-8a58968e'
  else
    resource_group="$production_resource_group"
    sql_server_name='tasksqlprodwu8a58968e'
    container_app_name='task-api-prod-west-8a58968e'
    identity_name='task-api-prod-west-identity'
  fi

  principal_id=$(az identity show --name "$identity_name" --resource-group "$resource_group" --query principalId --output tsv)
  client_id=$(az identity show --name "$identity_name" --resource-group "$resource_group" --query clientId --output tsv)
  identity_resource_id=$(az identity show --name "$identity_name" --resource-group "$resource_group" --query id --output tsv)
  az sql server firewall-rule create \
    --resource-group "$resource_group" \
    --server "$sql_server_name" \
    --name ProvisionerExactIp \
    --start-ip-address "$provisioner_ip" \
    --end-ip-address "$provisioner_ip" \
    --output none
  gh variable set MANAGED_IDENTITY_ID --repo sahad7793/agetic-sdlc-demo --env "$environment" --body "$identity_resource_id"
  ./scripts/grant-sql-access.sh "$sql_server_name" taskmanagement "$identity_name" "$principal_id" "$client_id"
done

echo "Provisioning complete. Set the ACR and Container App GitHub Environment variables before running the delivery workflow."
