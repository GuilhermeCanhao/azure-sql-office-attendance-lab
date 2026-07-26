/*
    Verification for 011_create_security_roles.sql.

    The test creates no-login users, one terminal synthetic batch, and one
    updatable reporting view over that fixture inside a transaction. It proves allowed and
    denied behavior under EXECUTE AS USER, guarantees REVERT, rolls back every
    fixture, and independently verifies cleanup.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF DATABASE_PRINCIPAL_ID(N'app_loader') IS NULL
   OR DATABASE_PRINCIPAL_ID(N'report_reader') IS NULL
    THROW 51910, 'One or more custom security roles are missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.database_principals
    WHERE name IN (N'app_loader', N'report_reader')
      AND type = 'R'
      AND owning_principal_id = DATABASE_PRINCIPAL_ID(N'dbo')
) <> 2
    THROW 51931, 'The custom roles do not have the expected dbo ownership.', 1;

IF OBJECT_ID(N'stage.usp_GetImportBatchResult', N'P') IS NULL
    THROW 51911, 'The controlled batch-result procedure is missing.', 1;

DECLARE @LoaderUser sysname = N'tst_app_loader';
DECLARE @ReporterUser sysname = N'tst_report_reader';
DECLARE @Checksum binary(32) = HASHBYTES('SHA2_256', 'TST-SECURITY-ROLES-20260717');
DECLARE @ImportBatchId bigint;
DECLARE @LoaderDenied int = 0;
DECLARE @ReporterDenied int = 0;
DECLARE @ReporterWriteSucceeded bit = 0;
DECLARE @ProbeValue int;
DECLARE @TransactionRollback varchar(10) = 'FAIL';

IF DATABASE_PRINCIPAL_ID(@LoaderUser) IS NOT NULL
   OR DATABASE_PRINCIPAL_ID(@ReporterUser) IS NOT NULL
   OR OBJECT_ID(N'report.vw_SecurityPermissionProbe', N'V') IS NOT NULL
   OR OBJECT_ID(N'report.vw_LoaderUnauthorized', N'V') IS NOT NULL
   OR OBJECT_ID(N'report.vw_ReporterUnauthorized', N'V') IS NOT NULL
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE FileChecksum = @Checksum)
    THROW 51912, 'A security-test fixture already exists.', 1;

IF HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL') <> 1
    THROW 51913, 'The verification caller does not have the required administrator control.', 1;

IF IS_ROLEMEMBER(N'app_loader') = 1 OR IS_ROLEMEMBER(N'report_reader') = 1
    THROW 51914, 'The administrator must not be a member of an application role.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    INNER JOIN sys.objects AS object_name
        ON object_name.object_id = permission.major_id
    INNER JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = object_name.schema_id
    WHERE principal.name = N'app_loader'
      AND permission.state IN ('G', 'W')
      AND permission.permission_name = 'EXECUTE'
      AND permission.class_desc = 'OBJECT_OR_COLUMN'
      AND CONCAT(schema_name.name, N'.', object_name.name) IN
      (
          N'stage.usp_BeginImportBatch',
          N'stage.usp_AppendImportChunk',
          N'stage.usp_FinalizeImportBatch',
          N'stage.usp_FailImportBatch',
          N'stage.usp_GetImportBatchResult',
          N'core.usp_RefreshDailyAttendanceSummary'
      )
) <> 6
    THROW 51915, 'The app_loader procedure-grant set is incomplete.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    LEFT JOIN sys.objects AS object_name
        ON permission.class_desc = 'OBJECT_OR_COLUMN'
       AND object_name.object_id = permission.major_id
    LEFT JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = object_name.schema_id
    WHERE principal.name = N'app_loader'
      AND permission.class_desc = 'OBJECT_OR_COLUMN'
      AND
      (
          permission.state NOT IN ('G', 'W')
          OR permission.permission_name <> 'EXECUTE'
          OR CONCAT(schema_name.name, N'.', object_name.name) NOT IN
          (
              N'stage.usp_BeginImportBatch',
              N'stage.usp_AppendImportChunk',
              N'stage.usp_FinalizeImportBatch',
              N'stage.usp_FailImportBatch',
              N'stage.usp_GetImportBatchResult',
              N'core.usp_RefreshDailyAttendanceSummary'
          )
      )
)
    THROW 51925, 'The app_loader role has an unexpected object permission.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    INNER JOIN sys.schemas AS schema_name
        ON permission.class_desc = 'SCHEMA'
       AND schema_name.schema_id = permission.major_id
    WHERE principal.name = N'app_loader'
      AND permission.state = 'D'
      AND schema_name.name IN (N'stage', N'core', N'report')
      AND permission.permission_name IN
      (
          'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
      )
) <> 18
    THROW 51926, 'The app_loader schema-denial set is incorrect.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    INNER JOIN sys.schemas AS schema_name
        ON permission.class_desc = 'SCHEMA'
       AND schema_name.schema_id = permission.major_id
    WHERE principal.name = N'app_loader'
      AND
      (
          permission.state <> 'D'
          OR schema_name.name NOT IN (N'stage', N'core', N'report')
          OR permission.permission_name NOT IN
          (
              'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
          )
      )
)
    THROW 51929, 'The app_loader role has an unexpected schema permission.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    INNER JOIN sys.schemas AS schema_name
        ON permission.class_desc = 'SCHEMA'
       AND schema_name.schema_id = permission.major_id
    WHERE principal.name = N'report_reader'
      AND
      (
          (
              permission.state = 'D'
              AND
              (
                  (
                      schema_name.name IN (N'stage', N'core')
                      AND permission.permission_name IN
                      (
                          'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
                      )
                  )
                  OR
                  (
                      schema_name.name = N'report'
                      AND permission.permission_name IN
                      (
                          'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
                      )
                  )
              )
          )
          OR
          (
              permission.state IN ('G', 'W')
              AND schema_name.name = N'report'
              AND permission.permission_name = 'SELECT'
          )
      )
) <> 18
    THROW 51927, 'The report_reader schema-permission set is incorrect.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    INNER JOIN sys.schemas AS schema_name
        ON permission.class_desc = 'SCHEMA'
       AND schema_name.schema_id = permission.major_id
    WHERE principal.name = N'report_reader'
      AND NOT
      (
          (
              permission.state = 'D'
              AND
              (
                  (
                      schema_name.name IN (N'stage', N'core')
                      AND permission.permission_name IN
                      (
                          'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
                      )
                  )
                  OR
                  (
                      schema_name.name = N'report'
                      AND permission.permission_name IN
                      (
                          'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'VIEW DEFINITION'
                      )
                  )
              )
          )
          OR
          (
              permission.state IN ('G', 'W')
              AND schema_name.name = N'report'
              AND permission.permission_name = 'SELECT'
          )
      )
)
    THROW 51930, 'The report_reader role has an unexpected schema permission.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    WHERE principal.name = N'report_reader'
      AND permission.class_desc = 'OBJECT_OR_COLUMN'
)
    THROW 51928, 'The report_reader role has an unexpected object permission.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.database_permissions AS permission
    INNER JOIN sys.database_principals AS principal
        ON principal.principal_id = permission.grantee_principal_id
    WHERE principal.name IN (N'app_loader', N'report_reader')
      AND permission.class_desc = 'DATABASE'
)
    THROW 51916, 'A custom role has an unexpected database-level permission.', 1;

BEGIN TRANSACTION;

BEGIN TRY
    EXEC(N'CREATE USER tst_app_loader WITHOUT LOGIN;');
    EXEC(N'CREATE USER tst_report_reader WITHOUT LOGIN;');
    ALTER ROLE app_loader ADD MEMBER tst_app_loader;
    ALTER ROLE report_reader ADD MEMBER tst_report_reader;

    INSERT INTO stage.ImportBatch
    (
        SourceType,
        SourceFileName,
        FileChecksum,
        CompletedAt,
        Status,
        RowsReceived,
        RowsAccepted,
        RowsRejected
    )
    VALUES
    (
        'CARD',
        N'tst-security-role.csv',
        @Checksum,
        SYSUTCDATETIME(),
        'COMPLETED',
        0,
        0,
        0
    );

    SET @ImportBatchId = CONVERT(bigint, SCOPE_IDENTITY());

    EXEC(N'CREATE VIEW report.vw_SecurityPermissionProbe AS
        SELECT ImportBatchId, RowsReceived
        FROM stage.ImportBatch
        WHERE SourceFileName = N''tst-security-role.csv'';');

    EXECUTE AS USER = N'tst_app_loader';
    BEGIN TRY
        IF HAS_PERMS_BY_NAME(N'stage.usp_BeginImportBatch', 'OBJECT', 'EXECUTE') <> 1
           OR HAS_PERMS_BY_NAME(N'stage.usp_AppendImportChunk', 'OBJECT', 'EXECUTE') <> 1
           OR HAS_PERMS_BY_NAME(N'stage.usp_FinalizeImportBatch', 'OBJECT', 'EXECUTE') <> 1
           OR HAS_PERMS_BY_NAME(N'stage.usp_FailImportBatch', 'OBJECT', 'EXECUTE') <> 1
           OR HAS_PERMS_BY_NAME(N'stage.usp_GetImportBatchResult', 'OBJECT', 'EXECUTE') <> 1
           OR HAS_PERMS_BY_NAME(N'core.usp_RefreshDailyAttendanceSummary', 'OBJECT', 'EXECUTE') <> 1
            THROW 51917, 'The loader is missing an approved procedure permission.', 1;

        DECLARE @BatchResult TABLE
        (
            SourceType varchar(10),
            SourceFileName nvarchar(260),
            FileChecksumHex varchar(64),
            Status varchar(10),
            RowsReceived int,
            RowsAccepted int,
            RowsRejected int
        );

        INSERT INTO @BatchResult
        EXEC stage.usp_GetImportBatchResult
            @ImportBatchId = @ImportBatchId,
            @FileChecksum = @Checksum;

        IF NOT EXISTS
        (
            SELECT 1
            FROM @BatchResult
            WHERE SourceType = 'CARD'
              AND SourceFileName = N'tst-security-role.csv'
              AND FileChecksumHex = CONVERT(varchar(64), @Checksum, 2)
              AND Status = 'COMPLETED'
              AND RowsReceived = 0
              AND RowsAccepted = 0
              AND RowsRejected = 0
        )
            THROW 51918, 'The loader could not use the controlled result interface.', 1;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) ImportBatchId FROM stage.ImportBatch;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @LoaderDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) PersonId FROM core.Person;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @LoaderDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC(N'SELECT ImportBatchId FROM report.vw_SecurityPermissionProbe;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @LoaderDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC core.usp_BootstrapReferenceData @ReferencePayload = N'{}';
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @LoaderDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC core.usp_AssignDevice
                @PersonnelCode = N'TST-NOT-USED',
                @DeviceToken = N'TST-NOT-USED',
                @ValidFrom = '2026-01-01T00:00:00',
                @ValidTo = NULL;
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @LoaderDenied += 1; ELSE THROW;
        END CATCH;

        IF HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'ALTER') = 0
           AND HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'VIEW DEFINITION') = 0
            SET @LoaderDenied += 1;
        ELSE
            THROW 51919, 'The loader unexpectedly has report-schema alteration permission.', 1;

        REVERT;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() = -1
            ROLLBACK TRANSACTION;
        REVERT;
        THROW;
    END CATCH;

    IF @LoaderDenied <> 6
        THROW 51920, 'The loader negative-permission count is incorrect.', 1;

    EXECUTE AS USER = N'tst_report_reader';
    BEGIN TRY
        SELECT @ProbeValue = RowsReceived
        FROM report.vw_SecurityPermissionProbe
        WHERE ImportBatchId = @ImportBatchId;

        IF @ProbeValue <> 0
            THROW 51921, 'The reporter could not read the approved reporting view.', 1;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) ImportBatchId FROM stage.ImportBatch;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) PersonId FROM core.Person;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC stage.usp_GetImportBatchResult
                @ImportBatchId = @ImportBatchId,
                @FileChecksum = @Checksum;
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC core.usp_RefreshDailyAttendanceSummary
                @FromDate = NULL,
                @ThroughDate = NULL;
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            UPDATE report.vw_SecurityPermissionProbe
            SET RowsReceived = RowsReceived
            WHERE ImportBatchId = @ImportBatchId;
            SET @ReporterWriteSucceeded = 1;
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        IF @ReporterWriteSucceeded = 1
            THROW 51934, 'The reporter unexpectedly updated the reporting view.', 1;

        IF HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'ALTER') = 0
           AND HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'VIEW DEFINITION') = 0
            SET @ReporterDenied += 1;
        ELSE
            THROW 51922, 'The reporter unexpectedly has report-schema alteration permission.', 1;

        REVERT;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() = -1
            ROLLBACK TRANSACTION;
        REVERT;
        THROW;
    END CATCH;

    IF @ReporterDenied <> 6
        THROW 51923, 'The reporter negative-permission count is incorrect.', 1;

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF DATABASE_PRINCIPAL_ID(@LoaderUser) IS NOT NULL
   OR DATABASE_PRINCIPAL_ID(@ReporterUser) IS NOT NULL
   OR OBJECT_ID(N'report.vw_SecurityPermissionProbe', N'V') IS NOT NULL
   OR OBJECT_ID(N'report.vw_LoaderUnauthorized', N'V') IS NOT NULL
   OR OBJECT_ID(N'report.vw_ReporterUnauthorized', N'V') IS NOT NULL
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE FileChecksum = @Checksum)
    THROW 51924, 'Security verification fixtures remain after rollback.', 1;

SELECT
    CAST('security' AS varchar(10)) AS ComponentName,
    CAST('PASS' AS varchar(10)) AS AdministratorControl,
    CAST('PASS' AS varchar(10)) AS LoaderPositive,
    @LoaderDenied AS LoaderExpectedDenials,
    CAST('PASS' AS varchar(10)) AS ReporterPositive,
    @ReporterDenied AS ReporterExpectedDenials,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
