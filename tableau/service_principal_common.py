#!/usr/bin/env python3
"""Frozen policy and Azure SQL safeguards for the temporary Tableau identity."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    ODBC_DRIVER,
    SafeLoaderError,
    classify_database_error,
)
from reporting_common import ReportingExpectations  # noqa: E402
from verify_reporting import verify_report_views  # noqa: E402


IDENTITY_NAME = "sp-office-attendance-tableau-proof"
CREDENTIAL_NAME = "tableau-private-proof-60m"
REPORT_ROLE = "report_reader"
SECRET_LIFETIME_MINUTES = 60
CLIPBOARD_MAX_SECONDS = 60
SERVICE_PRINCIPAL_CONNECTION_ATTEMPTS = 4
SERVICE_PRINCIPAL_CONNECTION_TIMEOUT_SECONDS = 30
SERVICE_PRINCIPAL_PROPAGATION_DELAY_SECONDS = 10
REPORT_VIEWS = (
    "vw_DailyAttendanceTrend",
    "vw_DailyDepartmentAttendance",
    "vw_LoadQualitySummary",
    "vw_ValidationIssueSummary",
)


@dataclass(frozen=True)
class ServicePrincipalPolicy:
    identity_name: str = IDENTITY_NAME
    credential_name: str = CREDENTIAL_NAME
    secret_lifetime_minutes: int = SECRET_LIFETIME_MINUTES
    clipboard_max_seconds: int = CLIPBOARD_MAX_SECONDS
    sign_in_audience: str = "AzureADMyOrg"
    database_role: str = REPORT_ROLE
    azure_rbac_roles: int = 0
    directory_roles: int = 0
    required_api_permissions: int = 0
    persisted_secret_files: int = 0


APPROVED_POLICY = ServicePrincipalPolicy()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def validate_policy(policy: ServicePrincipalPolicy = APPROVED_POLICY) -> dict:
    require(policy.identity_name == IDENTITY_NAME, "Tableau identity name changed.")
    require(policy.credential_name == CREDENTIAL_NAME, "Credential label changed.")
    require(policy.secret_lifetime_minutes == 60, "Secret lifetime must remain 60 minutes.")
    require(policy.clipboard_max_seconds == 60, "Clipboard limit must remain 60 seconds.")
    require(policy.sign_in_audience == "AzureADMyOrg", "Identity must remain single-tenant.")
    require(policy.database_role == "report_reader", "Database role changed.")
    require(policy.azure_rbac_roles == 0, "Azure RBAC must remain empty.")
    require(policy.directory_roles == 0, "Directory roles must remain empty.")
    require(policy.required_api_permissions == 0, "Application API permissions must remain empty.")
    require(policy.persisted_secret_files == 0, "Secret persistence is prohibited.")
    return {
        "secret_minutes": policy.secret_lifetime_minutes,
        "clipboard_seconds": policy.clipboard_max_seconds,
        "azure_roles": policy.azure_rbac_roles,
        "directory_roles": policy.directory_roles,
        "api_permissions": policy.required_api_permissions,
        "database_roles": 1,
    }


def _rows(connection, sql: str) -> list:
    try:
        return [tuple(row) for row in connection.cursor().execute(sql).fetchall()]
    except Exception as exc:
        raise SafeLoaderError(
            "A Tableau identity safety query failed; database details were suppressed."
        ) from exc


def verify_database_preflight(connection) -> None:
    rows = _rows(
        connection,
        "SELECT "
        "(SELECT COUNT(*) FROM sys.database_principals WHERE name = N'" + IDENTITY_NAME + "'), "
        "(SELECT COUNT(*) FROM sys.database_principals WHERE name = N'report_reader' AND type = 'R'), "
        "(SELECT COUNT(*) FROM sys.database_role_members AS membership "
        " INNER JOIN sys.database_principals AS roles ON roles.principal_id = membership.role_principal_id "
        " WHERE roles.name = N'report_reader');",
    )
    require(rows == [(0, 1, 0)], "Tableau identity database preflight is not clean.")
    views = tuple(
        str(row[0])
        for row in _rows(
            connection,
            "SELECT name FROM sys.views WHERE schema_id = SCHEMA_ID(N'report') ORDER BY name;",
        )
    )
    require(views == tuple(sorted(REPORT_VIEWS)), "Tableau reporting-view inventory is not exact.")


BIND_SQL = f"""
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
    BEGIN TRANSACTION;
    IF DATABASE_PRINCIPAL_ID(N'{IDENTITY_NAME}') IS NOT NULL
        THROW 51940, 'Temporary Tableau principal already exists.', 1;
    IF EXISTS
    (
        SELECT 1 FROM sys.database_role_members AS membership
        INNER JOIN sys.database_principals AS roles
            ON roles.principal_id = membership.role_principal_id
        WHERE roles.name = N'{REPORT_ROLE}'
    )
        THROW 51941, 'Reporting role is not empty.', 1;
    CREATE USER [{IDENTITY_NAME}] FROM EXTERNAL PROVIDER;
    ALTER ROLE [{REPORT_ROLE}] ADD MEMBER [{IDENTITY_NAME}];
    IF NOT EXISTS
    (
        SELECT 1 FROM sys.database_role_members AS membership
        WHERE membership.role_principal_id = DATABASE_PRINCIPAL_ID(N'{REPORT_ROLE}')
          AND membership.member_principal_id = DATABASE_PRINCIPAL_ID(N'{IDENTITY_NAME}')
    )
        THROW 51942, 'Temporary Tableau membership was not created.', 1;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
