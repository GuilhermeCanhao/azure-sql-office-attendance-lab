/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 001_create_schemas.sql
    Purpose: Create the stage, core, and report schema boundaries.

    This script is intentionally rerunnable. Existing schemas are retained.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF SCHEMA_ID(N'stage') IS NULL
        EXEC(N'CREATE SCHEMA [stage] AUTHORIZATION [dbo];');

    IF SCHEMA_ID(N'core') IS NULL
        EXEC(N'CREATE SCHEMA [core] AUTHORIZATION [dbo];');

    IF SCHEMA_ID(N'report') IS NULL
        EXEC(N'CREATE SCHEMA [report] AUTHORIZATION [dbo];');

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
