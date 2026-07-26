/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 008_create_batch_loader.sql
    Purpose: Create the controlled monthly source-batch lifecycle.

    The client registers a batch in autocommit mode, then opens one transaction
    for all chunk appends and finalization. A session application lock protects
    the source-type/checksum identity and disappears if the client disconnects.
*/

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE stage.usp_BeginImportBatch
    @SourceType varchar(10),
    @SourceFileName nvarchar(260),
    @FileChecksum binary(32)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @ImportBatchId bigint;
    DECLARE @Status varchar(10);
    DECLARE @LockResult int;
    DECLARE @LockResource nvarchar(255) = CONCAT
    (
        N'stage.ImportBatch:',
        @SourceType,
        N':',
        CONVERT(varchar(64), @FileChecksum, 2)
    );

    IF @SourceType IS NULL OR @SourceType NOT IN ('CARD', 'WIFI')
        THROW 51500, 'SourceType must be CARD or WIFI.', 1;

    IF @SourceFileName IS NULL
       OR LEN(LTRIM(RTRIM(@SourceFileName))) = 0
       OR @SourceFileName LIKE N'%/%'
       OR @SourceFileName LIKE N'%\%'
        THROW 51501, 'SourceFileName must be a non-empty basename.', 1;

    IF @FileChecksum IS NULL
        THROW 51503, 'FileChecksum is required.', 1;

    EXEC @LockResult = sys.sp_getapplock
        @Resource = @LockResource,
        @LockMode = N'Exclusive',
        @LockOwner = N'Session',
        @LockTimeout = 15000;

    IF @LockResult < 0
        THROW 51502, 'Could not acquire the import-batch application lock.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @ImportBatchId = ImportBatchId,
            @Status = Status
        FROM stage.ImportBatch WITH (UPDLOCK, HOLDLOCK)
        WHERE SourceType = @SourceType
          AND FileChecksum = @FileChecksum;

        IF @ImportBatchId IS NOT NULL AND @Status IN ('COMPLETED', 'PARTIAL')
        BEGIN
            COMMIT TRANSACTION;

            EXEC sys.sp_releaseapplock
                @Resource = @LockResource,
                @LockOwner = N'Session';

            SELECT
                @ImportBatchId AS ImportBatchId,
                CAST('ALREADY_PROCESSED' AS varchar(20)) AS BeginResult;
            RETURN;
        END;

        IF @ImportBatchId IS NULL
        BEGIN
            INSERT INTO stage.ImportBatch
            (
                SourceType,
                SourceFileName,
                FileChecksum
            )
            VALUES
            (
                @SourceType,
                @SourceFileName,
                @FileChecksum
            );

            SET @ImportBatchId = CONVERT(bigint, SCOPE_IDENTITY());
        END;
        ELSE
        BEGIN
            DELETE FROM core.AttendanceSignal WHERE ImportBatchId = @ImportBatchId;
            DELETE FROM stage.ImportError WHERE ImportBatchId = @ImportBatchId;
            DELETE FROM stage.CardAccessEvent WHERE ImportBatchId = @ImportBatchId;
            DELETE FROM stage.WifiObservation WHERE ImportBatchId = @ImportBatchId;

            UPDATE stage.ImportBatch
            SET SourceFileName = @SourceFileName,
                StartedAt = SYSUTCDATETIME(),
                CompletedAt = NULL,
                Status = 'STARTED',
                RowsReceived = 0,
                RowsAccepted = 0,
                RowsRejected = 0,
                ErrorMessage = NULL
            WHERE ImportBatchId = @ImportBatchId;
        END;

        COMMIT TRANSACTION;

        SELECT
            @ImportBatchId AS ImportBatchId,
            CAST('READY' AS varchar(20)) AS BeginResult;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;

        EXEC sys.sp_releaseapplock
            @Resource = @LockResource,
            @LockOwner = N'Session';

        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE stage.usp_AppendImportChunk
    @ImportBatchId bigint,
    @RowsJson nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @SourceType varchar(10);
    DECLARE @FileChecksum binary(32);
    DECLARE @RowsInChunk int;
    DECLARE @LockResource nvarchar(255);

    SELECT
        @SourceType = SourceType,
        @FileChecksum = FileChecksum
    FROM stage.ImportBatch WITH (UPDLOCK, HOLDLOCK)
    WHERE ImportBatchId = @ImportBatchId
      AND Status = 'STARTED';

    IF @SourceType IS NULL
        THROW 51510, 'Import batch does not exist or is not STARTED.', 1;

    SET @LockResource = CONCAT
    (
        N'stage.ImportBatch:',
        @SourceType,
        N':',
        CONVERT(varchar(64), @FileChecksum, 2)
    );

    IF COALESCE(APPLOCK_MODE(N'public', @LockResource, N'Session'), N'NoLock') <> N'Exclusive'
        THROW 51511, 'The current session does not own the import-batch lock.', 1;

    IF @RowsJson IS NULL
       OR ISJSON(@RowsJson) <> 1
       OR LEFT(LTRIM(@RowsJson), 1) <> N'['
        THROW 51512, 'RowsJson must be a valid JSON array.', 1;

    SELECT @RowsInChunk = COUNT(*) FROM OPENJSON(@RowsJson);

    IF @RowsInChunk < 1 OR @RowsInChunk > 1000
        THROW 51513, 'Each chunk must contain between 1 and 1000 rows.', 1;

    IF @SourceType = 'CARD'
    BEGIN
        INSERT INTO stage.CardAccessEvent
        (
            ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            PersonnelCodeRaw,
            AccessPointCodeRaw
        )
        SELECT
            @ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            PersonnelCodeRaw,
            AccessPointCodeRaw
        FROM OPENJSON(@RowsJson)
        WITH
        (
            SourceRowNumber int '$.source_row_number',
            ObservedAtRaw nvarchar(50) '$.observed_at_raw',
            PersonnelCodeRaw nvarchar(50) '$.personnel_code_raw',
            AccessPointCodeRaw nvarchar(50) '$.access_point_code_raw'
        );
    END;
    ELSE
    BEGIN
        INSERT INTO stage.WifiObservation
        (
            ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            DeviceTokenRaw,
            AccessPointCodeRaw,
            SignalStrengthRaw
        )
        SELECT
            @ImportBatchId,
            SourceRowNumber,
            ObservedAtRaw,
            DeviceTokenRaw,
            AccessPointCodeRaw,
            SignalStrengthRaw
        FROM OPENJSON(@RowsJson)
        WITH
        (
            SourceRowNumber int '$.source_row_number',
            ObservedAtRaw nvarchar(50) '$.observed_at_raw',
            DeviceTokenRaw nvarchar(100) '$.device_token_raw',
            AccessPointCodeRaw nvarchar(50) '$.access_point_code_raw',
            SignalStrengthRaw nvarchar(20) '$.signal_strength_raw'
        );
    END;

    UPDATE stage.ImportBatch
    SET RowsReceived = RowsReceived + @RowsInChunk
    WHERE ImportBatchId = @ImportBatchId;

    SELECT
        @ImportBatchId AS ImportBatchId,
        @RowsInChunk AS RowsAppended;
