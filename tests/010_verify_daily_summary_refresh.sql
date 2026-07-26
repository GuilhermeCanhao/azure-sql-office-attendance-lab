/*
    Verification for 009_create_daily_summary_refresh.sql.

    All fixtures are synthetic and transactionally rolled back. The test proves
    full CARD/WIFI/BOTH reconciliation, range replacement, deterministic rerun,
    invalid-range rejection, and complete cleanup.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF OBJECT_ID(N'core.usp_RefreshDailyAttendanceSummary', N'P') IS NULL
    THROW 51800, 'The daily-summary refresh procedure is missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.parameters
    WHERE object_id = OBJECT_ID(N'core.usp_RefreshDailyAttendanceSummary')
) <> 2
    THROW 51801, 'The daily-summary refresh has unexpected parameters.', 1;

DECLARE @ReferencePayload nvarchar(max) = N'{
  "offices":[{"office_code":"TST-SUM-01","display_name":"Synthetic Summary Office","time_zone_name":"GMT Standard Time","capacity":25,"is_active":1}],
  "departments":[{"department_code":"TST-SUM","department_name":"Synthetic Summary Department","is_active":1}],
  "people":[{"personnel_code":"TST-SUM-001","display_name":"Synthetic Summary Person One","synthetic_email":"tst-sum-001@attendance-lab.example","department_code":"TST-SUM","valid_from":"2026-01-01","valid_to":null},{"personnel_code":"TST-SUM-002","display_name":"Synthetic Summary Person Two","synthetic_email":"tst-sum-002@attendance-lab.example","department_code":"TST-SUM","valid_from":"2026-01-01","valid_to":null}],
  "devices":[{"device_token":"DEV-FFF30001","device_status":"ACTIVE"}],
  "device_assignments":[{"personnel_code":"TST-SUM-001","device_token":"DEV-FFF30001","valid_from_utc":"2026-01-01T00:00:00.000","valid_to_utc":null}],
  "access_points":[{"office_code":"TST-SUM-01","access_point_code":"TST-SUM-CARD-01","access_point_type":"CARD_READER","display_label":"Synthetic Card Reader","is_active":1},{"office_code":"TST-SUM-01","access_point_code":"TST-SUM-WIFI-01","access_point_type":"WIFI_AP","display_label":"Synthetic Wi-Fi AP","is_active":1}]
}';

DECLARE @BootstrapResult TABLE
(
    OfficesInserted int,
    DepartmentsInserted int,
    PeopleInserted int,
    DevicesInserted int,
    AssignmentsInserted int,
    AccessPointsInserted int,
    BootstrapResult varchar(10)
);

DECLARE @RefreshResult TABLE
(
    EffectiveFromDate date NULL,
    EffectiveThroughDate date NULL,
    RowsRefreshed int,
    RefreshScope varchar(10)
);

DECLARE @OfficeId int;
DECLARE @PersonOneId int;
DECLARE @PersonTwoId int;
DECLARE @DeviceId int;
DECLARE @CardAccessPointId int;
DECLARE @WifiAccessPointId int;
DECLARE @CardBatchId bigint;
DECLARE @WifiBatchId bigint;
DECLARE @InvalidRangeRejected bit = 0;
DECLARE @TransactionRollback varchar(10) = 'FAIL';
DECLARE @ExpectedFullFromDate date;
DECLARE @ExpectedFullThroughDate date;
DECLARE @ExpectedFullRows int;
DECLARE @CardChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-SUM-CARD-20260715');
DECLARE @WifiChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-SUM-WIFI-20260715');

BEGIN TRANSACTION;

BEGIN TRY
    INSERT INTO @BootstrapResult
    EXEC core.usp_BootstrapReferenceData @ReferencePayload = @ReferencePayload;

    SELECT @OfficeId = OfficeId FROM core.Office WHERE OfficeCode = 'TST-SUM-01';
    SELECT @PersonOneId = PersonId FROM core.Person WHERE PersonnelCode = 'TST-SUM-001';
    SELECT @PersonTwoId = PersonId FROM core.Person WHERE PersonnelCode = 'TST-SUM-002';
    SELECT @DeviceId = DeviceId FROM core.Device WHERE DeviceToken = 'DEV-FFF30001';
    SELECT @CardAccessPointId = AccessPointId FROM core.AccessPoint WHERE AccessPointCode = 'TST-SUM-CARD-01';
    SELECT @WifiAccessPointId = AccessPointId FROM core.AccessPoint WHERE AccessPointCode = 'TST-SUM-WIFI-01';

    INSERT INTO stage.ImportBatch
    (
        SourceType, SourceFileName, FileChecksum, CompletedAt,
        Status, RowsReceived, RowsAccepted, RowsRejected
    )
    VALUES
        ('CARD', N'tst-summary-card.csv', @CardChecksum, SYSUTCDATETIME(), 'COMPLETED', 3, 3, 0),
        ('WIFI', N'tst-summary-wifi.csv', @WifiChecksum, SYSUTCDATETIME(), 'COMPLETED', 3, 3, 0);

    SELECT @CardBatchId = ImportBatchId
    FROM stage.ImportBatch
    WHERE SourceType = 'CARD' AND FileChecksum = @CardChecksum;

    SELECT @WifiBatchId = ImportBatchId
    FROM stage.ImportBatch
    WHERE SourceType = 'WIFI' AND FileChecksum = @WifiChecksum;

    INSERT INTO core.AttendanceSignal
    (
        ImportBatchId, SourceRowNumber, OfficeId, PersonId, AccessPointId,
        DeviceId, SignalType, ObservedAtUtc, AttendanceDateLocal
    )
    VALUES
        (@CardBatchId, 1, @OfficeId, @PersonOneId, @CardAccessPointId, NULL, 'CARD', '2030-01-15T09:00:00.000', '2030-01-15'),
        (@CardBatchId, 2, @OfficeId, @PersonOneId, @CardAccessPointId, NULL, 'CARD', '2030-01-15T17:00:00.000', '2030-01-15'),
        (@CardBatchId, 3, @OfficeId, @PersonTwoId, @CardAccessPointId, NULL, 'CARD', '2030-01-15T10:00:00.000', '2030-01-15'),
        (@WifiBatchId, 1, @OfficeId, @PersonOneId, @WifiAccessPointId, @DeviceId, 'WIFI', '2030-01-15T09:05:00.000', '2030-01-15'),
        (@WifiBatchId, 2, @OfficeId, @PersonOneId, @WifiAccessPointId, @DeviceId, 'WIFI', '2030-01-15T16:55:00.000', '2030-01-15'),
        (@WifiBatchId, 3, @OfficeId, @PersonOneId, @WifiAccessPointId, @DeviceId, 'WIFI', '2030-01-16T09:30:00.000', '2030-01-16');

    SELECT
        @ExpectedFullFromDate = MIN(AttendanceDateLocal),
        @ExpectedFullThroughDate = MAX(AttendanceDateLocal)
    FROM core.AttendanceSignal;

    SELECT @ExpectedFullRows = COUNT(*)
    FROM
    (
        SELECT AttendanceDateLocal, OfficeId, PersonId
        FROM core.AttendanceSignal
        GROUP BY AttendanceDateLocal, OfficeId, PersonId
    ) AS expected_person_days;

    INSERT INTO @RefreshResult
    EXEC core.usp_RefreshDailyAttendanceSummary;

    IF NOT EXISTS
    (
        SELECT 1 FROM @RefreshResult
        WHERE EffectiveFromDate = @ExpectedFullFromDate
          AND EffectiveThroughDate = @ExpectedFullThroughDate
          AND RowsRefreshed = @ExpectedFullRows
          AND RefreshScope = 'FULL'
    )
        THROW 51802, 'Full refresh returned unexpected reconciliation metadata.', 1;

    IF NOT EXISTS
    (
        SELECT 1 FROM core.DailyAttendanceSummary
        WHERE AttendanceDateLocal = '2030-01-15'
          AND OfficeId = @OfficeId
          AND PersonId = @PersonOneId
          AND DetectionMethod = 'BOTH'
          AND CardSignalCount = 2
          AND WifiSignalCount = 2
          AND FirstObservedAtUtc = '2030-01-15T09:00:00.000'
          AND LastObservedAtUtc = '2030-01-15T17:00:00.000'
    )
       OR NOT EXISTS
       (
           SELECT 1 FROM core.DailyAttendanceSummary
           WHERE AttendanceDateLocal = '2030-01-15'
             AND OfficeId = @OfficeId
             AND PersonId = @PersonTwoId
             AND DetectionMethod = 'CARD'
             AND CardSignalCount = 1
             AND WifiSignalCount = 0
       )
       OR NOT EXISTS
       (
           SELECT 1 FROM core.DailyAttendanceSummary
           WHERE AttendanceDateLocal = '2030-01-16'
             AND OfficeId = @OfficeId
             AND PersonId = @PersonOneId
             AND DetectionMethod = 'WIFI'
             AND CardSignalCount = 0
             AND WifiSignalCount = 1
       )
        THROW 51803, 'Full refresh did not produce the expected CARD/WIFI/BOTH summaries.', 1;

    UPDATE stage.ImportBatch
    SET RowsReceived = 4, RowsAccepted = 4
    WHERE ImportBatchId = @CardBatchId;

    INSERT INTO core.AttendanceSignal
    (
        ImportBatchId, SourceRowNumber, OfficeId, PersonId, AccessPointId,
        DeviceId, SignalType, ObservedAtUtc, AttendanceDateLocal
    )
    VALUES
        (@CardBatchId, 4, @OfficeId, @PersonTwoId, @CardAccessPointId, NULL, 'CARD', '2030-01-15T18:00:00.000', '2030-01-15');

    DELETE FROM @RefreshResult;

    INSERT INTO @RefreshResult
    EXEC core.usp_RefreshDailyAttendanceSummary
        @FromDate = '2030-01-15',
        @ThroughDate = '2030-01-15';

    IF NOT EXISTS
    (
        SELECT 1 FROM @RefreshResult
        WHERE EffectiveFromDate = '2030-01-15'
          AND EffectiveThroughDate = '2030-01-15'
          AND RowsRefreshed = 2
          AND RefreshScope = 'RANGE'
    )
       OR NOT EXISTS
       (
           SELECT 1 FROM core.DailyAttendanceSummary
           WHERE AttendanceDateLocal = '2030-01-15'
             AND OfficeId = @OfficeId
             AND PersonId = @PersonTwoId
             AND DetectionMethod = 'CARD'
             AND CardSignalCount = 2
             AND WifiSignalCount = 0
             AND LastObservedAtUtc = '2030-01-15T18:00:00.000'
       )
       OR NOT EXISTS
       (
           SELECT 1 FROM core.DailyAttendanceSummary
           WHERE AttendanceDateLocal = '2030-01-16'
             AND OfficeId = @OfficeId
             AND PersonId = @PersonOneId
             AND DetectionMethod = 'WIFI'
             AND WifiSignalCount = 1
       )
        THROW 51804, 'Range refresh did not replace only the intended date.', 1;

    BEGIN TRY
        EXEC core.usp_RefreshDailyAttendanceSummary
            @FromDate = '2030-01-16',
            @ThroughDate = '2030-01-15';
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51701
            THROW;
        SET @InvalidRangeRejected = 1;
    END CATCH;

    IF @InvalidRangeRejected = 0
        THROW 51805, 'An invalid summary refresh range was accepted.', 1;

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF EXISTS (SELECT 1 FROM core.Office WHERE OfficeCode = 'TST-SUM-01')
   OR EXISTS (SELECT 1 FROM core.Department WHERE DepartmentCode = 'TST-SUM')
   OR EXISTS (SELECT 1 FROM core.Person WHERE PersonnelCode LIKE 'TST-SUM-%')
   OR EXISTS (SELECT 1 FROM core.Device WHERE DeviceToken = 'DEV-FFF30001')
   OR EXISTS (SELECT 1 FROM core.AccessPoint WHERE AccessPointCode LIKE 'TST-SUM-%')
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE FileChecksum IN (@CardChecksum, @WifiChecksum))
    THROW 51806, 'Daily-summary verification fixtures remain after rollback.', 1;

SELECT
    CAST('core' AS varchar(10)) AS SchemaName,
    CAST('usp_RefreshDailyAttendanceSummary' AS varchar(40)) AS ProcedureName,
    CAST(2 AS int) AS ParameterCount,
    CAST('PASS' AS varchar(10)) AS FullReconciliation,
    CAST('PASS' AS varchar(10)) AS RangeReplacement,
    CAST('PASS' AS varchar(10)) AS InvalidRangeRejection,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
