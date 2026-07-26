/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Test: 007_verify_schema_behavior.sql
    Purpose: Prove representative staging, reference, fact, and summary rules.

    Every fixture and successful control row is created inside one transaction.
    Expected constraint errors are caught and identified by constraint name. The
    transaction is rolled back on success and on unexpected failure.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

DECLARE @Results table
(
    TestOrder int NOT NULL PRIMARY KEY,
    TestName varchar(70) NOT NULL,
    ExpectedConstraint sysname NOT NULL,
    Result varchar(10) NOT NULL
);

DECLARE @Rejected bit;
DECLARE @DepartmentId int;
DECLARE @PersonId int;
DECLARE @DeviceId int;
DECLARE @OfficeOneId int;
DECLARE @OfficeTwoId int;
DECLARE @CardAccessPointId int;
DECLARE @WifiAccessPointId int;
DECLARE @CardBatchId bigint;
DECLARE @WifiBatchId bigint;

BEGIN TRY
    BEGIN TRANSACTION;

    INSERT INTO core.Department
    (
        DepartmentCode,
        DepartmentName
    )
    VALUES
    (
        'TST-NEG',
        N'Schema Behavior Verification'
    );

    SET @DepartmentId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Person
    (
        PersonnelCode,
        DisplayName,
        SyntheticEmail,
        DepartmentId,
        ValidFrom,
        ValidTo
    )
    VALUES
    (
        'TST-NEG-001',
        N'Synthetic Negative Test Person',
        'tst-neg-001@attendance-lab.example',
        @DepartmentId,
        '2026-01-01',
        NULL
    );

    SET @PersonId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Device
    (
        DeviceToken,
        DeviceStatus
    )
    VALUES
    (
        'DEV-EEE00001',
        'ACTIVE'
    );

    SET @DeviceId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Office
    (
        OfficeCode,
        DisplayName,
        TimeZoneName,
        Capacity
    )
    VALUES
        ('TST-OFF-01', N'Synthetic Office One', N'GMT Standard Time', 100),
        ('TST-OFF-02', N'Synthetic Office Two', N'GMT Standard Time', 50);

    SELECT @OfficeOneId = OfficeId
    FROM core.Office
    WHERE OfficeCode = 'TST-OFF-01';

    SELECT @OfficeTwoId = OfficeId
    FROM core.Office
    WHERE OfficeCode = 'TST-OFF-02';

    INSERT INTO core.AccessPoint
    (
        OfficeId,
        AccessPointCode,
        AccessPointType,
        DisplayLabel
    )
    VALUES
        (@OfficeOneId, 'TST-CARD-01', 'CARD_READER', N'Synthetic Card Reader'),
        (@OfficeOneId, 'TST-WIFI-01', 'WIFI_AP', N'Synthetic Wi-Fi Access Point');

    SELECT @CardAccessPointId = AccessPointId
    FROM core.AccessPoint
    WHERE AccessPointCode = 'TST-CARD-01';

    SELECT @WifiAccessPointId = AccessPointId
    FROM core.AccessPoint
    WHERE AccessPointCode = 'TST-WIFI-01';

    INSERT INTO stage.ImportBatch
    (
        SourceType,
        SourceFileName,
        FileChecksum
    )
    VALUES
        ('CARD', N'test-card.csv', HASHBYTES('SHA2_256', N'test-card-content')),
        ('WIFI', N'test-wifi.csv', HASHBYTES('SHA2_256', N'test-wifi-content'));

    SELECT @CardBatchId = ImportBatchId
    FROM stage.ImportBatch
    WHERE SourceType = 'CARD'
      AND FileChecksum = HASHBYTES('SHA2_256', N'test-card-content');

    SELECT @WifiBatchId = ImportBatchId
    FROM stage.ImportBatch
    WHERE SourceType = 'WIFI'
      AND FileChecksum = HASHBYTES('SHA2_256', N'test-wifi-content');

    /* 1. A STARTED batch cannot have a completion timestamp. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO stage.ImportBatch
        (
            SourceType,
            SourceFileName,
            FileChecksum,
            CompletedAt,
            Status
        )
        VALUES
        (
            'CARD',
            N'invalid-timing.csv',
            HASHBYTES('SHA2_256', N'invalid-timing'),
            SYSUTCDATETIME(),
            'STARTED'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_stage_ImportBatch_Timing%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (1, 'STARTED batch with completion time', 'CK_stage_ImportBatch_Timing', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51401, 'Invalid import-batch timing was accepted.', 1;

    /* 2. Accepted and rejected rows cannot exceed received rows. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO stage.ImportBatch
        (
            SourceType,
            SourceFileName,
            FileChecksum,
            RowsReceived,
            RowsAccepted,
            RowsRejected
        )
        VALUES
        (
            'CARD',
            N'invalid-counts.csv',
            HASHBYTES('SHA2_256', N'invalid-counts'),
            1,
            1,
            1
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_stage_ImportBatch_RowCounts%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (2, 'Batch reconciliation exceeds received rows', 'CK_stage_ImportBatch_RowCounts', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51402, 'Invalid import-batch counts were accepted.', 1;

    /* 3. Card staging requires a processing timestamp after acceptance. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO stage.CardAccessEvent
        (
            ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            PersonnelCodeRaw,
            AccessPointCodeRaw,
            ProcessingStatus,
            ProcessedAt
        )
        VALUES
        (
            @CardBatchId,
            1,
            N'2026-02-02T08:00:00Z',
            N'TST-NEG-001',
            N'TST-CARD-01',
            'ACCEPTED',
            NULL
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_stage_CardAccessEvent_ProcessingTiming%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (3, 'Accepted card row without processed time', 'CK_stage_CardAccessEvent_ProcessingTiming', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51403, 'Invalid card processing state was accepted.', 1;

    /* 4. Wi-Fi staging rejects non-positive source row numbers. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO stage.WifiObservation
        (
            ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            DeviceTokenRaw,
            AccessPointCodeRaw
        )
        VALUES
        (
            @WifiBatchId,
            0,
            N'2026-02-02T08:00:00Z',
            N'DEV-EEE00001',
            N'TST-WIFI-01'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_stage_WifiObservation_SourceRowNumber%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (4, 'Wi-Fi source row number is zero', 'CK_stage_WifiObservation_SourceRowNumber', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51404, 'Invalid Wi-Fi source row number was accepted.', 1;

    /* 5. Synthetic-looking email must use the reserved lab domain. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.Person
        (
            PersonnelCode,
            DisplayName,
            SyntheticEmail,
            DepartmentId,
            ValidFrom
        )
        VALUES
        (
            'TST-NEG-002',
            N'Invalid Synthetic Email',
            'not-the-lab-domain@example.com',
            @DepartmentId,
            '2026-01-01'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_Person_SyntheticEmail%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (5, 'Person email outside reserved domain', 'CK_core_Person_SyntheticEmail', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51405, 'A person email outside the reserved domain was accepted.', 1;

    /* 6. Device identifiers must remain opaque lab tokens. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.Device
        (
            DeviceToken,
            DeviceStatus
        )
        VALUES
        (
            'NOT-A-DEVICE',
            'ACTIVE'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_Device_DeviceToken%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (6, 'Device token outside opaque format', 'CK_core_Device_DeviceToken', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51406, 'An invalid device token was accepted.', 1;

    /* Valid controls used as parents for fact and summary negative tests. */
    INSERT INTO core.AttendanceSignal
    (
        ImportBatchId,
        SourceRowNumber,
        OfficeId,
        PersonId,
        AccessPointId,
        DeviceId,
        SignalType,
        ObservedAtUtc,
        AttendanceDateLocal
    )
    VALUES
        (
            @CardBatchId, 1, @OfficeOneId, @PersonId,
            @CardAccessPointId, NULL, 'CARD',
            '2026-02-02T08:00:00.000', '2026-02-02'
        ),
        (
            @WifiBatchId, 1, @OfficeOneId, @PersonId,
            @WifiAccessPointId, @DeviceId, 'WIFI',
            '2026-02-02T08:05:00.000', '2026-02-02'
        );

    /* 7. Fact signal type must agree with its batch source type. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.AttendanceSignal
        (
            ImportBatchId, SourceRowNumber, OfficeId, PersonId,
            AccessPointId, DeviceId, SignalType, ObservedAtUtc,
            AttendanceDateLocal
        )
        VALUES
        (
            @CardBatchId, 2, @OfficeOneId, @PersonId,
            @WifiAccessPointId, @DeviceId, 'WIFI',
            '2026-02-02T08:10:00.000', '2026-02-02'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%FK_core_AttendanceSignal_BatchSourceType%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (7, 'Fact signal type disagrees with batch', 'FK_core_AttendanceSignal_BatchSourceType', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51407, 'A fact with mismatched batch type was accepted.', 1;

    /* 8. Fact office must agree with its access point. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.AttendanceSignal
        (
            ImportBatchId, SourceRowNumber, OfficeId, PersonId,
            AccessPointId, DeviceId, SignalType, ObservedAtUtc,
            AttendanceDateLocal
        )
        VALUES
        (
            @CardBatchId, 2, @OfficeTwoId, @PersonId,
            @CardAccessPointId, NULL, 'CARD',
            '2026-02-02T08:10:00.000', '2026-02-02'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%FK_core_AttendanceSignal_AccessPointOffice%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (8, 'Fact office disagrees with access point', 'FK_core_AttendanceSignal_AccessPointOffice', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51408, 'A fact with mismatched access-point office was accepted.', 1;

    /* 9. Card facts cannot carry a device identifier. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.AttendanceSignal
        (
            ImportBatchId, SourceRowNumber, OfficeId, PersonId,
            AccessPointId, DeviceId, SignalType, ObservedAtUtc,
            AttendanceDateLocal
        )
        VALUES
        (
            @CardBatchId, 2, @OfficeOneId, @PersonId,
            @CardAccessPointId, @DeviceId, 'CARD',
            '2026-02-02T08:10:00.000', '2026-02-02'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_AttendanceSignal_DeviceSemantics%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (9, 'Card fact incorrectly contains a device', 'CK_core_AttendanceSignal_DeviceSemantics', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51409, 'A card fact containing a device was accepted.', 1;

    /* 10. Wi-Fi facts require a resolved device identifier. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.AttendanceSignal
        (
            ImportBatchId, SourceRowNumber, OfficeId, PersonId,
            AccessPointId, DeviceId, SignalType, ObservedAtUtc,
            AttendanceDateLocal
        )
        VALUES
        (
            @WifiBatchId, 2, @OfficeOneId, @PersonId,
            @WifiAccessPointId, NULL, 'WIFI',
            '2026-02-02T08:10:00.000', '2026-02-02'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_AttendanceSignal_DeviceSemantics%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (10, 'Wi-Fi fact has no resolved device', 'CK_core_AttendanceSignal_DeviceSemantics', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51410, 'A Wi-Fi fact without a device was accepted.', 1;

    /* 11. One source row can produce at most one authoritative fact. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.AttendanceSignal
        (
            ImportBatchId, SourceRowNumber, OfficeId, PersonId,
            AccessPointId, DeviceId, SignalType, ObservedAtUtc,
            AttendanceDateLocal
        )
        VALUES
        (
            @CardBatchId, 1, @OfficeOneId, @PersonId,
            @CardAccessPointId, NULL, 'CARD',
            '2026-02-02T08:15:00.000', '2026-02-02'
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() NOT IN (2601, 2627)
           OR ERROR_MESSAGE() NOT LIKE '%UQ_core_AttendanceSignal_SourceLineage%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (11, 'Duplicate authoritative source lineage', 'UQ_core_AttendanceSignal_SourceLineage', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51411, 'Duplicate authoritative source lineage was accepted.', 1;

    /* 12. Detection method must agree with its signal counts. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.DailyAttendanceSummary
        (
            AttendanceDateLocal, OfficeId, PersonId, DetectionMethod,
            FirstObservedAtUtc, LastObservedAtUtc,
            CardSignalCount, WifiSignalCount
        )
        VALUES
        (
            '2026-02-02', @OfficeOneId, @PersonId, 'BOTH',
            '2026-02-02T08:00:00.000', '2026-02-02T08:05:00.000',
            1, 0
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_DailyAttendanceSummary_DetectionMethod%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (12, 'Summary method disagrees with counts', 'CK_core_DailyAttendanceSummary_DetectionMethod', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51412, 'An inconsistent summary detection method was accepted.', 1;

    /* 13. First observation cannot be later than the last observation. */
    SET @Rejected = 0;
    BEGIN TRY
        INSERT INTO core.DailyAttendanceSummary
        (
            AttendanceDateLocal, OfficeId, PersonId, DetectionMethod,
            FirstObservedAtUtc, LastObservedAtUtc,
            CardSignalCount, WifiSignalCount
        )
        VALUES
        (
            '2026-02-02', @OfficeOneId, @PersonId, 'BOTH',
            '2026-02-02T09:00:00.000', '2026-02-02T08:00:00.000',
            1, 1
        );
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 547
           OR ERROR_MESSAGE() NOT LIKE '%CK_core_DailyAttendanceSummary_ObservedRange%'
            THROW;

        SET @Rejected = 1;
        INSERT INTO @Results VALUES
            (13, 'Summary first observation follows last', 'CK_core_DailyAttendanceSummary_ObservedRange', 'PASS');
    END CATCH;

    IF @Rejected = 0
        THROW 51413, 'An invalid summary observation range was accepted.', 1;

    IF XACT_STATE() <> 1
        THROW 51414, 'The verification transaction is not committable.', 1;

    ROLLBACK TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;

