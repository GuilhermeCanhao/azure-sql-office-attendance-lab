/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 012_create_reporting_views.sql
    Purpose: Create the aggregate, privacy-safe reporting publication surface.

    The views deliberately omit person, device, access-point, source-row,
    filename, checksum, error-text, and individual timestamp fields. The
    script is rerunnable and creates no user, identity, grant, or canonical row.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

BEGIN TRY
    BEGIN TRANSACTION;

    EXEC(N'
        CREATE OR ALTER VIEW report.vw_DailyAttendanceTrend
        AS
        SELECT
            summary.AttendanceDateLocal,
            office.OfficeCode,
            office.DisplayName AS OfficeName,
            office.Capacity AS OfficeCapacity,
            COUNT_BIG(*) AS PersonDayCount,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''CARD'' THEN 1 ELSE 0 END))
                AS CardOnlyPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''WIFI'' THEN 1 ELSE 0 END))
                AS WifiOnlyPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''BOTH'' THEN 1 ELSE 0 END))
                AS BothPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod IN (''CARD'', ''BOTH'') THEN 1 ELSE 0 END))
                AS BadgeObservedPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod IN (''WIFI'', ''BOTH'') THEN 1 ELSE 0 END))
                AS WifiObservedPersonDays,
            CONVERT
            (
                decimal(9, 6),
                CONVERT(decimal(19, 6), COUNT_BIG(*))
                    / NULLIF(CONVERT(decimal(19, 6), office.Capacity), 0)
            ) AS OccupancyRate
        FROM core.DailyAttendanceSummary AS summary
        INNER JOIN core.Office AS office
            ON office.OfficeId = summary.OfficeId
        GROUP BY
            summary.AttendanceDateLocal,
            office.OfficeCode,
            office.DisplayName,
            office.Capacity;
    ');

    EXEC(N'
        CREATE OR ALTER VIEW report.vw_DailyDepartmentAttendance
        AS
        SELECT
            summary.AttendanceDateLocal,
            office.OfficeCode,
            office.DisplayName AS OfficeName,
            department.DepartmentCode,
            department.DepartmentName,
            COUNT_BIG(*) AS PersonDayCount,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''CARD'' THEN 1 ELSE 0 END))
                AS CardOnlyPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''WIFI'' THEN 1 ELSE 0 END))
                AS WifiOnlyPersonDays,
            SUM(CONVERT(bigint, CASE WHEN summary.DetectionMethod = ''BOTH'' THEN 1 ELSE 0 END))
                AS BothPersonDays
        FROM core.DailyAttendanceSummary AS summary
        INNER JOIN core.Office AS office
            ON office.OfficeId = summary.OfficeId
        INNER JOIN core.Person AS person
            ON person.PersonId = summary.PersonId
        INNER JOIN core.Department AS department
            ON department.DepartmentId = person.DepartmentId
        GROUP BY
            summary.AttendanceDateLocal,
            office.OfficeCode,
            office.DisplayName,
            department.DepartmentCode,
            department.DepartmentName;
    ');

    EXEC(N'
        CREATE OR ALTER VIEW report.vw_LoadQualitySummary
        AS
        SELECT
            batch.SourceType,
            SUM(CONVERT(bigint, CASE WHEN batch.Status IN (''COMPLETED'', ''PARTIAL'', ''FAILED'') THEN 1 ELSE 0 END))
                AS TerminalBatchCount,
            SUM(CONVERT(bigint, CASE WHEN batch.Status = ''STARTED'' THEN 1 ELSE 0 END))
                AS InProgressBatchCount,
            SUM(CONVERT(bigint, CASE WHEN batch.Status = ''COMPLETED'' THEN 1 ELSE 0 END))
                AS CompletedWithoutRejectsBatchCount,
            SUM(CONVERT(bigint, CASE WHEN batch.Status = ''PARTIAL'' THEN 1 ELSE 0 END))
                AS CompletedWithRejectsBatchCount,
            SUM(CONVERT(bigint, CASE WHEN batch.Status = ''FAILED'' THEN 1 ELSE 0 END))
                AS FailedBatchCount,
            SUM(CONVERT(bigint, batch.RowsReceived)) AS RowsReceived,
            SUM(CONVERT(bigint, batch.RowsAccepted)) AS RowsAccepted,
            SUM(CONVERT(bigint, batch.RowsRejected)) AS RowsRejected,
            CONVERT
            (
                decimal(9, 6),
                CASE
                    WHEN SUM(CONVERT(bigint, batch.RowsReceived)) = 0 THEN 0
                    ELSE CONVERT(decimal(19, 6), SUM(CONVERT(bigint, batch.RowsAccepted)))
                        / CONVERT(decimal(19, 6), SUM(CONVERT(bigint, batch.RowsReceived)))
                END
            ) AS AcceptanceRate
        FROM stage.ImportBatch AS batch
        GROUP BY batch.SourceType;
    ');

    EXEC(N'
        CREATE OR ALTER VIEW report.vw_ValidationIssueSummary
        AS
        SELECT
            batch.SourceType,
            issue.ValidationCode,
            COUNT_BIG(*) AS RejectedRowCount
        FROM stage.ImportError AS issue
        INNER JOIN stage.ImportBatch AS batch
            ON batch.ImportBatchId = issue.ImportBatchId
        GROUP BY
            batch.SourceType,
            issue.ValidationCode;
    ');

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
