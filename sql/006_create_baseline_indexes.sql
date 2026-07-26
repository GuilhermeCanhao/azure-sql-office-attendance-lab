/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 006_create_baseline_indexes.sql
    Purpose: Add the minimum justified baseline index for daily reconciliation.

    Existing primary, unique, and assignment-supporting indexes already cover
    batch lineage, reference lookup, device resolution, and the daily-summary
    natural key. Additional investigative and Tableau indexes remain hypotheses
    for the measured performance phase rather than speculative write overhead.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'core.AttendanceSignal')
          AND name = N'IX_core_AttendanceSignal_DailySummaryRefresh'
    )
    BEGIN
        CREATE INDEX IX_core_AttendanceSignal_DailySummaryRefresh
            ON core.AttendanceSignal
            (
                AttendanceDateLocal,
                OfficeId,
                PersonId
            )
            INCLUDE
            (
                SignalType,
                ObservedAtUtc
            );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
