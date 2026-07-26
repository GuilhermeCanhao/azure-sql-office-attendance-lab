/*
    Verification for 005_create_assignment_procedure.sql.

    The script creates unmistakably synthetic fixtures, exercises successful
    and rejected assignments, and removes every fixture before it finishes.
*/

SET NOCOUNT ON;
SET XACT_ABORT OFF;

IF OBJECT_ID(N'core.usp_AssignDevice', N'P') IS NULL
    THROW 51200, 'The core.usp_AssignDevice procedure is missing.', 1;

IF
(
    SELECT COUNT(*)
    FROM sys.parameters
    WHERE object_id = OBJECT_ID(N'core.usp_AssignDevice')
      AND name IN (N'@PersonId', N'@DeviceId', N'@ValidFrom', N'@ValidTo')
) <> 4
    THROW 51201, 'The assignment procedure has unexpected parameters.', 1;

DECLARE @ProcedureDefinition nvarchar(max) =
(
    SELECT modules.definition
    FROM sys.sql_modules AS modules
    WHERE modules.object_id = OBJECT_ID(N'core.usp_AssignDevice')
);

IF @ProcedureDefinition NOT LIKE N'%UPDLOCK%'
   OR @ProcedureDefinition NOT LIKE N'%HOLDLOCK%'
   OR @ProcedureDefinition NOT LIKE N'%UX_core_PersonDeviceAssignment_Device_ValidFrom%'
    THROW 51202, 'The assignment procedure is missing its concurrency-control contract.', 1;

DECLARE @DepartmentId int;
DECLARE @PersonId int;
DECLARE @ActiveDeviceId int;
DECLARE @RetiredDeviceId int;
DECLARE @UnexpectedSuccess bit;

