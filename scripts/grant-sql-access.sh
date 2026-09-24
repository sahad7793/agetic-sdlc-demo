#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo "Usage: $0 <server> <database> <identity-name> <identity-principal-id> <identity-client-id>" >&2
  exit 1
fi

sql_server="$1"
database="$2"
identity_name="$3"
identity_principal_id="$4"
identity_client_id="$5"

if ! command -v sqlcmd >/dev/null 2>&1; then
  echo "sqlcmd is required to grant Azure SQL data-plane access." >&2
  exit 1
fi

sql_query="
IF NOT EXISTS (
  SELECT 1
  FROM sys.database_principals
  WHERE sid = CAST('${identity_principal_id}' AS uniqueidentifier)
)
  CREATE USER [${identity_name}] FROM EXTERNAL PROVIDER WITH OBJECT_ID = '${identity_principal_id}';

IF NOT EXISTS (
  SELECT 1
  FROM sys.database_role_members membership
  JOIN sys.database_principals role_principal ON membership.role_principal_id = role_principal.principal_id
  JOIN sys.database_principals member_principal ON membership.member_principal_id = member_principal.principal_id
  WHERE role_principal.name = N'db_datareader' AND member_principal.name = N'${identity_name}'
)
  ALTER ROLE db_datareader ADD MEMBER [${identity_name}];

IF NOT EXISTS (
  SELECT 1
  FROM sys.database_role_members membership
  JOIN sys.database_principals role_principal ON membership.role_principal_id = role_principal.principal_id
  JOIN sys.database_principals member_principal ON membership.member_principal_id = member_principal.principal_id
  WHERE role_principal.name = N'db_datawriter' AND member_principal.name = N'${identity_name}'
)
  ALTER ROLE db_datawriter ADD MEMBER [${identity_name}];

IF NOT EXISTS (
  SELECT 1
  FROM sys.database_role_members membership
  JOIN sys.database_principals role_principal ON membership.role_principal_id = role_principal.principal_id
  JOIN sys.database_principals member_principal ON membership.member_principal_id = member_principal.principal_id
  WHERE role_principal.name = N'db_ddladmin' AND member_principal.name = N'${identity_name}'
)
  ALTER ROLE db_ddladmin ADD MEMBER [${identity_name}];
"

echo "Granting Azure SQL access to managed identity ${identity_name} (${identity_principal_id}; client ${identity_client_id})."
sqlcmd \
  -S "${sql_server}.database.windows.net" \
  -d "$database" \
  --authentication-method ActiveDirectoryDefault \
  -Q "$sql_query"