END;
GO

CREATE OR ALTER PROCEDURE stage.usp_FinalizeImportBatch
    @ImportBatchId bigint,
    @ExpectedRows int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @SourceType varchar(10);
    DECLARE @FileChecksum binary(32);
    DECLARE @RowsReceived int;
    DECLARE @RowsAccepted int;
    DECLARE @RowsRejected int;
    DECLARE @LockResource nvarchar(255);
    DECLARE @ProcessedAt datetime2(3) = SYSUTCDATETIME();

    SELECT
        @SourceType = SourceType,
        @FileChecksum = FileChecksum,
        @RowsReceived = RowsReceived
    FROM stage.ImportBatch WITH (UPDLOCK, HOLDLOCK)
    WHERE ImportBatchId = @ImportBatchId
      AND Status = 'STARTED';

    IF @SourceType IS NULL
        THROW 51520, 'Import batch does not exist or is not STARTED.', 1;

    SET @LockResource = CONCAT
    (
        N'stage.ImportBatch:',
        @SourceType,
        N':',
        CONVERT(varchar(64), @FileChecksum, 2)
    );

    IF COALESCE(APPLOCK_MODE(N'public', @LockResource, N'Session'), N'NoLock') <> N'Exclusive'
        THROW 51521, 'The current session does not own the import-batch lock.', 1;

    IF @ExpectedRows IS NULL OR @ExpectedRows < 1 OR @RowsReceived <> @ExpectedRows
        THROW 51522, 'Received row count does not match the expected file count.', 1;

    IF @SourceType = 'CARD'
       AND (SELECT COUNT(*) FROM stage.CardAccessEvent WHERE ImportBatchId = @ImportBatchId) <> @ExpectedRows
        THROW 51523, 'Card staging count does not reconcile with the batch.', 1;

    IF @SourceType = 'WIFI'
       AND (SELECT COUNT(*) FROM stage.WifiObservation WHERE ImportBatchId = @ImportBatchId) <> @ExpectedRows
        THROW 51524, 'Wi-Fi staging count does not reconcile with the batch.', 1;

    IF @SourceType = 'CARD'
    BEGIN
        CREATE TABLE #CardResolution
        (
            SourceRowNumber int NOT NULL PRIMARY KEY,
            ObservedAtUtc datetime2(3) NULL,
            AttendanceDateLocal date NULL,
            OfficeId int NULL,
            PersonId int NULL,
            AccessPointId int NULL,
            ValidationCode varchar(50) NULL,
            ErrorDescription nvarchar(500) NULL
        );

        INSERT INTO #CardResolution
        SELECT
            source.SourceRowNumber,
            parsed.ObservedAtUtc,
            CASE
                WHEN parsed.ObservedAtUtc IS NOT NULL AND access_point.AccessPointId IS NOT NULL
                    THEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    )
                ELSE NULL
            END,
            office.OfficeId,
            person.PersonId,
            access_point.AccessPointId,
            validation.ValidationCode,
            validation.ErrorDescription
        FROM stage.CardAccessEvent AS source
        OUTER APPLY
        (
            SELECT CONVERT
            (
                datetime2(3),
                SWITCHOFFSET
                (
                    TRY_CONVERT(datetimeoffset(3), source.ObservedAtRaw, 127),
                    '+00:00'
                )
            ) AS ObservedAtUtc
        ) AS parsed
        LEFT JOIN core.Person AS person
            ON person.PersonnelCode = LTRIM(RTRIM(source.PersonnelCodeRaw))
        LEFT JOIN core.AccessPoint AS access_point
            ON access_point.AccessPointCode = LTRIM(RTRIM(source.AccessPointCodeRaw))
           AND access_point.AccessPointType = 'CARD_READER'
           AND access_point.IsActive = 1
        LEFT JOIN core.Office AS office
            ON office.OfficeId = access_point.OfficeId
        CROSS APPLY
        (
            SELECT
                CASE
                    WHEN parsed.ObservedAtUtc IS NULL THEN 'INVALID_TIMESTAMP'
                    WHEN LEN(LTRIM(RTRIM(source.PersonnelCodeRaw))) = 0 THEN 'BLANK_PERSONNEL_CODE'
                    WHEN person.PersonId IS NULL THEN 'UNKNOWN_PERSONNEL'
                    WHEN access_point.AccessPointId IS NULL THEN 'UNKNOWN_ACCESS_POINT'
                    WHEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    ) < person.ValidFrom
                      OR
                      (
                          person.ValidTo IS NOT NULL
                          AND CONVERT
                          (
                              date,
                              parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                                  AT TIME ZONE office.TimeZoneName
                          ) >= person.ValidTo
                      ) THEN 'PERSON_OUTSIDE_VALIDITY'
                    ELSE NULL
                END AS ValidationCode,
                CASE
                    WHEN parsed.ObservedAtUtc IS NULL THEN N'Observation timestamp is not valid ISO 8601 UTC.'
                    WHEN LEN(LTRIM(RTRIM(source.PersonnelCodeRaw))) = 0 THEN N'Personnel code is blank.'
                    WHEN person.PersonId IS NULL THEN N'Personnel code does not resolve.'
                    WHEN access_point.AccessPointId IS NULL THEN N'Card access point does not resolve to an active card reader.'
                    WHEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    ) < person.ValidFrom
                      OR
                      (
                          person.ValidTo IS NOT NULL
                          AND CONVERT
                          (
                              date,
                              parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                                  AT TIME ZONE office.TimeZoneName
                          ) >= person.ValidTo
                      ) THEN N'Person is outside the valid attendance period.'
                    ELSE NULL
                END AS ErrorDescription
        ) AS validation
        WHERE source.ImportBatchId = @ImportBatchId;

        INSERT INTO stage.ImportError
        (
            ImportBatchId, SourceTable, SourceRowNumber, ValidationCode, ErrorDescription
        )
        SELECT
            @ImportBatchId,
            'CARD_ACCESS_EVENT',
            SourceRowNumber,
            ValidationCode,
            ErrorDescription
        FROM #CardResolution
        WHERE ValidationCode IS NOT NULL;

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
        SELECT
            @ImportBatchId,
            SourceRowNumber,
            OfficeId,
            PersonId,
            AccessPointId,
            NULL,
            'CARD',
            ObservedAtUtc,
            AttendanceDateLocal
        FROM #CardResolution
        WHERE ValidationCode IS NULL;

        UPDATE source
        SET ProcessingStatus = CASE WHEN result.ValidationCode IS NULL THEN 'ACCEPTED' ELSE 'REJECTED' END,
            ProcessedAt = @ProcessedAt
        FROM stage.CardAccessEvent AS source
        INNER JOIN #CardResolution AS result
            ON result.SourceRowNumber = source.SourceRowNumber
        WHERE source.ImportBatchId = @ImportBatchId;
    END;
    ELSE
    BEGIN
        CREATE TABLE #WifiResolution
        (
            SourceRowNumber int NOT NULL PRIMARY KEY,
            ObservedAtUtc datetime2(3) NULL,
            AttendanceDateLocal date NULL,
            OfficeId int NULL,
            PersonId int NULL,
            AccessPointId int NULL,
            DeviceId int NULL,
            ValidationCode varchar(50) NULL,
            ErrorDescription nvarchar(500) NULL
        );

        INSERT INTO #WifiResolution
        SELECT
            source.SourceRowNumber,
            parsed.ObservedAtUtc,
            CASE
                WHEN parsed.ObservedAtUtc IS NOT NULL AND access_point.AccessPointId IS NOT NULL
                    THEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    )
                ELSE NULL
            END,
            office.OfficeId,
            assignment.PersonId,
            access_point.AccessPointId,
            device.DeviceId,
            validation.ValidationCode,
            validation.ErrorDescription
        FROM stage.WifiObservation AS source
        OUTER APPLY
        (
            SELECT
                CONVERT
                (
                    datetime2(3),
                    SWITCHOFFSET
                    (
                        TRY_CONVERT(datetimeoffset(3), source.ObservedAtRaw, 127),
                        '+00:00'
                    )
                ) AS ObservedAtUtc,
                TRY_CONVERT(int, source.SignalStrengthRaw) AS SignalStrength
        ) AS parsed
        LEFT JOIN core.Device AS device
            ON device.DeviceToken = LTRIM(RTRIM(source.DeviceTokenRaw))
        LEFT JOIN core.AccessPoint AS access_point
            ON access_point.AccessPointCode = LTRIM(RTRIM(source.AccessPointCodeRaw))
           AND access_point.AccessPointType = 'WIFI_AP'
           AND access_point.IsActive = 1
        LEFT JOIN core.Office AS office
            ON office.OfficeId = access_point.OfficeId
        OUTER APPLY
        (
            SELECT TOP (1)
                link.PersonId,
                person.ValidFrom AS PersonValidFrom,
                person.ValidTo AS PersonValidTo
            FROM core.PersonDeviceAssignment AS link
            INNER JOIN core.Person AS person ON person.PersonId = link.PersonId
            WHERE link.DeviceId = device.DeviceId
              AND parsed.ObservedAtUtc >= link.ValidFrom
              AND (link.ValidTo IS NULL OR parsed.ObservedAtUtc < link.ValidTo)
            ORDER BY link.ValidFrom DESC
        ) AS assignment
        CROSS APPLY
        (
            SELECT
                CASE
                    WHEN parsed.ObservedAtUtc IS NULL THEN 'INVALID_TIMESTAMP'
                    WHEN device.DeviceId IS NULL THEN 'UNKNOWN_DEVICE'
                    WHEN access_point.AccessPointId IS NULL THEN 'UNKNOWN_ACCESS_POINT'
                    WHEN parsed.SignalStrength IS NULL OR parsed.SignalStrength < -100 OR parsed.SignalStrength > 0
                        THEN 'INVALID_SIGNAL_STRENGTH'
                    WHEN assignment.PersonId IS NULL THEN 'DEVICE_NOT_ASSIGNED'
                    WHEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    ) < assignment.PersonValidFrom
                      OR
                      (
                          assignment.PersonValidTo IS NOT NULL
                          AND CONVERT
                          (
                              date,
                              parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                                  AT TIME ZONE office.TimeZoneName
                          ) >= assignment.PersonValidTo
                      ) THEN 'PERSON_OUTSIDE_VALIDITY'
                    ELSE NULL
                END AS ValidationCode,
                CASE
                    WHEN parsed.ObservedAtUtc IS NULL THEN N'Observation timestamp is not valid ISO 8601 UTC.'
                    WHEN device.DeviceId IS NULL THEN N'Device token does not resolve.'
                    WHEN access_point.AccessPointId IS NULL THEN N'Wi-Fi access point does not resolve to an active Wi-Fi access point.'
                    WHEN parsed.SignalStrength IS NULL OR parsed.SignalStrength < -100 OR parsed.SignalStrength > 0
                        THEN N'Signal strength is not a valid integer RSSI value.'
                    WHEN assignment.PersonId IS NULL THEN N'Device has no assignment at the observation time.'
                    WHEN CONVERT
                    (
                        date,
                        parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                            AT TIME ZONE office.TimeZoneName
                    ) < assignment.PersonValidFrom
                      OR
                      (
                          assignment.PersonValidTo IS NOT NULL
                          AND CONVERT
                          (
                              date,
                              parsed.ObservedAtUtc AT TIME ZONE 'UTC'
                                  AT TIME ZONE office.TimeZoneName
                          ) >= assignment.PersonValidTo
                      ) THEN N'Person is outside the valid attendance period.'
                    ELSE NULL
                END AS ErrorDescription
        ) AS validation
        WHERE source.ImportBatchId = @ImportBatchId;

        INSERT INTO stage.ImportError
        (
            ImportBatchId, SourceTable, SourceRowNumber, ValidationCode, ErrorDescription
        )
        SELECT
            @ImportBatchId,
            'WIFI_OBSERVATION',
            SourceRowNumber,
            ValidationCode,
            ErrorDescription
        FROM #WifiResolution
        WHERE ValidationCode IS NOT NULL;

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
        SELECT
            @ImportBatchId,
            SourceRowNumber,
            OfficeId,
            PersonId,
            AccessPointId,
            DeviceId,
            'WIFI',
            ObservedAtUtc,
            AttendanceDateLocal
        FROM #WifiResolution
        WHERE ValidationCode IS NULL;

        UPDATE source
        SET ProcessingStatus = CASE WHEN result.ValidationCode IS NULL THEN 'ACCEPTED' ELSE 'REJECTED' END,
            ProcessedAt = @ProcessedAt
        FROM stage.WifiObservation AS source
        INNER JOIN #WifiResolution AS result
            ON result.SourceRowNumber = source.SourceRowNumber
        WHERE source.ImportBatchId = @ImportBatchId;
    END;

    SELECT @RowsAccepted = COUNT(*)
    FROM core.AttendanceSignal
    WHERE ImportBatchId = @ImportBatchId;

    SELECT @RowsRejected = COUNT(*)
    FROM stage.ImportError
    WHERE ImportBatchId = @ImportBatchId;

    IF @RowsAccepted + @RowsRejected <> @RowsReceived
        THROW 51525, 'Accepted and rejected rows do not reconcile with received rows.', 1;

    IF @SourceType = 'CARD'
       AND EXISTS
       (
           SELECT 1 FROM stage.CardAccessEvent
           WHERE ImportBatchId = @ImportBatchId AND ProcessingStatus = 'PENDING'
       )
        THROW 51526, 'Card batch still contains pending rows.', 1;

    IF @SourceType = 'WIFI'
       AND EXISTS
       (
           SELECT 1 FROM stage.WifiObservation
           WHERE ImportBatchId = @ImportBatchId AND ProcessingStatus = 'PENDING'
       )
        THROW 51527, 'Wi-Fi batch still contains pending rows.', 1;

    UPDATE stage.ImportBatch
    SET CompletedAt = @ProcessedAt,
        Status = CASE WHEN @RowsRejected = 0 THEN 'COMPLETED' ELSE 'PARTIAL' END,
        RowsAccepted = @RowsAccepted,
        RowsRejected = @RowsRejected,
        ErrorMessage = NULL
    WHERE ImportBatchId = @ImportBatchId;

    EXEC sys.sp_releaseapplock
        @Resource = @LockResource,
        @LockOwner = N'Session';

    SELECT
        @ImportBatchId AS ImportBatchId,
        @RowsReceived AS RowsReceived,
        @RowsAccepted AS RowsAccepted,
        @RowsRejected AS RowsRejected,
        CAST(CASE WHEN @RowsRejected = 0 THEN 'COMPLETED' ELSE 'PARTIAL' END AS varchar(10)) AS FinalStatus;
