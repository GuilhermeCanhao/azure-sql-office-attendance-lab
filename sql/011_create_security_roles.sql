/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 011_create_security_roles.sql
    Purpose: Create the least-privilege loader and reporting authorization roles.

    Authentication principals are deliberately separate from these durable
    database roles. This script creates no login, user, password, secret, or
    external identity.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF DATABASE_PRINCIPAL_ID(N'app_loader') IS NULL
        EXEC(N'CREATE ROLE app_loader AUTHORIZATION dbo;');
    ELSE IF NOT EXISTS
    (
        SELECT 1
        FROM sys.database_principals
        WHERE principal_id = DATABASE_PRINCIPAL_ID(N'app_loader')
          AND type = 'R'
    )
        THROW 51900, 'The app_loader name exists but is not a database role.', 1;

    IF DATABASE_PRINCIPAL_ID(N'report_reader') IS NULL
        EXEC(N'CREATE ROLE report_reader AUTHORIZATION dbo;');
    ELSE IF NOT EXISTS
    (
        SELECT 1
        FROM sys.database_principals
        WHERE principal_id = DATABASE_PRINCIPAL_ID(N'report_reader')
          AND type = 'R'
    )
        THROW 51901, 'The report_reader name exists but is not a database role.', 1;

    ALTER AUTHORIZATION ON ROLE::app_loader TO dbo;
    ALTER AUTHORIZATION ON ROLE::report_reader TO dbo;

    /*
        Direct data and definition access is prohibited. The procedure grants
        below continue to work through the common dbo ownership chain.
    */
    DENY SELECT, INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::stage TO app_loader;
    DENY SELECT, INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::core TO app_loader;
    DENY SELECT, INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::report TO app_loader;

    GRANT EXECUTE ON OBJECT::stage.usp_BeginImportBatch TO app_loader;
    GRANT EXECUTE ON OBJECT::stage.usp_AppendImportChunk TO app_loader;
    GRANT EXECUTE ON OBJECT::stage.usp_FinalizeImportBatch TO app_loader;
    GRANT EXECUTE ON OBJECT::stage.usp_FailImportBatch TO app_loader;
    GRANT EXECUTE ON OBJECT::stage.usp_GetImportBatchResult TO app_loader;
    GRANT EXECUTE ON OBJECT::core.usp_RefreshDailyAttendanceSummary TO app_loader;

    REVOKE EXECUTE ON OBJECT::core.usp_BootstrapReferenceData FROM app_loader;
    REVOKE EXECUTE ON OBJECT::core.usp_AssignDevice FROM app_loader;

    DENY SELECT, INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::stage TO report_reader;
    DENY SELECT, INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::core TO report_reader;
    DENY INSERT, UPDATE, DELETE, ALTER, VIEW DEFINITION
        ON SCHEMA::report TO report_reader;

    GRANT SELECT ON SCHEMA::report TO report_reader;

    REVOKE EXECUTE ON OBJECT::stage.usp_BeginImportBatch FROM report_reader;
    REVOKE EXECUTE ON OBJECT::stage.usp_AppendImportChunk FROM report_reader;
    REVOKE EXECUTE ON OBJECT::stage.usp_FinalizeImportBatch FROM report_reader;
    REVOKE EXECUTE ON OBJECT::stage.usp_FailImportBatch FROM report_reader;
    REVOKE EXECUTE ON OBJECT::stage.usp_GetImportBatchResult FROM report_reader;
    REVOKE EXECUTE ON OBJECT::core.usp_BootstrapReferenceData FROM report_reader;
    REVOKE EXECUTE ON OBJECT::core.usp_AssignDevice FROM report_reader;
    REVOKE EXECUTE ON OBJECT::core.usp_RefreshDailyAttendanceSummary FROM report_reader;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