BEGIN TRY
    /* Remove fixtures left only by an interrupted earlier verification run. */
    DELETE assignment
    FROM core.PersonDeviceAssignment AS assignment
    INNER JOIN core.Device AS device
        ON device.DeviceId = assignment.DeviceId
    WHERE device.DeviceToken IN ('DEV-FFF00001', 'DEV-FFF00002');

    DELETE FROM core.Device
    WHERE DeviceToken IN ('DEV-FFF00001', 'DEV-FFF00002');

    DELETE FROM core.Person
    WHERE PersonnelCode = 'TST-ASGN-001';

    DELETE FROM core.Department
    WHERE DepartmentCode = 'TST-ASGN';

    INSERT INTO core.Department
    (
        DepartmentCode,
        DepartmentName
    )
    VALUES
    (
        'TST-ASGN',
        N'Assignment Procedure Verification'
    );

    SET @DepartmentId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Person
    (
        PersonnelCode,
        DisplayName,
        SyntheticEmail,
        DepartmentId,
        ValidFrom,
        ValidTo
    )
    VALUES
    (
        'TST-ASGN-001',
        N'Synthetic Assignment Test Person',
        'tst-asgn-001@attendance-lab.example',
        @DepartmentId,
        '2026-01-01',
        NULL
    );

    SET @PersonId = CONVERT(int, SCOPE_IDENTITY());

    INSERT INTO core.Device
    (
        DeviceToken,
        DeviceStatus
    )
    VALUES
        ('DEV-FFF00001', 'ACTIVE'),
        ('DEV-FFF00002', 'RETIRED');

    SELECT @ActiveDeviceId = DeviceId
    FROM core.Device
    WHERE DeviceToken = 'DEV-FFF00001';

    SELECT @RetiredDeviceId = DeviceId
    FROM core.Device
    WHERE DeviceToken = 'DEV-FFF00002';

    /* Valid bounded assignment. */
    EXEC core.usp_AssignDevice
        @PersonId = @PersonId,
        @DeviceId = @ActiveDeviceId,
        @ValidFrom = '2026-01-10T09:00:00.000',
        @ValidTo = '2026-02-01T00:00:00.000';

    /* Exactly adjacent: allowed by the half-open interval model. */
    EXEC core.usp_AssignDevice
        @PersonId = @PersonId,
        @DeviceId = @ActiveDeviceId,
        @ValidFrom = '2026-02-01T00:00:00.000',
        @ValidTo = '2026-03-01T00:00:00.000';

    /* Partial overlap must fail. */
    SET @UnexpectedSuccess = 0;
    BEGIN TRY
        EXEC core.usp_AssignDevice
            @PersonId = @PersonId,
            @DeviceId = @ActiveDeviceId,
            @ValidFrom = '2026-01-20T00:00:00.000',
            @ValidTo = '2026-02-10T00:00:00.000';
        SET @UnexpectedSuccess = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51105
            THROW;
    END CATCH;

    IF @UnexpectedSuccess = 1
        THROW 51203, 'A partially overlapping assignment was accepted.', 1;

    /* Contained overlap must fail. */
    SET @UnexpectedSuccess = 0;
    BEGIN TRY
        EXEC core.usp_AssignDevice
            @PersonId = @PersonId,
            @DeviceId = @ActiveDeviceId,
            @ValidFrom = '2026-01-15T00:00:00.000',
            @ValidTo = '2026-01-16T00:00:00.000';
        SET @UnexpectedSuccess = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51105
            THROW;
    END CATCH;

    IF @UnexpectedSuccess = 1
        THROW 51204, 'A contained overlapping assignment was accepted.', 1;

    /* One open-ended assignment may start at the adjacent boundary. */
    EXEC core.usp_AssignDevice
        @PersonId = @PersonId,
        @DeviceId = @ActiveDeviceId,
        @ValidFrom = '2026-03-01T00:00:00.000',
        @ValidTo = NULL;

    /* A second open-ended assignment must overlap the existing one. */
    SET @UnexpectedSuccess = 0;
    BEGIN TRY
        EXEC core.usp_AssignDevice
            @PersonId = @PersonId,
            @DeviceId = @ActiveDeviceId,
            @ValidFrom = '2026-12-01T00:00:00.000',
            @ValidTo = NULL;
        SET @UnexpectedSuccess = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51105
            THROW;
    END CATCH;

    IF @UnexpectedSuccess = 1
        THROW 51205, 'A second open-ended assignment was accepted.', 1;

    /* Invalid zero-length interval must fail before any write. */
    SET @UnexpectedSuccess = 0;
    BEGIN TRY
        EXEC core.usp_AssignDevice
            @PersonId = @PersonId,
            @DeviceId = @ActiveDeviceId,
            @ValidFrom = '2026-06-01T00:00:00.000',
            @ValidTo = '2026-06-01T00:00:00.000';
        SET @UnexpectedSuccess = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51100
            THROW;
    END CATCH;

    IF @UnexpectedSuccess = 1
        THROW 51206, 'An invalid assignment range was accepted.', 1;

    /* Retired devices cannot receive a new assignment. */
    SET @UnexpectedSuccess = 0;
    BEGIN TRY
        EXEC core.usp_AssignDevice
            @PersonId = @PersonId,
            @DeviceId = @RetiredDeviceId,
            @ValidFrom = '2026-04-01T00:00:00.000',
            @ValidTo = '2026-05-01T00:00:00.000';
        SET @UnexpectedSuccess = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51104
            THROW;
    END CATCH;

    IF @UnexpectedSuccess = 1
        THROW 51207, 'A retired device received a new assignment.', 1;

    IF
    (
        SELECT COUNT(*)
        FROM core.PersonDeviceAssignment
        WHERE DeviceId = @ActiveDeviceId
    ) <> 3
        THROW 51208, 'The successful assignment count is unexpected.', 1;

    DELETE FROM core.PersonDeviceAssignment
    WHERE DeviceId IN (@ActiveDeviceId, @RetiredDeviceId);

    DELETE FROM core.Device
    WHERE DeviceId IN (@ActiveDeviceId, @RetiredDeviceId);

    DELETE FROM core.Person
    WHERE PersonId = @PersonId;

    DELETE FROM core.Department
    WHERE DepartmentId = @DepartmentId;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    DELETE assignment
    FROM core.PersonDeviceAssignment AS assignment
    INNER JOIN core.Device AS device
        ON device.DeviceId = assignment.DeviceId
    WHERE device.DeviceToken IN ('DEV-FFF00001', 'DEV-FFF00002');

    DELETE FROM core.Device
    WHERE DeviceToken IN ('DEV-FFF00001', 'DEV-FFF00002');

    DELETE FROM core.Person
    WHERE PersonnelCode = 'TST-ASGN-001';

    DELETE FROM core.Department
    WHERE DepartmentCode = 'TST-ASGN';

    THROW;
END CATCH;

IF EXISTS
(
    SELECT 1
    FROM core.Device
    WHERE DeviceToken IN ('DEV-FFF00001', 'DEV-FFF00002')
)
OR EXISTS
(
    SELECT 1
    FROM core.Person
    WHERE PersonnelCode = 'TST-ASGN-001'
)
OR EXISTS
(
    SELECT 1
    FROM core.Department
    WHERE DepartmentCode = 'TST-ASGN'
)
    THROW 51209, 'Verification fixtures were not completely removed.', 1;

SELECT
    CAST(OBJECT_SCHEMA_NAME(procedures.object_id) AS varchar(10)) AS SchemaName,
    CAST(procedures.name AS varchar(40)) AS ProcedureName,
    COUNT(parameters.parameter_id) AS ParameterCount,
    CAST('PASS' AS varchar(10)) AS BehaviorTests,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup
FROM sys.procedures AS procedures
LEFT JOIN sys.parameters AS parameters
    ON parameters.object_id = procedures.object_id
WHERE procedures.object_id = OBJECT_ID(N'core.usp_AssignDevice')
GROUP BY procedures.object_id, procedures.name;
