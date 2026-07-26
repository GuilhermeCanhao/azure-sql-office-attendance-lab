/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 009_create_daily_summary_refresh.sql
    Purpose: Rebuild the reproducible daily attendance projection.

    AttendanceSignal remains authoritative. This procedure replaces either the
    complete projection or one inclusive local-date range in one transaction.
*/

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE core.usp_RefreshDailyAttendanceSummary
    @FromDate date = NULL,
    @ThroughDate date = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @InitialTranCount int = @@TRANCOUNT;
    DECLARE @LockResult int;
    DECLARE @RowsRefreshed int;
    DECLARE @RefreshAll bit = CASE
        WHEN @FromDate IS NULL AND @ThroughDate IS NULL THEN 1
        ELSE 0
    END;
    DECLARE @EffectiveFromDate date;
    DECLARE @EffectiveThroughDate date;
    DECLARE @RefreshedAtUtc datetime2(3) = SYSUTCDATETIME();

    IF (@FromDate IS NULL AND @ThroughDate IS NOT NULL)
       OR (@FromDate IS NOT NULL AND @ThroughDate IS NULL)
        THROW 51700, 'FromDate and ThroughDate must both be supplied or both be NULL.', 1;

    IF @FromDate IS NOT NULL AND @FromDate > @ThroughDate
        THROW 51701, 'FromDate cannot follow ThroughDate.', 1;

    IF @RefreshAll = 1
    BEGIN
        SELECT
            @EffectiveFromDate = MIN(AttendanceDateLocal),
            @EffectiveThroughDate = MAX(AttendanceDateLocal)
        FROM core.AttendanceSignal;
    END;
    ELSE
    BEGIN
        SET @EffectiveFromDate = @FromDate;
        SET @EffectiveThroughDate = @ThroughDate;
    END;

    BEGIN TRY
        IF @InitialTranCount = 0
            BEGIN TRANSACTION;
        ELSE
            SAVE TRANSACTION RefreshDailySummarySavepoint;

        EXEC @LockResult = sys.sp_getapplock
            @Resource = N'core.usp_RefreshDailyAttendanceSummary',
            @LockMode = N'Exclusive',
            @LockOwner = N'Transaction',
            @LockTimeout = 15000;

        IF @LockResult < 0
            THROW 51702, 'Could not acquire the daily-summary refresh lock.', 1;

        IF @RefreshAll = 1
        BEGIN
            DELETE FROM core.DailyAttendanceSummary;
        END;
        ELSE
        BEGIN
            DELETE FROM core.DailyAttendanceSummary
            WHERE AttendanceDateLocal >= @EffectiveFromDate
              AND AttendanceDateLocal <= @EffectiveThroughDate;
        END;

        INSERT INTO core.DailyAttendanceSummary
        (
            AttendanceDateLocal,
            OfficeId,
            PersonId,
            DetectionMethod,
            FirstObservedAtUtc,
            LastObservedAtUtc,
            CardSignalCount,
            WifiSignalCount,
            RefreshedAtUtc
        )
        SELECT
            signal.AttendanceDateLocal,
            signal.OfficeId,
            signal.PersonId,
            CASE
                WHEN SUM(CASE WHEN signal.SignalType = 'CARD' THEN 1 ELSE 0 END) > 0
                 AND SUM(CASE WHEN signal.SignalType = 'WIFI' THEN 1 ELSE 0 END) > 0
                    THEN 'BOTH'
                WHEN SUM(CASE WHEN signal.SignalType = 'CARD' THEN 1 ELSE 0 END) > 0
                    THEN 'CARD'
                ELSE 'WIFI'
            END,
            MIN(signal.ObservedAtUtc),
            MAX(signal.ObservedAtUtc),
            SUM(CASE WHEN signal.SignalType = 'CARD' THEN 1 ELSE 0 END),
            SUM(CASE WHEN signal.SignalType = 'WIFI' THEN 1 ELSE 0 END),
            @RefreshedAtUtc
        FROM core.AttendanceSignal AS signal
        WHERE @RefreshAll = 1
           OR
           (
               signal.AttendanceDateLocal >= @EffectiveFromDate
               AND signal.AttendanceDateLocal <= @EffectiveThroughDate
           )
        GROUP BY
            signal.AttendanceDateLocal,
            signal.OfficeId,
            signal.PersonId;

        SET @RowsRefreshed = @@ROWCOUNT;

        IF @InitialTranCount = 0
            COMMIT TRANSACTION;

        SELECT
            @EffectiveFromDate AS EffectiveFromDate,
            @EffectiveThroughDate AS EffectiveThroughDate,
            @RowsRefreshed AS RowsRefreshed,
            CAST(CASE WHEN @RefreshAll = 1 THEN 'FULL' ELSE 'RANGE' END AS varchar(10)) AS RefreshScope;
    END TRY
    BEGIN CATCH
        IF @InitialTranCount = 0 AND XACT_STATE() <> 0
            ROLLBACK TRANSACTION;
        ELSE IF @InitialTranCount > 0 AND XACT_STATE() = 1
            ROLLBACK TRANSACTION RefreshDailySummarySavepoint;

        THROW;
    END CATCH;
END;
GO