"""


UNBIND_SQL = f"""
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRY
    BEGIN TRANSACTION;
    IF DATABASE_PRINCIPAL_ID(N'{IDENTITY_NAME}') IS NOT NULL
    BEGIN
        IF IS_ROLEMEMBER(N'{REPORT_ROLE}', N'{IDENTITY_NAME}') = 1
            ALTER ROLE [{REPORT_ROLE}] DROP MEMBER [{IDENTITY_NAME}];
        DROP USER [{IDENTITY_NAME}];
    END;
    IF DATABASE_PRINCIPAL_ID(N'{IDENTITY_NAME}') IS NOT NULL
        THROW 51943, 'Temporary Tableau principal cleanup failed.', 1;
    IF EXISTS
    (
        SELECT 1 FROM sys.database_role_members AS membership
        INNER JOIN sys.database_principals AS roles
            ON roles.principal_id = membership.role_principal_id
        WHERE roles.name = N'{REPORT_ROLE}'
    )
        THROW 51944, 'Reporting role is not empty after cleanup.', 1;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
"""


def _execute_controlled(connection, sql: str, action: str) -> None:
    try:
        connection.cursor().execute(sql)
    except Exception as exc:
        raise SafeLoaderError(f"Temporary Tableau database {action} failed safely.") from exc


def bind_database_user(connection) -> None:
    _execute_controlled(connection, BIND_SQL, "binding")


def unbind_database_user(connection) -> None:
    _execute_controlled(connection, UNBIND_SQL, "cleanup")
    verify_database_preflight(connection)


def _odbc_value(value: str) -> str:
    require(bool(value), "A private service-principal value is empty.")
    require("\x00" not in value, "A private service-principal value is invalid.")
    return "{" + value.replace("}", "}}") + "}"


def connect_service_principal(server: str, database: str, client_id: str, secret: str):
    try:
        import pyodbc
    except ImportError as exc:
        raise SafeLoaderError("Project-local pyodbc is not installed.") from exc
    require(ODBC_DRIVER in pyodbc.drivers(), "Microsoft ODBC Driver 18 is unavailable.")
    connection_string = (
        f"Driver={{{ODBC_DRIVER}}};Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;"
        f"Connection Timeout={SERVICE_PRINCIPAL_CONNECTION_TIMEOUT_SECONDS};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={_odbc_value(client_id)};PWD={_odbc_value(secret)};"
    )
    last_exception = None
    last_category = "DATABASE_OPERATION"
    for attempt in range(1, SERVICE_PRINCIPAL_CONNECTION_ATTEMPTS + 1):
        try:
            return pyodbc.connect(
                connection_string,
                autocommit=True,
                timeout=SERVICE_PRINCIPAL_CONNECTION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            last_exception = exc
            last_category = classify_database_error(exc)
            retryable = last_category in {"AUTHENTICATION", "DATABASE_OPERATION"}
            if attempt == SERVICE_PRINCIPAL_CONNECTION_ATTEMPTS or not retryable:
                break
            time.sleep(SERVICE_PRINCIPAL_PROPAGATION_DELAY_SECONDS)
    raise SafeLoaderError(
        f"Temporary Tableau identity connection failed: {last_category}."
    ) from last_exception


def verify_service_principal_boundary(
    connection, expectations: ReportingExpectations
) -> dict:
    rows = _rows(
        connection,
        "SELECT USER_NAME(), IS_ROLEMEMBER(N'report_reader'), IS_ROLEMEMBER(N'app_loader'), "
        "IS_MEMBER(N'db_owner'), "
        "HAS_PERMS_BY_NAME(N'report', N'SCHEMA', N'SELECT'), "
        "HAS_PERMS_BY_NAME(N'report', N'SCHEMA', N'UPDATE'), "
        "HAS_PERMS_BY_NAME(N'stage', N'SCHEMA', N'SELECT'), "
        "HAS_PERMS_BY_NAME(N'core', N'SCHEMA', N'SELECT'), "
        "HAS_PERMS_BY_NAME(N'stage.usp_BeginImportBatch', N'OBJECT', N'EXECUTE'), "
        "HAS_PERMS_BY_NAME(N'core.usp_RefreshDailyAttendanceSummary', N'OBJECT', N'EXECUTE'), "
        "HAS_PERMS_BY_NAME(N'report', N'SCHEMA', N'VIEW DEFINITION');",
    )
    require(
        rows == [(IDENTITY_NAME, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0)],
        "Temporary Tableau principal permissions are not exact.",
    )
    verify_report_views(connection, expectations)
    for sql in (
        "SELECT TOP (0) * FROM stage.ImportBatch;",
        "SELECT TOP (0) * FROM core.DailyAttendanceSummary;",
    ):
        try:
            connection.cursor().execute(sql).fetchall()
        except Exception:
            continue
        raise SafeLoaderError("A direct restricted-schema read unexpectedly succeeded.")
    return {"report_views": 4, "expected_denials": 2, "report_roles": 1}
