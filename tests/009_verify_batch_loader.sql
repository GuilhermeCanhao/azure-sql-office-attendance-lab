/*
    Verification for 008_create_batch_loader.sql.

    The fixtures are synthetic and remain inside one transaction. The test
    proves card and Wi-Fi validation precedence, accepted/rejected
    reconciliation, authoritative lineage, duplicate-file idempotency,
    recoverable failure, and complete rollback cleanup.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF OBJECT_ID(N'stage.usp_BeginImportBatch', N'P') IS NULL
   OR OBJECT_ID(N'stage.usp_AppendImportChunk', N'P') IS NULL
   OR OBJECT_ID(N'stage.usp_FinalizeImportBatch', N'P') IS NULL
   OR OBJECT_ID(N'stage.usp_FailImportBatch', N'P') IS NULL
    THROW 51600, 'One or more batch-loader procedures are missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.parameters
    WHERE object_id IN
    (
        OBJECT_ID(N'stage.usp_BeginImportBatch'),
        OBJECT_ID(N'stage.usp_AppendImportChunk'),
        OBJECT_ID(N'stage.usp_FinalizeImportBatch'),
        OBJECT_ID(N'stage.usp_FailImportBatch')
    )
) <> 9
    THROW 51601, 'The batch-loader procedures have unexpected parameters.', 1;

DECLARE @ReferencePayload nvarchar(max) = N'{
  "offices":[{"office_code":"TST-LOAD-01","display_name":"Synthetic Loader Office","time_zone_name":"GMT Standard Time","capacity":25,"is_active":1}],
  "departments":[{"department_code":"TST-LOAD","department_name":"Synthetic Loader Department","is_active":1}],
  "people":[{"personnel_code":"TST-LOAD-001","display_name":"Synthetic Loader Person","synthetic_email":"tst-load-001@attendance-lab.example","department_code":"TST-LOAD","valid_from":"2026-01-01","valid_to":"2026-02-01"}],
  "devices":[{"device_token":"DEV-FFF20001","device_status":"ACTIVE"},{"device_token":"DEV-FFF20002","device_status":"ACTIVE"}],
  "device_assignments":[{"personnel_code":"TST-LOAD-001","device_token":"DEV-FFF20001","valid_from_utc":"2026-01-01T00:00:00.000","valid_to_utc":"2026-02-01T00:00:00.000"}],
  "access_points":[{"office_code":"TST-LOAD-01","access_point_code":"TST-LOAD-CARD-01","access_point_type":"CARD_READER","display_label":"Synthetic Card Reader","is_active":1},{"office_code":"TST-LOAD-01","access_point_code":"TST-LOAD-WIFI-01","access_point_type":"WIFI_AP","display_label":"Synthetic Wi-Fi AP","is_active":1}]
}';

DECLARE @CardRows nvarchar(max) = N'[
  {"source_row_number":1,"observed_at_raw":"2026-01-15T09:00:00.000Z","personnel_code_raw":"TST-LOAD-001","access_point_code_raw":"TST-LOAD-CARD-01"},
  {"source_row_number":2,"observed_at_raw":"not-a-time","personnel_code_raw":"UNKNOWN","access_point_code_raw":"UNKNOWN"},
  {"source_row_number":3,"observed_at_raw":"2026-01-15T09:05:00.000Z","personnel_code_raw":"","access_point_code_raw":"TST-LOAD-CARD-01"},
  {"source_row_number":4,"observed_at_raw":"2026-01-15T09:10:00.000Z","personnel_code_raw":"UNKNOWN","access_point_code_raw":"TST-LOAD-CARD-01"},
  {"source_row_number":5,"observed_at_raw":"2026-01-15T09:15:00.000Z","personnel_code_raw":"TST-LOAD-001","access_point_code_raw":"UNKNOWN"},
  {"source_row_number":6,"observed_at_raw":"2026-02-02T09:20:00.000Z","personnel_code_raw":"TST-LOAD-001","access_point_code_raw":"TST-LOAD-CARD-01"}
]';

DECLARE @WifiRows nvarchar(max) = N'[
  {"source_row_number":1,"observed_at_raw":"2026-01-15T09:00:00.000Z","device_token_raw":"DEV-FFF20001","access_point_code_raw":"TST-LOAD-WIFI-01","signal_strength_raw":"-55"},
  {"source_row_number":2,"observed_at_raw":"not-a-time","device_token_raw":"UNKNOWN","access_point_code_raw":"UNKNOWN","signal_strength_raw":"bad"},
  {"source_row_number":3,"observed_at_raw":"2026-01-15T09:05:00.000Z","device_token_raw":"UNKNOWN","access_point_code_raw":"TST-LOAD-WIFI-01","signal_strength_raw":"-60"},
  {"source_row_number":4,"observed_at_raw":"2026-01-15T09:10:00.000Z","device_token_raw":"DEV-FFF20001","access_point_code_raw":"UNKNOWN","signal_strength_raw":"-60"},
  {"source_row_number":5,"observed_at_raw":"2026-01-15T09:15:00.000Z","device_token_raw":"DEV-FFF20001","access_point_code_raw":"TST-LOAD-WIFI-01","signal_strength_raw":"8"},
  {"source_row_number":6,"observed_at_raw":"2026-01-15T09:20:00.000Z","device_token_raw":"DEV-FFF20002","access_point_code_raw":"TST-LOAD-WIFI-01","signal_strength_raw":"-65"}
]';

DECLARE @CardChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-BATCH-CARD-20260715');
DECLARE @WifiChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-BATCH-WIFI-20260715');
DECLARE @FailureChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-BATCH-FAIL-20260715');
DECLARE @CardBatchId bigint;
DECLARE @WifiBatchId bigint;
DECLARE @FailureBatchId bigint;
DECLARE @TransactionRollback varchar(10) = 'FAIL';

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

DECLARE @BeginResult TABLE
(
    ImportBatchId bigint,
    BeginResult varchar(20)
);

DECLARE @AppendResult TABLE
(
    ImportBatchId bigint,
    RowsAppended int
);

DECLARE @FinalizeResult TABLE
(
    ImportBatchId bigint,
    RowsReceived int,
    RowsAccepted int,
    RowsRejected int,
    FinalStatus varchar(10)
);

DECLARE @FailResult TABLE
(
    ImportBatchId bigint,
    FinalStatus varchar(10)
);

BEGIN TRANSACTION;

BEGIN TRY
    INSERT INTO @BootstrapResult
    EXEC core.usp_BootstrapReferenceData @ReferencePayload = @ReferencePayload;

    INSERT INTO @BeginResult
    EXEC stage.usp_BeginImportBatch
        @SourceType = 'CARD',
        @SourceFileName = N'tst-card.csv',
        @FileChecksum = @CardChecksum;

    SELECT @CardBatchId = ImportBatchId
    FROM @BeginResult
    WHERE BeginResult = 'READY';

    IF @CardBatchId IS NULL
        THROW 51602, 'Card batch did not enter READY state.', 1;

    INSERT INTO @AppendResult
    EXEC stage.usp_AppendImportChunk
        @ImportBatchId = @CardBatchId,
        @RowsJson = @CardRows;

    INSERT INTO @FinalizeResult
    EXEC stage.usp_FinalizeImportBatch
        @ImportBatchId = @CardBatchId,
        @ExpectedRows = 6;

    IF NOT EXISTS
    (
        SELECT 1 FROM @FinalizeResult
        WHERE ImportBatchId = @CardBatchId
          AND RowsReceived = 6
          AND RowsAccepted = 1
          AND RowsRejected = 5
          AND FinalStatus = 'PARTIAL'
    )
        THROW 51603, 'Card batch did not reconcile to the expected PARTIAL result.', 1;

    IF
    (
        SELECT STRING_AGG(CONCAT(SourceRowNumber, ':', ValidationCode), ',')
            WITHIN GROUP (ORDER BY SourceRowNumber)
        FROM stage.ImportError
        WHERE ImportBatchId = @CardBatchId
    ) <> '2:INVALID_TIMESTAMP,3:BLANK_PERSONNEL_CODE,4:UNKNOWN_PERSONNEL,5:UNKNOWN_ACCESS_POINT,6:PERSON_OUTSIDE_VALIDITY'
        THROW 51604, 'Card validation precedence is incorrect.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM core.AttendanceSignal
        WHERE ImportBatchId = @CardBatchId
          AND SourceRowNumber = 1
          AND SignalType = 'CARD'
          AND DeviceId IS NULL
          AND AttendanceDateLocal = '2026-01-15'
    )
        THROW 51605, 'The accepted card row did not produce the expected fact.', 1;

    DELETE FROM @BeginResult;
    INSERT INTO @BeginResult
    EXEC stage.usp_BeginImportBatch
        @SourceType = 'CARD',
        @SourceFileName = N'tst-card-renamed.csv',
        @FileChecksum = @CardChecksum;

    IF NOT EXISTS
    (
        SELECT 1 FROM @BeginResult
        WHERE ImportBatchId = @CardBatchId
          AND BeginResult = 'ALREADY_PROCESSED'
    )
        THROW 51606, 'A duplicate completed checksum was not treated as already processed.', 1;

    DELETE FROM @BeginResult;
    DELETE FROM @AppendResult;
    DELETE FROM @FinalizeResult;

    INSERT INTO @BeginResult
    EXEC stage.usp_BeginImportBatch
        @SourceType = 'WIFI',
        @SourceFileName = N'tst-wifi.csv',
        @FileChecksum = @WifiChecksum;

    SELECT @WifiBatchId = ImportBatchId
    FROM @BeginResult
    WHERE BeginResult = 'READY';

    IF @WifiBatchId IS NULL
        THROW 51607, 'Wi-Fi batch did not enter READY state.', 1;

    INSERT INTO @AppendResult
    EXEC stage.usp_AppendImportChunk
        @ImportBatchId = @WifiBatchId,
        @RowsJson = @WifiRows;

    INSERT INTO @FinalizeResult
    EXEC stage.usp_FinalizeImportBatch
        @ImportBatchId = @WifiBatchId,
        @ExpectedRows = 6;

    IF NOT EXISTS
    (
        SELECT 1 FROM @FinalizeResult
        WHERE ImportBatchId = @WifiBatchId
          AND RowsReceived = 6
          AND RowsAccepted = 1
          AND RowsRejected = 5
          AND FinalStatus = 'PARTIAL'
    )
        THROW 51608, 'Wi-Fi batch did not reconcile to the expected PARTIAL result.', 1;

    IF
    (
        SELECT STRING_AGG(CONCAT(SourceRowNumber, ':', ValidationCode), ',')
            WITHIN GROUP (ORDER BY SourceRowNumber)
        FROM stage.ImportError
        WHERE ImportBatchId = @WifiBatchId
    ) <> '2:INVALID_TIMESTAMP,3:UNKNOWN_DEVICE,4:UNKNOWN_ACCESS_POINT,5:INVALID_SIGNAL_STRENGTH,6:DEVICE_NOT_ASSIGNED'
        THROW 51609, 'Wi-Fi validation precedence is incorrect.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM core.AttendanceSignal
        WHERE ImportBatchId = @WifiBatchId
          AND SourceRowNumber = 1
          AND SignalType = 'WIFI'
          AND DeviceId IS NOT NULL
          AND AttendanceDateLocal = '2026-01-15'
    )
        THROW 51610, 'The accepted Wi-Fi row did not produce the expected fact.', 1;

    DELETE FROM @BeginResult;
    DELETE FROM @AppendResult;

    INSERT INTO @BeginResult
    EXEC stage.usp_BeginImportBatch
        @SourceType = 'CARD',
        @SourceFileName = N'tst-failure.csv',
        @FileChecksum = @FailureChecksum;

    SELECT @FailureBatchId = ImportBatchId
    FROM @BeginResult
    WHERE BeginResult = 'READY';

    INSERT INTO @AppendResult
    EXEC stage.usp_AppendImportChunk
        @ImportBatchId = @FailureBatchId,
        @RowsJson = N'[{"source_row_number":1,"observed_at_raw":"2026-01-15T09:00:00.000Z","personnel_code_raw":"TST-LOAD-001","access_point_code_raw":"TST-LOAD-CARD-01"}]';

    INSERT INTO @FailResult
    EXEC stage.usp_FailImportBatch
        @ImportBatchId = @FailureBatchId,
        @ErrorCategory = 'CLIENT_ABORT';

    IF NOT EXISTS
    (
        SELECT 1
        FROM stage.ImportBatch
        WHERE ImportBatchId = @FailureBatchId
          AND Status = 'FAILED'
          AND RowsReceived = 0
          AND RowsAccepted = 0
          AND RowsRejected = 0
          AND ErrorMessage = N'LOAD_CLIENT_ABORT'
    )
       OR EXISTS
       (
           SELECT 1 FROM stage.CardAccessEvent WHERE ImportBatchId = @FailureBatchId
       )
        THROW 51611, 'Failure finalization did not clean and reconcile partial work.', 1;

    DELETE FROM @BeginResult;
    DELETE FROM @FailResult;

    INSERT INTO @BeginResult
    EXEC stage.usp_BeginImportBatch
        @SourceType = 'CARD',
        @SourceFileName = N'tst-failure-retry.csv',
        @FileChecksum = @FailureChecksum;

    IF NOT EXISTS
    (
        SELECT 1 FROM @BeginResult
        WHERE ImportBatchId = @FailureBatchId
          AND BeginResult = 'READY'
    )
        THROW 51612, 'A failed checksum could not be safely recovered.', 1;

    INSERT INTO @FailResult
    EXEC stage.usp_FailImportBatch
        @ImportBatchId = @FailureBatchId,
        @ErrorCategory = 'TEST_CLEANUP';

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF EXISTS (SELECT 1 FROM core.Office WHERE OfficeCode = 'TST-LOAD-01')
   OR EXISTS (SELECT 1 FROM core.Department WHERE DepartmentCode = 'TST-LOAD')
   OR EXISTS (SELECT 1 FROM core.Person WHERE PersonnelCode = 'TST-LOAD-001')
   OR EXISTS (SELECT 1 FROM core.Device WHERE DeviceToken IN ('DEV-FFF20001', 'DEV-FFF20002'))
   OR EXISTS (SELECT 1 FROM core.AccessPoint WHERE AccessPointCode LIKE 'TST-LOAD-%')
   OR EXISTS (SELECT 1 FROM stage.ImportBatch WHERE FileChecksum IN (@CardChecksum, @WifiChecksum, @FailureChecksum))
    THROW 51613, 'Batch-loader verification fixtures remain after rollback.', 1;

SELECT
    CAST('stage' AS varchar(10)) AS SchemaName,
    CAST('monthly batch loader' AS varchar(30)) AS ComponentName,
    CAST(4 AS int) AS ProcedureCount,
    CAST('PASS' AS varchar(10)) AS CardValidation,
    CAST('PASS' AS varchar(10)) AS WifiValidation,
    CAST('PASS' AS varchar(10)) AS DuplicateChecksum,
    CAST('PASS' AS varchar(10)) AS FailureRecovery,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
