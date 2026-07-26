/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 002_create_stage_tables.sql
    Purpose: Create operational import, raw landing, and validation-error tables.

    Raw values remain text where parsing is part of validation. This allows
    controlled malformed synthetic rows to be landed, audited, and rejected.
    The script is rerunnable: existing tables are retained.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'stage.ImportBatch', N'U') IS NULL
    BEGIN
        CREATE TABLE stage.ImportBatch
        (
            ImportBatchId bigint IDENTITY(1, 1) NOT NULL,
            SourceType varchar(10) NOT NULL,
            SourceFileName nvarchar(260) NOT NULL,
            FileChecksum binary(32) NOT NULL,
            StartedAt datetime2(3) NOT NULL
                CONSTRAINT DF_stage_ImportBatch_StartedAt DEFAULT SYSUTCDATETIME(),
            CompletedAt datetime2(3) NULL,
            Status varchar(10) NOT NULL
                CONSTRAINT DF_stage_ImportBatch_Status DEFAULT ('STARTED'),
            RowsReceived int NOT NULL
                CONSTRAINT DF_stage_ImportBatch_RowsReceived DEFAULT (0),
            RowsAccepted int NOT NULL
                CONSTRAINT DF_stage_ImportBatch_RowsAccepted DEFAULT (0),
            RowsRejected int NOT NULL
                CONSTRAINT DF_stage_ImportBatch_RowsRejected DEFAULT (0),
            ErrorMessage nvarchar(1000) NULL,

            CONSTRAINT PK_stage_ImportBatch
                PRIMARY KEY CLUSTERED (ImportBatchId),
            CONSTRAINT UQ_stage_ImportBatch_SourceType_FileChecksum
                UNIQUE (SourceType, FileChecksum),
            CONSTRAINT CK_stage_ImportBatch_SourceType
                CHECK (SourceType IN ('CARD', 'WIFI')),
            CONSTRAINT CK_stage_ImportBatch_SourceFileName
                CHECK (LEN(LTRIM(RTRIM(SourceFileName))) > 0),
            CONSTRAINT CK_stage_ImportBatch_Status
                CHECK (Status IN ('STARTED', 'COMPLETED', 'PARTIAL', 'FAILED')),
            CONSTRAINT CK_stage_ImportBatch_Timing
                CHECK
                (
                    (Status = 'STARTED' AND CompletedAt IS NULL)
                    OR
                    (Status <> 'STARTED' AND CompletedAt IS NOT NULL)
                ),
            CONSTRAINT CK_stage_ImportBatch_CompletedAt
                CHECK (CompletedAt IS NULL OR CompletedAt >= StartedAt),
            CONSTRAINT CK_stage_ImportBatch_RowCounts
                CHECK
                (
                    RowsReceived >= 0
                    AND RowsAccepted >= 0
                    AND RowsRejected >= 0
                    AND CAST(RowsAccepted AS bigint) + CAST(RowsRejected AS bigint)
                        <= CAST(RowsReceived AS bigint)
                )
        );
    END;

    IF OBJECT_ID(N'stage.CardAccessEvent', N'U') IS NULL
    BEGIN
        CREATE TABLE stage.CardAccessEvent
        (
            ImportBatchId bigint NOT NULL,
            SourceRowNumber int NOT NULL,
            ObservedAtRaw nvarchar(50) NOT NULL,
            PersonnelCodeRaw nvarchar(50) NOT NULL,
            AccessPointCodeRaw nvarchar(50) NOT NULL,
            ProcessingStatus varchar(10) NOT NULL
                CONSTRAINT DF_stage_CardAccessEvent_ProcessingStatus DEFAULT ('PENDING'),
            LoadedAt datetime2(3) NOT NULL
                CONSTRAINT DF_stage_CardAccessEvent_LoadedAt DEFAULT SYSUTCDATETIME(),
            ProcessedAt datetime2(3) NULL,

            CONSTRAINT PK_stage_CardAccessEvent
                PRIMARY KEY CLUSTERED (ImportBatchId, SourceRowNumber),
            CONSTRAINT FK_stage_CardAccessEvent_ImportBatch
                FOREIGN KEY (ImportBatchId)
                REFERENCES stage.ImportBatch (ImportBatchId),
            CONSTRAINT CK_stage_CardAccessEvent_SourceRowNumber
                CHECK (SourceRowNumber > 0),
            CONSTRAINT CK_stage_CardAccessEvent_ProcessingStatus
                CHECK (ProcessingStatus IN ('PENDING', 'ACCEPTED', 'REJECTED')),
            CONSTRAINT CK_stage_CardAccessEvent_ProcessingTiming
                CHECK
                (
                    (ProcessingStatus = 'PENDING' AND ProcessedAt IS NULL)
                    OR
                    (ProcessingStatus <> 'PENDING' AND ProcessedAt IS NOT NULL)
                ),
            CONSTRAINT CK_stage_CardAccessEvent_ProcessedAt
                CHECK (ProcessedAt IS NULL OR ProcessedAt >= LoadedAt)
        );
    END;

    IF OBJECT_ID(N'stage.WifiObservation', N'U') IS NULL
    BEGIN
        CREATE TABLE stage.WifiObservation
        (
            ImportBatchId bigint NOT NULL,
            SourceRowNumber int NOT NULL,
            ObservedAtRaw nvarchar(50) NOT NULL,
            DeviceTokenRaw nvarchar(100) NOT NULL,
            AccessPointCodeRaw nvarchar(50) NOT NULL,
            SignalStrengthRaw nvarchar(20) NULL,
            ProcessingStatus varchar(10) NOT NULL
                CONSTRAINT DF_stage_WifiObservation_ProcessingStatus DEFAULT ('PENDING'),
            LoadedAt datetime2(3) NOT NULL
                CONSTRAINT DF_stage_WifiObservation_LoadedAt DEFAULT SYSUTCDATETIME(),
            ProcessedAt datetime2(3) NULL,

            CONSTRAINT PK_stage_WifiObservation
                PRIMARY KEY CLUSTERED (ImportBatchId, SourceRowNumber),
            CONSTRAINT FK_stage_WifiObservation_ImportBatch
                FOREIGN KEY (ImportBatchId)
                REFERENCES stage.ImportBatch (ImportBatchId),
            CONSTRAINT CK_stage_WifiObservation_SourceRowNumber
                CHECK (SourceRowNumber > 0),
            CONSTRAINT CK_stage_WifiObservation_ProcessingStatus
                CHECK (ProcessingStatus IN ('PENDING', 'ACCEPTED', 'REJECTED')),
            CONSTRAINT CK_stage_WifiObservation_ProcessingTiming
                CHECK
                (
                    (ProcessingStatus = 'PENDING' AND ProcessedAt IS NULL)
                    OR
                    (ProcessingStatus <> 'PENDING' AND ProcessedAt IS NOT NULL)
                ),
            CONSTRAINT CK_stage_WifiObservation_ProcessedAt
                CHECK (ProcessedAt IS NULL OR ProcessedAt >= LoadedAt)
        );
    END;

    IF OBJECT_ID(N'stage.ImportError', N'U') IS NULL
    BEGIN
        CREATE TABLE stage.ImportError
        (
            ImportErrorId bigint IDENTITY(1, 1) NOT NULL,
            ImportBatchId bigint NOT NULL,
            SourceTable varchar(30) NOT NULL,
            SourceRowNumber int NOT NULL,
            ValidationCode varchar(50) NOT NULL,
            ErrorDescription nvarchar(500) NOT NULL,
            RecordedAt datetime2(3) NOT NULL
                CONSTRAINT DF_stage_ImportError_RecordedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_stage_ImportError
                PRIMARY KEY CLUSTERED (ImportErrorId),
            CONSTRAINT UQ_stage_ImportError_RowRule
                UNIQUE (ImportBatchId, SourceTable, SourceRowNumber, ValidationCode),
            CONSTRAINT FK_stage_ImportError_ImportBatch
                FOREIGN KEY (ImportBatchId)
                REFERENCES stage.ImportBatch (ImportBatchId),
            CONSTRAINT CK_stage_ImportError_SourceTable
                CHECK (SourceTable IN ('CARD_ACCESS_EVENT', 'WIFI_OBSERVATION')),
            CONSTRAINT CK_stage_ImportError_SourceRowNumber
                CHECK (SourceRowNumber > 0),
            CONSTRAINT CK_stage_ImportError_ValidationCode
                CHECK (LEN(LTRIM(RTRIM(ValidationCode))) > 0),
            CONSTRAINT CK_stage_ImportError_ErrorDescription
                CHECK (LEN(LTRIM(RTRIM(ErrorDescription))) > 0)
        );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
