/*
    Verification for 010_create_batch_result_reader.sql.

    The fixture is synthetic and remains inside one transaction. The test
    proves the exact result contract, checksum scoping, input rejection,
    transaction rollback, and fixture cleanup.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF OBJECT_ID(N'stage.usp_GetImportBatchResult', N'P') IS NULL
    THROW 51810, 'The controlled batch-result procedure is missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.parameters
    WHERE object_id = OBJECT_ID(N'stage.usp_GetImportBatchResult')
) <> 2
    THROW 51811, 'The controlled batch-result procedure has unexpected parameters.', 1;

DECLARE @Checksum binary(32) = HASHBYTES('SHA2_256', 'TST-BATCH-RESULT-20260717');
DECLARE @WrongChecksum binary(32) = HASHBYTES('SHA2_256', 'TST-BATCH-RESULT-WRONG-20260717');
DECLARE @ImportBatchId bigint;
DECLARE @ExpectedRejections int = 0;
DECLARE @TransactionRollback varchar(10) = 'FAIL';

DECLARE @Result TABLE
(
    SourceType varchar(10),
    SourceFileName nvarchar(260),
    FileChecksumHex varchar(64),
    Status varchar(10),
    RowsReceived int,
    RowsAccepted int,
    RowsRejected int
);

BEGIN TRANSACTION;

BEGIN TRY
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
        N'tst-batch-result.csv',
        @Checksum,
        SYSUTCDATETIME(),
        'PARTIAL',
        3,
        2,
        1
    );

    SET @ImportBatchId = CONVERT(bigint, SCOPE_IDENTITY());

    INSERT INTO @Result
    EXEC stage.usp_GetImportBatchResult
        @ImportBatchId = @ImportBatchId,
        @FileChecksum = @Checksum;

    IF NOT EXISTS
    (
        SELECT 1
        FROM @Result
        WHERE SourceType = 'CARD'
          AND SourceFileName = N'tst-batch-result.csv'
          AND FileChecksumHex = CONVERT(varchar(64), @Checksum, 2)
          AND Status = 'PARTIAL'
          AND RowsReceived = 3
          AND RowsAccepted = 2
          AND RowsRejected = 1
    )
        THROW 51812, 'The controlled batch result did not match its fixture.', 1;

    BEGIN TRY
        EXEC stage.usp_GetImportBatchResult
            @ImportBatchId = @ImportBatchId,
            @FileChecksum = @WrongChecksum;
        THROW 51813, 'A mismatched checksum unexpectedly returned a batch result.', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() = 51802
            SET @ExpectedRejections += 1;
        ELSE
            THROW;
    END CATCH;

    BEGIN TRY
        EXEC stage.usp_GetImportBatchResult
            @ImportBatchId = 0,
            @FileChecksum = @Checksum;
        THROW 51814, 'A non-positive batch identifier was unexpectedly accepted.', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() = 51800
            SET @ExpectedRejections += 1;
        ELSE
            THROW;
    END CATCH;

    BEGIN TRY
        EXEC stage.usp_GetImportBatchResult
            @ImportBatchId = @ImportBatchId,
            @FileChecksum = NULL;
        THROW 51815, 'A null checksum was unexpectedly accepted.', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() = 51801
            SET @ExpectedRejections += 1;
        ELSE
            THROW;
    END CATCH;

    IF @ExpectedRejections <> 3
        THROW 51816, 'The controlled batch-result rejection count is incorrect.', 1;

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF EXISTS
(
    SELECT 1
    FROM stage.ImportBatch
    WHERE FileChecksum IN (@Checksum, @WrongChecksum)
)
    THROW 51817, 'The controlled batch-result fixture remains after rollback.', 1;

SELECT
    CAST('stage' AS varchar(10)) AS SchemaName,
    CAST('controlled batch result' AS varchar(30)) AS ComponentName,
    CAST('PASS' AS varchar(10)) AS ExactResult,
    @ExpectedRejections AS ExpectedRejections,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