IF EXISTS
(
    SELECT 1
    FROM core.Department
    WHERE DepartmentCode = 'TST-NEG'
)
OR EXISTS
(
    SELECT 1
    FROM core.Person
    WHERE PersonnelCode IN ('TST-NEG-001', 'TST-NEG-002')
)
OR EXISTS
(
    SELECT 1
    FROM core.Device
    WHERE DeviceToken = 'DEV-EEE00001'
)
OR EXISTS
(
    SELECT 1
    FROM core.Office
    WHERE OfficeCode IN ('TST-OFF-01', 'TST-OFF-02')
)
OR EXISTS
(
    SELECT 1
    FROM core.AccessPoint
    WHERE AccessPointCode IN ('TST-CARD-01', 'TST-WIFI-01')
)
OR EXISTS
(
    SELECT 1
    FROM stage.ImportBatch
    WHERE SourceFileName IN (N'test-card.csv', N'test-wifi.csv')
)
    THROW 51415, 'Schema-verification fixtures remained after rollback.', 1;

IF (SELECT COUNT(*) FROM @Results WHERE Result = 'PASS') <> 13
    THROW 51416, 'Not all schema behavior tests passed.', 1;

SELECT
    TestOrder,
    TestName,
    ExpectedConstraint,
    Result
FROM @Results
ORDER BY TestOrder;

SELECT
    COUNT(*) AS BehaviorTestCount,
    SUM(CASE WHEN Result = 'PASS' THEN 1 ELSE 0 END) AS PassedTestCount,
    CAST('PASS' AS varchar(10)) AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup
FROM @Results;
