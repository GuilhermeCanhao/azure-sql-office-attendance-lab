/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 004_create_core_fact_tables.sql
    Purpose: Create the authoritative attendance fact and reproducible daily summary.

    AttendanceSignal is authoritative. DailyAttendanceSummary is a disposable,
    controlled projection that will be rebuilt by a later stored procedure.
    The script is rerunnable: existing tables and indexes are retained.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    /*
        These alternate-key indexes enable composite foreign keys that prevent
        a fact from disagreeing with its batch type or access-point office.
    */
    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'stage.ImportBatch')
          AND name = N'UX_stage_ImportBatch_ImportBatchId_SourceType'
    )
    BEGIN
        CREATE UNIQUE INDEX UX_stage_ImportBatch_ImportBatchId_SourceType
            ON stage.ImportBatch (ImportBatchId, SourceType);
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'core.AccessPoint')
          AND name = N'UX_core_AccessPoint_AccessPointId_OfficeId'
    )
    BEGIN
        CREATE UNIQUE INDEX UX_core_AccessPoint_AccessPointId_OfficeId
            ON core.AccessPoint (AccessPointId, OfficeId);
    END;

    IF OBJECT_ID(N'core.AttendanceSignal', N'U') IS NULL
    BEGIN
        CREATE TABLE core.AttendanceSignal
        (
            AttendanceSignalId bigint IDENTITY(1, 1) NOT NULL,
            ImportBatchId bigint NOT NULL,
            SourceRowNumber int NOT NULL,
            OfficeId int NOT NULL,
            PersonId int NOT NULL,
            AccessPointId int NOT NULL,
            DeviceId int NULL,
            SignalType varchar(10) NOT NULL,
            ObservedAtUtc datetime2(3) NOT NULL,
            AttendanceDateLocal date NOT NULL,
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_AttendanceSignal_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_AttendanceSignal
                PRIMARY KEY CLUSTERED (AttendanceSignalId),
            CONSTRAINT UQ_core_AttendanceSignal_SourceLineage
                UNIQUE (ImportBatchId, SourceRowNumber),
            CONSTRAINT FK_core_AttendanceSignal_BatchSourceType
                FOREIGN KEY (ImportBatchId, SignalType)
                REFERENCES stage.ImportBatch (ImportBatchId, SourceType),
            CONSTRAINT FK_core_AttendanceSignal_AccessPointOffice
                FOREIGN KEY (AccessPointId, OfficeId)
                REFERENCES core.AccessPoint (AccessPointId, OfficeId),
            CONSTRAINT FK_core_AttendanceSignal_Person
                FOREIGN KEY (PersonId)
                REFERENCES core.Person (PersonId),
            CONSTRAINT FK_core_AttendanceSignal_Device
                FOREIGN KEY (DeviceId)
                REFERENCES core.Device (DeviceId),
            CONSTRAINT CK_core_AttendanceSignal_SourceRowNumber
                CHECK (SourceRowNumber > 0),
            CONSTRAINT CK_core_AttendanceSignal_SignalType
                CHECK (SignalType IN ('CARD', 'WIFI')),
            CONSTRAINT CK_core_AttendanceSignal_DeviceSemantics
                CHECK
                (
                    (SignalType = 'CARD' AND DeviceId IS NULL)
                    OR
                    (SignalType = 'WIFI' AND DeviceId IS NOT NULL)
                )
        );
    END;

    IF OBJECT_ID(N'core.DailyAttendanceSummary', N'U') IS NULL
    BEGIN
        CREATE TABLE core.DailyAttendanceSummary
        (
            AttendanceDateLocal date NOT NULL,
            OfficeId int NOT NULL,
            PersonId int NOT NULL,
            DetectionMethod varchar(10) NOT NULL,
            FirstObservedAtUtc datetime2(3) NOT NULL,
            LastObservedAtUtc datetime2(3) NOT NULL,
            CardSignalCount int NOT NULL,
            WifiSignalCount int NOT NULL,
            RefreshedAtUtc datetime2(3) NOT NULL
                CONSTRAINT DF_core_DailyAttendanceSummary_RefreshedAtUtc
                    DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_DailyAttendanceSummary
                PRIMARY KEY CLUSTERED
                (
                    AttendanceDateLocal,
                    OfficeId,
                    PersonId
                ),
            CONSTRAINT FK_core_DailyAttendanceSummary_Office
                FOREIGN KEY (OfficeId)
                REFERENCES core.Office (OfficeId),
            CONSTRAINT FK_core_DailyAttendanceSummary_Person
                FOREIGN KEY (PersonId)
                REFERENCES core.Person (PersonId),
            CONSTRAINT CK_core_DailyAttendanceSummary_Counts
                CHECK
                (
                    CardSignalCount >= 0
                    AND WifiSignalCount >= 0
                    AND
                    (
                        CAST(CardSignalCount AS bigint)
                        + CAST(WifiSignalCount AS bigint)
                    ) > 0
                ),
            CONSTRAINT CK_core_DailyAttendanceSummary_ObservedRange
                CHECK (FirstObservedAtUtc <= LastObservedAtUtc),
            CONSTRAINT CK_core_DailyAttendanceSummary_DetectionMethod
                CHECK
                (
                    (
                        DetectionMethod = 'CARD'
                        AND CardSignalCount > 0
                        AND WifiSignalCount = 0
                    )
                    OR
                    (
                        DetectionMethod = 'WIFI'
                        AND CardSignalCount = 0
                        AND WifiSignalCount > 0
                    )
                    OR
                    (
                        DetectionMethod = 'BOTH'
                        AND CardSignalCount > 0
                        AND WifiSignalCount > 0
                    )
                )
        );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
