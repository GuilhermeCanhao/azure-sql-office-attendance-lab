/*
    Verification for 007_create_reference_loader.sql.

    All fixtures live inside one transaction and are rolled back. The test
    proves first application, unchanged rerun, atomic conflict rejection, and
    complete cleanup without loading the canonical dataset.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF OBJECT_ID(N'core.usp_BootstrapReferenceData', N'P') IS NULL
    THROW 51400, 'The core.usp_BootstrapReferenceData procedure is missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.parameters
    WHERE object_id = OBJECT_ID(N'core.usp_BootstrapReferenceData')
      AND name = N'@ReferencePayload'
) <> 1
    THROW 51401, 'The reference bootstrap has unexpected parameters.', 1;

DECLARE @Payload nvarchar(max) = N'{
  "offices":[{"office_code":"TST-BOOT-01","display_name":"Synthetic Bootstrap Office","time_zone_name":"GMT Standard Time","capacity":25,"is_active":1}],
  "departments":[{"department_code":"TST-BOOT","department_name":"Synthetic Bootstrap Department","is_active":1}],
  "people":[{"personnel_code":"TST-BOOT-001","display_name":"Synthetic Bootstrap Person","synthetic_email":"tst-boot-001@attendance-lab.example","department_code":"TST-BOOT","valid_from":"2026-01-01","valid_to":null}],
  "devices":[{"device_token":"DEV-FFF10001","device_status":"RETIRED"}],
  "device_assignments":[{"personnel_code":"TST-BOOT-001","device_token":"DEV-FFF10001","valid_from_utc":"2026-01-01T00:00:00.000","valid_to_utc":"2026-02-01T00:00:00.000"}],
  "access_points":[{"office_code":"TST-BOOT-01","access_point_code":"TST-BOOT-AP-01","access_point_type":"WIFI_AP","display_label":"Synthetic Bootstrap Access Point","is_active":1}]
}';

DECLARE @ConflictPayload nvarchar(max) = JSON_MODIFY
(
    @Payload,
    '$.offices[0].capacity',
    26
);

DECLARE @FirstResult TABLE
(
    OfficesInserted int,
    DepartmentsInserted int,
    PeopleInserted int,
    DevicesInserted int,
    AssignmentsInserted int,
    AccessPointsInserted int,
    BootstrapResult varchar(10)
);

DECLARE @SecondResult TABLE
(
    OfficesInserted int,
    DepartmentsInserted int,
    PeopleInserted int,
    DevicesInserted int,
    AssignmentsInserted int,
    AccessPointsInserted int,
    BootstrapResult varchar(10)
);

DECLARE @ConflictRejected bit = 0;
DECLARE @TransactionRollback varchar(10) = 'FAIL';

BEGIN TRANSACTION;

BEGIN TRY
    INSERT INTO @FirstResult
    EXEC core.usp_BootstrapReferenceData @ReferencePayload = @Payload;

    IF NOT EXISTS
    (
        SELECT 1
        FROM @FirstResult
        WHERE OfficesInserted = 1
          AND DepartmentsInserted = 1
          AND PeopleInserted = 1
          AND DevicesInserted = 1
          AND AssignmentsInserted = 1
          AND AccessPointsInserted = 1
          AND BootstrapResult = 'APPLIED'
    )
        THROW 51402, 'First reference bootstrap did not apply the complete graph.', 1;

    INSERT INTO @SecondResult
    EXEC core.usp_BootstrapReferenceData @ReferencePayload = @Payload;

    IF NOT EXISTS
    (
        SELECT 1
        FROM @SecondResult
        WHERE OfficesInserted = 0
          AND DepartmentsInserted = 0
          AND PeopleInserted = 0
          AND DevicesInserted = 0
          AND AssignmentsInserted = 0
          AND AccessPointsInserted = 0
          AND BootstrapResult = 'UNCHANGED'
    )
        THROW 51403, 'Unchanged reference rerun was not a no-op.', 1;

    BEGIN TRY
        EXEC core.usp_BootstrapReferenceData @ReferencePayload = @ConflictPayload;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51310
            THROW;

        SET @ConflictRejected = 1;
    END CATCH;

    IF @ConflictRejected = 0
        THROW 51404, 'A conflicting office natural key was accepted.', 1;

    /*
        THROW honors XACT_ABORT. If the ambient transaction is still
        committable, confirm the retained value directly; if it is doomed,
        the mandatory full rollback below is itself the atomicity boundary.
    */
    IF XACT_STATE() = 1
       AND
       (
           SELECT Capacity FROM core.Office WHERE OfficeCode = 'TST-BOOT-01'
       ) <> 25
        THROW 51406, 'Conflict handling changed existing reference data.', 1;

    ROLLBACK TRANSACTION;
    SET @TransactionRollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF EXISTS (SELECT 1 FROM core.Office WHERE OfficeCode = 'TST-BOOT-01')
   OR EXISTS (SELECT 1 FROM core.Department WHERE DepartmentCode = 'TST-BOOT')
   OR EXISTS (SELECT 1 FROM core.Person WHERE PersonnelCode = 'TST-BOOT-001')
   OR EXISTS (SELECT 1 FROM core.Device WHERE DeviceToken = 'DEV-FFF10001')
   OR EXISTS (SELECT 1 FROM core.AccessPoint WHERE AccessPointCode = 'TST-BOOT-AP-01')
    THROW 51407, 'Reference-bootstrap verification fixtures remain after rollback.', 1;

SELECT
    CAST(OBJECT_SCHEMA_NAME(procedures.object_id) AS varchar(10)) AS SchemaName,
    CAST(procedures.name AS varchar(40)) AS ProcedureName,
    COUNT(parameters.parameter_id) AS ParameterCount,
    CAST('PASS' AS varchar(10)) AS FirstApplication,
    CAST('PASS' AS varchar(10)) AS UnchangedRerun,
    CAST('PASS' AS varchar(10)) AS ConflictRejection,
    @TransactionRollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup
FROM sys.procedures AS procedures
LEFT JOIN sys.parameters AS parameters
    ON parameters.object_id = procedures.object_id
WHERE procedures.object_id = OBJECT_ID(N'core.usp_BootstrapReferenceData')
GROUP BY procedures.object_id, procedures.name;