END;
GO

CREATE OR ALTER PROCEDURE stage.usp_FailImportBatch
    @ImportBatchId bigint,
    @ErrorCategory varchar(50)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @SourceType varchar(10);
    DECLARE @FileChecksum binary(32);
    DECLARE @LockResource nvarchar(255);

    IF @ErrorCategory IS NULL
       OR LEN(LTRIM(RTRIM(@ErrorCategory))) = 0
       OR @ErrorCategory COLLATE Latin1_General_100_BIN2 LIKE '%[^A-Z0-9_]%'
        THROW 51530, 'ErrorCategory must be a safe uppercase category.', 1;

    SELECT
        @SourceType = SourceType,
        @FileChecksum = FileChecksum
    FROM stage.ImportBatch WITH (UPDLOCK, HOLDLOCK)
    WHERE ImportBatchId = @ImportBatchId
      AND Status = 'STARTED';

    IF @SourceType IS NULL
        THROW 51531, 'Import batch does not exist or is not STARTED.', 1;

    SET @LockResource = CONCAT
    (
        N'stage.ImportBatch:',
        @SourceType,
        N':',
        CONVERT(varchar(64), @FileChecksum, 2)
    );

    IF COALESCE(APPLOCK_MODE(N'public', @LockResource, N'Session'), N'NoLock') <> N'Exclusive'
        THROW 51532, 'The current session does not own the import-batch lock.', 1;

    /*
        Failure is itself a reconciled terminal state. Remove any partial work
        before zeroing the counters so the batch record never disagrees with
        retained staging, error, or fact rows.
    */
    DELETE FROM core.AttendanceSignal WHERE ImportBatchId = @ImportBatchId;
    DELETE FROM stage.ImportError WHERE ImportBatchId = @ImportBatchId;
    DELETE FROM stage.CardAccessEvent WHERE ImportBatchId = @ImportBatchId;
    DELETE FROM stage.WifiObservation WHERE ImportBatchId = @ImportBatchId;

    UPDATE stage.ImportBatch
    SET CompletedAt = SYSUTCDATETIME(),
        Status = 'FAILED',
        RowsReceived = 0,
        RowsAccepted = 0,
        RowsRejected = 0,
        ErrorMessage = CONCAT(N'LOAD_', @ErrorCategory)
    WHERE ImportBatchId = @ImportBatchId;

    EXEC sys.sp_releaseapplock
        @Resource = @LockResource,
        @LockOwner = N'Session';

    SELECT
        @ImportBatchId AS ImportBatchId,
        CAST('FAILED' AS varchar(10)) AS FinalStatus;
END;
GO
