/* Remove the Phase 6 candidate and verify that cleanup completed. */

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRANSACTION;

BEGIN TRY
    IF EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'core.DailyAttendanceSummary')
          AND name = N'IX_core_DailyAttendanceSummary_OfficeDateMethod'
    )
        DROP INDEX IX_core_DailyAttendanceSummary_OfficeDateMethod
        ON core.DailyAttendanceSummary;

    IF EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'core.DailyAttendanceSummary')
          AND name = N'IX_core_DailyAttendanceSummary_OfficeDateMethod'
    )
        THROW 52102, 'The performance candidate remains after cleanup.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
