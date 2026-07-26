/*
    Phase 6 reversible candidate index.

    This script deliberately rejects a pre-existing candidate so the experiment
    cannot mistake retained state for a clean deployment.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'core.DailyAttendanceSummary', N'U') IS NULL
    THROW 52100, 'The daily attendance summary table is missing.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'core.DailyAttendanceSummary')
      AND name = N'IX_core_DailyAttendanceSummary_OfficeDateMethod'
)
    THROW 52101, 'The performance candidate already exists.', 1;

BEGIN TRANSACTION;

BEGIN TRY
    CREATE INDEX IX_core_DailyAttendanceSummary_OfficeDateMethod
    ON core.DailyAttendanceSummary
    (
        OfficeId,
        AttendanceDateLocal,
        DetectionMethod
    );

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
