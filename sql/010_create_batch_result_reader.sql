/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 010_create_batch_result_reader.sql
    Purpose: Expose one privacy-safe import-batch result to the application loader.

    The loader uses this interface to verify an ALREADY_PROCESSED response
    without receiving SELECT permission on stage.ImportBatch. Both the batch
    identifier and its content checksum must match.
*/

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE stage.usp_GetImportBatchResult
    @ImportBatchId bigint,
    @FileChecksum binary(32)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @ImportBatchId IS NULL OR @ImportBatchId <= 0
        THROW 51800, 'ImportBatchId must be a positive identifier.', 1;

    IF @FileChecksum IS NULL
        THROW 51801, 'FileChecksum is required.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM stage.ImportBatch
        WHERE ImportBatchId = @ImportBatchId
          AND FileChecksum = @FileChecksum
    )
        THROW 51802, 'The requested import-batch result was not found.', 1;

    SELECT
        SourceType,
        SourceFileName,
        CONVERT(varchar(64), FileChecksum, 2) AS FileChecksumHex,
        Status,
        RowsReceived,
        RowsAccepted,
        RowsRejected
    FROM stage.ImportBatch
    WHERE ImportBatchId = @ImportBatchId
      AND FileChecksum = @FileChecksum;
END;
GO
