/*
    Verification for 012_create_reporting_views.sql.

    Exact metadata, aggregate formulas, report-reader behavior, context
    reversion, transaction rollback, and independent fixture cleanup are all
    verified without exposing a person-level reporting row.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF DATABASE_PRINCIPAL_ID(N'report_reader') IS NULL
    THROW 52000, 'The report_reader role is missing.', 1;

DECLARE @ExpectedColumns TABLE
(
    ViewName sysname NOT NULL,
    ColumnId int NOT NULL,
    ColumnName sysname NOT NULL,
    TypeName sysname NOT NULL,
    PRIMARY KEY (ViewName, ColumnId)
);

INSERT INTO @ExpectedColumns (ViewName, ColumnId, ColumnName, TypeName)
VALUES
    (N'vw_DailyAttendanceTrend', 1, N'AttendanceDateLocal', N'date'),
    (N'vw_DailyAttendanceTrend', 2, N'OfficeCode', N'varchar'),
    (N'vw_DailyAttendanceTrend', 3, N'OfficeName', N'nvarchar'),
    (N'vw_DailyAttendanceTrend', 4, N'OfficeCapacity', N'int'),
    (N'vw_DailyAttendanceTrend', 5, N'PersonDayCount', N'bigint'),
    (N'vw_DailyAttendanceTrend', 6, N'CardOnlyPersonDays', N'bigint'),
    (N'vw_DailyAttendanceTrend', 7, N'WifiOnlyPersonDays', N'bigint'),
    (N'vw_DailyAttendanceTrend', 8, N'BothPersonDays', N'bigint'),
    (N'vw_DailyAttendanceTrend', 9, N'BadgeObservedPersonDays', N'bigint'),
    (N'vw_DailyAttendanceTrend', 10, N'WifiObservedPersonDays', N'bigint'),
    (N'vw_DailyAttendanceTrend', 11, N'OccupancyRate', N'decimal'),
    (N'vw_DailyDepartmentAttendance', 1, N'AttendanceDateLocal', N'date'),
    (N'vw_DailyDepartmentAttendance', 2, N'OfficeCode', N'varchar'),
    (N'vw_DailyDepartmentAttendance', 3, N'OfficeName', N'nvarchar'),
    (N'vw_DailyDepartmentAttendance', 4, N'DepartmentCode', N'varchar'),
    (N'vw_DailyDepartmentAttendance', 5, N'DepartmentName', N'nvarchar'),
    (N'vw_DailyDepartmentAttendance', 6, N'PersonDayCount', N'bigint'),
    (N'vw_DailyDepartmentAttendance', 7, N'CardOnlyPersonDays', N'bigint'),
    (N'vw_DailyDepartmentAttendance', 8, N'WifiOnlyPersonDays', N'bigint'),
    (N'vw_DailyDepartmentAttendance', 9, N'BothPersonDays', N'bigint'),
    (N'vw_LoadQualitySummary', 1, N'SourceType', N'varchar'),
    (N'vw_LoadQualitySummary', 2, N'TerminalBatchCount', N'bigint'),
    (N'vw_LoadQualitySummary', 3, N'InProgressBatchCount', N'bigint'),
    (N'vw_LoadQualitySummary', 4, N'CompletedWithoutRejectsBatchCount', N'bigint'),
    (N'vw_LoadQualitySummary', 5, N'CompletedWithRejectsBatchCount', N'bigint'),
    (N'vw_LoadQualitySummary', 6, N'FailedBatchCount', N'bigint'),
    (N'vw_LoadQualitySummary', 7, N'RowsReceived', N'bigint'),
    (N'vw_LoadQualitySummary', 8, N'RowsAccepted', N'bigint'),
    (N'vw_LoadQualitySummary', 9, N'RowsRejected', N'bigint'),
    (N'vw_LoadQualitySummary', 10, N'AcceptanceRate', N'decimal'),
    (N'vw_ValidationIssueSummary', 1, N'SourceType', N'varchar'),
    (N'vw_ValidationIssueSummary', 2, N'ValidationCode', N'varchar'),
    (N'vw_ValidationIssueSummary', 3, N'RejectedRowCount', N'bigint');

IF
(
    SELECT COUNT(*)
    FROM sys.views AS view_object
    INNER JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = view_object.schema_id
    WHERE schema_name.name = N'report'
) <> 4
    THROW 52001, 'The report schema does not contain exactly four views.', 1;

IF EXISTS
(
    SELECT expected.ViewName, expected.ColumnId, expected.ColumnName, expected.TypeName
    FROM @ExpectedColumns AS expected
    EXCEPT
    SELECT view_object.name, column_name.column_id, column_name.name, type_name.name
    FROM sys.views AS view_object
    INNER JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = view_object.schema_id
    INNER JOIN sys.columns AS column_name
        ON column_name.object_id = view_object.object_id
    INNER JOIN sys.types AS type_name
        ON type_name.user_type_id = column_name.user_type_id
    WHERE schema_name.name = N'report'
)
OR EXISTS
(
    SELECT view_object.name, column_name.column_id, column_name.name, type_name.name
    FROM sys.views AS view_object
    INNER JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = view_object.schema_id
    INNER JOIN sys.columns AS column_name
        ON column_name.object_id = view_object.object_id
    INNER JOIN sys.types AS type_name
        ON type_name.user_type_id = column_name.user_type_id
    WHERE schema_name.name = N'report'
    EXCEPT
    SELECT expected.ViewName, expected.ColumnId, expected.ColumnName, expected.TypeName
    FROM @ExpectedColumns AS expected
)
    THROW 52002, 'A reporting view has an unexpected column contract.', 1;

IF EXISTS
(
    SELECT 1
    FROM @ExpectedColumns
    WHERE ColumnName IN
    (
        N'PersonId', N'PersonnelCode', N'DisplayName', N'SyntheticEmail',
        N'DeviceId', N'DeviceToken', N'AccessPointId', N'AccessPointCode',
        N'FirstObservedAtUtc', N'LastObservedAtUtc', N'ImportBatchId',
        N'SourceFileName', N'FileChecksum', N'ErrorDescription'
    )
)
    THROW 52003, 'A sensitive column crossed the reporting boundary.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.views AS view_object
    INNER JOIN sys.schemas AS schema_name
        ON schema_name.schema_id = view_object.schema_id
    WHERE schema_name.name = N'report'
      AND view_object.principal_id IS NOT NULL
)
    THROW 52004, 'A reporting view overrides the dbo-owned schema boundary.', 1;

DECLARE @ReporterUser sysname = N'tst_report_views';
DECLARE @OfficeId int;
DECLARE @DepartmentAId int;
DECLARE @DepartmentBId int;
DECLARE @PersonAId int;
DECLARE @PersonBId int;
DECLARE @PersonCId int;
DECLARE @PartialBatchId bigint;
DECLARE @PreTerminal bigint;
DECLARE @PreInProgress bigint;
DECLARE @PreCompleted bigint;
DECLARE @PrePartial bigint;
DECLARE @PreFailed bigint;
DECLARE @PreReceived bigint;
DECLARE @PreAccepted bigint;
DECLARE @PreRejected bigint;
DECLARE @ReporterPositive int = 0;
DECLARE @ReporterDenied int = 0;
DECLARE @TransactionRollback varchar(10) = 'FAIL';

IF DATABASE_PRINCIPAL_ID(@ReporterUser) IS NOT NULL
   OR EXISTS (SELECT 1 FROM core.Office WHERE OfficeCode = 'TST-RPT-01')
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE SourceFileName LIKE N'tst-report-%')
    THROW 52005, 'A reporting-test fixture already exists.', 1;

SELECT
    @PreTerminal = TerminalBatchCount,
    @PreInProgress = InProgressBatchCount,
    @PreCompleted = CompletedWithoutRejectsBatchCount,
    @PrePartial = CompletedWithRejectsBatchCount,
    @PreFailed = FailedBatchCount,
    @PreReceived = RowsReceived,
    @PreAccepted = RowsAccepted,
    @PreRejected = RowsRejected
FROM report.vw_LoadQualitySummary
WHERE SourceType = 'CARD';

BEGIN TRANSACTION;

BEGIN TRY
    EXEC(N'CREATE USER tst_report_views WITHOUT LOGIN;');
    ALTER ROLE report_reader ADD MEMBER tst_report_views;

    INSERT INTO core.Office (OfficeCode, DisplayName, TimeZoneName, Capacity)
    VALUES ('TST-RPT-01', N'Synthetic Reporting Office', N'GMT Standard Time', 17);
    SET @OfficeId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Department (DepartmentCode, DepartmentName)
    VALUES ('TST-RPT-A', N'Synthetic Reporting Department A');
    SET @DepartmentAId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Department (DepartmentCode, DepartmentName)
    VALUES ('TST-RPT-B', N'Synthetic Reporting Department B');
    SET @DepartmentBId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Person
    (
        PersonnelCode, DisplayName, SyntheticEmail, DepartmentId, ValidFrom
    )
    VALUES
        ('TST-RPT-001', N'Synthetic Reporting Person 1', 'tst-rpt-001@attendance-lab.example', @DepartmentAId, '2099-01-01'),
        ('TST-RPT-002', N'Synthetic Reporting Person 2', 'tst-rpt-002@attendance-lab.example', @DepartmentAId, '2099-01-01'),
        ('TST-RPT-003', N'Synthetic Reporting Person 3', 'tst-rpt-003@attendance-lab.example', @DepartmentBId, '2099-01-01');

    SELECT @PersonAId = PersonId FROM core.Person WHERE PersonnelCode = 'TST-RPT-001';
    SELECT @PersonBId = PersonId FROM core.Person WHERE PersonnelCode = 'TST-RPT-002';
    SELECT @PersonCId = PersonId FROM core.Person WHERE PersonnelCode = 'TST-RPT-003';

    INSERT INTO core.DailyAttendanceSummary
    (
        AttendanceDateLocal, OfficeId, PersonId, DetectionMethod,
        FirstObservedAtUtc, LastObservedAtUtc, CardSignalCount, WifiSignalCount
    )
    VALUES
        ('2099-01-05', @OfficeId, @PersonAId, 'CARD', '2099-01-05T08:00:00', '2099-01-05T08:00:00', 1, 0),
        ('2099-01-05', @OfficeId, @PersonBId, 'BOTH', '2099-01-05T08:15:00', '2099-01-05T17:00:00', 1, 2),
        ('2099-01-05', @OfficeId, @PersonCId, 'WIFI', '2099-01-05T09:00:00', '2099-01-05T16:30:00', 0, 2);

    INSERT INTO stage.ImportBatch
    (
        SourceType, SourceFileName, FileChecksum, CompletedAt, Status,
        RowsReceived, RowsAccepted, RowsRejected
    )
    VALUES
        ('CARD', N'tst-report-completed.csv', HASHBYTES('SHA2_256', 'TST-REPORT-COMPLETED'), SYSUTCDATETIME(), 'COMPLETED', 4, 4, 0),
        ('CARD', N'tst-report-partial.csv', HASHBYTES('SHA2_256', 'TST-REPORT-PARTIAL'), SYSUTCDATETIME(), 'PARTIAL', 3, 2, 1),
        ('CARD', N'tst-report-failed.csv', HASHBYTES('SHA2_256', 'TST-REPORT-FAILED'), SYSUTCDATETIME(), 'FAILED', 2, 0, 0),
        ('CARD', N'tst-report-started.csv', HASHBYTES('SHA2_256', 'TST-REPORT-STARTED'), NULL, 'STARTED', 0, 0, 0);

    SELECT @PartialBatchId = ImportBatchId
    FROM stage.ImportBatch
    WHERE SourceFileName = N'tst-report-partial.csv';

    INSERT INTO stage.ImportError
    (
        ImportBatchId, SourceTable, SourceRowNumber, ValidationCode, ErrorDescription
    )
    VALUES
    (
        @PartialBatchId, 'CARD_ACCESS_EVENT', 3,
        'TST_REPORT_VALIDATION', N'Synthetic reporting verification rejection.'
    );

    IF NOT EXISTS
    (
        SELECT 1
        FROM report.vw_DailyAttendanceTrend
        WHERE AttendanceDateLocal = '2099-01-05'
          AND OfficeCode = 'TST-RPT-01'
          AND OfficeName = N'Synthetic Reporting Office'
          AND OfficeCapacity = 17
          AND PersonDayCount = 3
          AND CardOnlyPersonDays = 1
          AND WifiOnlyPersonDays = 1
          AND BothPersonDays = 1
          AND BadgeObservedPersonDays = 2
          AND WifiObservedPersonDays = 2
          AND OccupancyRate = CONVERT(decimal(9, 6), 0.176471)
    )
        THROW 52006, 'The daily attendance trend formula is incorrect.', 1;

    IF
    (
        SELECT COUNT(*)
        FROM report.vw_DailyDepartmentAttendance
        WHERE AttendanceDateLocal = '2099-01-05'
          AND OfficeCode = 'TST-RPT-01'
          AND
          (
              (DepartmentCode = 'TST-RPT-A' AND PersonDayCount = 2 AND CardOnlyPersonDays = 1 AND WifiOnlyPersonDays = 0 AND BothPersonDays = 1)
              OR
              (DepartmentCode = 'TST-RPT-B' AND PersonDayCount = 1 AND CardOnlyPersonDays = 0 AND WifiOnlyPersonDays = 1 AND BothPersonDays = 0)
          )
    ) <> 2
        THROW 52007, 'The department attendance formula is incorrect.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM report.vw_LoadQualitySummary
        WHERE SourceType = 'CARD'
          AND TerminalBatchCount = @PreTerminal + 3
          AND InProgressBatchCount = @PreInProgress + 1
          AND CompletedWithoutRejectsBatchCount = @PreCompleted + 1
          AND CompletedWithRejectsBatchCount = @PrePartial + 1
          AND FailedBatchCount = @PreFailed + 1
          AND RowsReceived = @PreReceived + 9
          AND RowsAccepted = @PreAccepted + 6
          AND RowsRejected = @PreRejected + 1
          AND AcceptanceRate = CONVERT
          (
              decimal(9, 6),
              CONVERT(decimal(19, 6), @PreAccepted + 6)
                  / NULLIF(CONVERT(decimal(19, 6), @PreReceived + 9), 0)
          )
    )
        THROW 52008, 'The load-quality formula is incorrect.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM report.vw_ValidationIssueSummary
        WHERE SourceType = 'CARD'
          AND ValidationCode = 'TST_REPORT_VALIDATION'
          AND RejectedRowCount = 1
    )
        THROW 52009, 'The validation-summary formula is incorrect.', 1;

    EXECUTE AS USER = N'tst_report_views';
    BEGIN TRY
        IF EXISTS (SELECT 1 FROM report.vw_DailyAttendanceTrend WHERE OfficeCode = 'TST-RPT-01')
            SET @ReporterPositive += 1;
        IF EXISTS (SELECT 1 FROM report.vw_DailyDepartmentAttendance WHERE OfficeCode = 'TST-RPT-01')
            SET @ReporterPositive += 1;
        IF EXISTS (SELECT 1 FROM report.vw_LoadQualitySummary WHERE SourceType = 'CARD')
            SET @ReporterPositive += 1;
        IF EXISTS (SELECT 1 FROM report.vw_ValidationIssueSummary WHERE ValidationCode = 'TST_REPORT_VALIDATION')
            SET @ReporterPositive += 1;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) PersonId FROM core.Person;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        BEGIN TRY
            EXEC(N'SELECT TOP (1) ImportBatchId FROM stage.ImportBatch;');
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER() = 229 SET @ReporterDenied += 1; ELSE THROW;
        END CATCH;

        IF HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'UPDATE') = 0
           AND HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'ALTER') = 0
           AND HAS_PERMS_BY_NAME(N'report', 'SCHEMA', 'VIEW DEFINITION') = 0
            SET @ReporterDenied += 1;
        ELSE
            THROW 52010, 'The reporter has an unexpected report-schema permission.', 1;

        REVERT;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() = -1
            ROLLBACK TRANSACTION;
        REVERT;
        THROW;
    END CATCH;

    IF @ReporterPositive <> 4 OR @ReporterDenied <> 3
        THROW 52011, 'The report-reader behavior counts are incorrect.', 1;

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF DATABASE_PRINCIPAL_ID(@ReporterUser) IS NOT NULL
   OR EXISTS (SELECT 1 FROM core.Office WHERE OfficeCode = 'TST-RPT-01')
   OR EXISTS (SELECT 1 FROM core.Department WHERE DepartmentCode LIKE 'TST-RPT-%')
   OR EXISTS (SELECT 1 FROM core.Person WHERE PersonnelCode LIKE 'TST-RPT-%')
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE SourceFileName LIKE N'tst-report-%')
   OR EXISTS
   (
       SELECT 1
       FROM sys.database_role_members AS membership
       INNER JOIN sys.database_principals AS member_name
           ON member_name.principal_id = membership.member_principal_id
       WHERE member_name.name = @ReporterUser
   )
    THROW 52012, 'A reporting verification fixture remains after rollback.', 1;

SELECT
    CAST('report' AS varchar(10)) AS SchemaName,
    CAST('PASS' AS varchar(10)) AS ExactMetadata,
    CAST('PASS' AS varchar(10)) AS AggregateFormulas,
    @ReporterPositive AS ReporterPositiveViews,
    @ReporterDenied AS ReporterExpectedDenials,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
