/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 007_create_reference_loader.sql
    Purpose: Create an atomic, strict, rerunnable reference-data bootstrap.

    The client supplies one JSON document containing all six reference arrays.
    Existing natural keys must either match exactly or fail the whole operation;
    this procedure never silently updates reference data.
*/

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE core.usp_BootstrapReferenceData
    @ReferencePayload nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @InitialTranCount int = @@TRANCOUNT;
    DECLARE @LockResult int;
    DECLARE @Infinity datetime2(3) = CONVERT(datetime2(3), '9999-12-31 23:59:59.999');
    DECLARE @OfficeInserted int = 0;
    DECLARE @DepartmentInserted int = 0;
    DECLARE @PersonInserted int = 0;
    DECLARE @DeviceInserted int = 0;
    DECLARE @AssignmentInserted int = 0;
    DECLARE @AccessPointInserted int = 0;

    DECLARE @Office TABLE
    (
        OfficeCode varchar(20) NOT NULL PRIMARY KEY,
        DisplayName nvarchar(100) NOT NULL,
        TimeZoneName nvarchar(128) NOT NULL,
        Capacity int NOT NULL,
        IsActive bit NOT NULL
    );

    DECLARE @Department TABLE
    (
        DepartmentCode varchar(20) NOT NULL PRIMARY KEY,
        DepartmentName nvarchar(100) NOT NULL,
        IsActive bit NOT NULL
    );

    DECLARE @Person TABLE
    (
        PersonnelCode varchar(20) NOT NULL PRIMARY KEY,
        DisplayName nvarchar(100) NOT NULL,
        SyntheticEmail varchar(254) NOT NULL,
        DepartmentCode varchar(20) NOT NULL,
        ValidFrom date NOT NULL,
        ValidTo date NULL
    );

    DECLARE @Device TABLE
    (
        DeviceToken varchar(12) NOT NULL PRIMARY KEY,
        DeviceStatus varchar(10) NOT NULL
    );

    DECLARE @Assignment TABLE
    (
        PersonnelCode varchar(20) NOT NULL,
        DeviceToken varchar(12) NOT NULL,
        ValidFrom datetime2(3) NOT NULL,
        ValidTo datetime2(3) NULL,
        PRIMARY KEY (DeviceToken, ValidFrom)
    );

    DECLARE @AccessPoint TABLE
    (
        AccessPointCode varchar(30) NOT NULL PRIMARY KEY,
        OfficeCode varchar(20) NOT NULL,
        AccessPointType varchar(20) NOT NULL,
        DisplayLabel nvarchar(100) NOT NULL,
        IsActive bit NOT NULL
    );

    IF ISJSON(@ReferencePayload) <> 1
        THROW 51300, 'Reference payload must be valid JSON.', 1;

    IF JSON_QUERY(@ReferencePayload, '$.offices') IS NULL
       OR JSON_QUERY(@ReferencePayload, '$.departments') IS NULL
       OR JSON_QUERY(@ReferencePayload, '$.people') IS NULL
       OR JSON_QUERY(@ReferencePayload, '$.devices') IS NULL
       OR JSON_QUERY(@ReferencePayload, '$.device_assignments') IS NULL
       OR JSON_QUERY(@ReferencePayload, '$.access_points') IS NULL
        THROW 51301, 'Reference payload is missing one or more required arrays.', 1;

    INSERT INTO @Office
    SELECT OfficeCode, DisplayName, TimeZoneName, Capacity, IsActive
    FROM OPENJSON(@ReferencePayload, '$.offices')
    WITH
    (
        OfficeCode varchar(20) '$.office_code',
        DisplayName nvarchar(100) '$.display_name',
        TimeZoneName nvarchar(128) '$.time_zone_name',
        Capacity int '$.capacity',
        IsActive bit '$.is_active'
    );

    INSERT INTO @Department
    SELECT DepartmentCode, DepartmentName, IsActive
    FROM OPENJSON(@ReferencePayload, '$.departments')
    WITH
    (
        DepartmentCode varchar(20) '$.department_code',
        DepartmentName nvarchar(100) '$.department_name',
        IsActive bit '$.is_active'
    );

    INSERT INTO @Person
    SELECT PersonnelCode, DisplayName, SyntheticEmail, DepartmentCode, ValidFrom, ValidTo
    FROM OPENJSON(@ReferencePayload, '$.people')
    WITH
    (
        PersonnelCode varchar(20) '$.personnel_code',
        DisplayName nvarchar(100) '$.display_name',
        SyntheticEmail varchar(254) '$.synthetic_email',
        DepartmentCode varchar(20) '$.department_code',
        ValidFrom date '$.valid_from',
        ValidTo date '$.valid_to'
    );

    INSERT INTO @Device
    SELECT DeviceToken, DeviceStatus
    FROM OPENJSON(@ReferencePayload, '$.devices')
    WITH
    (
        DeviceToken varchar(12) '$.device_token',
        DeviceStatus varchar(10) '$.device_status'
    );

    INSERT INTO @Assignment
    SELECT PersonnelCode, DeviceToken, ValidFrom, ValidTo
    FROM OPENJSON(@ReferencePayload, '$.device_assignments')
    WITH
    (
        PersonnelCode varchar(20) '$.personnel_code',
        DeviceToken varchar(12) '$.device_token',
        ValidFrom datetime2(3) '$.valid_from_utc',
        ValidTo datetime2(3) '$.valid_to_utc'
    );

    INSERT INTO @AccessPoint
    SELECT AccessPointCode, OfficeCode, AccessPointType, DisplayLabel, IsActive
    FROM OPENJSON(@ReferencePayload, '$.access_points')
    WITH
    (
        OfficeCode varchar(20) '$.office_code',
        AccessPointCode varchar(30) '$.access_point_code',
        AccessPointType varchar(20) '$.access_point_type',
        DisplayLabel nvarchar(100) '$.display_label',
        IsActive bit '$.is_active'
    );

    IF NOT EXISTS (SELECT 1 FROM @Office)
       OR NOT EXISTS (SELECT 1 FROM @Department)
       OR NOT EXISTS (SELECT 1 FROM @Person)
       OR NOT EXISTS (SELECT 1 FROM @Device)
       OR NOT EXISTS (SELECT 1 FROM @Assignment)
       OR NOT EXISTS (SELECT 1 FROM @AccessPoint)
        THROW 51302, 'Every reference array must contain at least one row.', 1;

    IF EXISTS
    (
        SELECT 1
        FROM @Assignment AS first_assignment
        INNER JOIN @Assignment AS second_assignment
            ON second_assignment.DeviceToken = first_assignment.DeviceToken
           AND second_assignment.ValidFrom > first_assignment.ValidFrom
           AND second_assignment.ValidFrom < COALESCE(first_assignment.ValidTo, @Infinity)
           AND first_assignment.ValidFrom < COALESCE(second_assignment.ValidTo, @Infinity)
    )
        THROW 51303, 'Reference payload contains overlapping device assignments.', 1;

    BEGIN TRY
        IF @InitialTranCount = 0
            BEGIN TRANSACTION;
        ELSE
            SAVE TRANSACTION BootstrapReferenceSavepoint;

        EXEC @LockResult = sys.sp_getapplock
            @Resource = N'core.usp_BootstrapReferenceData',
            @LockMode = N'Exclusive',
            @LockOwner = N'Transaction',
            @LockTimeout = 15000;

        IF @LockResult < 0
            THROW 51304, 'Could not acquire the reference-bootstrap application lock.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @Office AS source
            INNER JOIN core.Office AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.OfficeCode = source.OfficeCode
            WHERE target.DisplayName <> source.DisplayName
               OR target.TimeZoneName <> source.TimeZoneName
               OR target.Capacity <> source.Capacity
               OR target.IsActive <> source.IsActive
        )
            THROW 51310, 'Existing office conflicts with the reference payload.', 1;

        INSERT INTO core.Office (OfficeCode, DisplayName, TimeZoneName, Capacity, IsActive)
        SELECT source.OfficeCode, source.DisplayName, source.TimeZoneName, source.Capacity, source.IsActive
        FROM @Office AS source
        WHERE NOT EXISTS
        (
            SELECT 1 FROM core.Office AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.OfficeCode = source.OfficeCode
        );
        SET @OfficeInserted = @@ROWCOUNT;

        IF EXISTS
        (
            SELECT 1
            FROM @Department AS source
            INNER JOIN core.Department AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.DepartmentCode = source.DepartmentCode
            WHERE target.DepartmentName <> source.DepartmentName
               OR target.IsActive <> source.IsActive
        )
            THROW 51311, 'Existing department conflicts with the reference payload.', 1;

        INSERT INTO core.Department (DepartmentCode, DepartmentName, IsActive)
        SELECT source.DepartmentCode, source.DepartmentName, source.IsActive
        FROM @Department AS source
        WHERE NOT EXISTS
        (
            SELECT 1 FROM core.Department AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.DepartmentCode = source.DepartmentCode
        );
        SET @DepartmentInserted = @@ROWCOUNT;

        IF EXISTS
        (
            SELECT 1
            FROM @Person AS source
            LEFT JOIN core.Department AS department
                ON department.DepartmentCode = source.DepartmentCode
            WHERE department.DepartmentId IS NULL
        )
            THROW 51312, 'A person references an unknown department.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @Person AS source
            INNER JOIN core.Department AS department
                ON department.DepartmentCode = source.DepartmentCode
            INNER JOIN core.Person AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.PersonnelCode = source.PersonnelCode
            WHERE target.DisplayName <> source.DisplayName
               OR target.SyntheticEmail <> source.SyntheticEmail
               OR target.DepartmentId <> department.DepartmentId
               OR target.ValidFrom <> source.ValidFrom
               OR (target.ValidTo <> source.ValidTo)
               OR (target.ValidTo IS NULL AND source.ValidTo IS NOT NULL)
               OR (target.ValidTo IS NOT NULL AND source.ValidTo IS NULL)
        )
            THROW 51313, 'Existing person conflicts with the reference payload.', 1;

        INSERT INTO core.Person
        (
            PersonnelCode, DisplayName, SyntheticEmail, DepartmentId, ValidFrom, ValidTo
        )
        SELECT
            source.PersonnelCode,
            source.DisplayName,
            source.SyntheticEmail,
            department.DepartmentId,
            source.ValidFrom,
            source.ValidTo
        FROM @Person AS source
        INNER JOIN core.Department AS department
            ON department.DepartmentCode = source.DepartmentCode
        WHERE NOT EXISTS
        (
            SELECT 1 FROM core.Person AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.PersonnelCode = source.PersonnelCode
        );
        SET @PersonInserted = @@ROWCOUNT;

        IF EXISTS
        (
            SELECT 1
            FROM @Device AS source
            INNER JOIN core.Device AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.DeviceToken = source.DeviceToken
            WHERE target.DeviceStatus <> source.DeviceStatus
        )
            THROW 51314, 'Existing device conflicts with the reference payload.', 1;

        INSERT INTO core.Device (DeviceToken, DeviceStatus)
        SELECT source.DeviceToken, source.DeviceStatus
        FROM @Device AS source
        WHERE NOT EXISTS
        (
            SELECT 1 FROM core.Device AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.DeviceToken = source.DeviceToken
        );
        SET @DeviceInserted = @@ROWCOUNT;

        IF EXISTS
        (
            SELECT 1
            FROM @Assignment AS source
            LEFT JOIN core.Person AS person ON person.PersonnelCode = source.PersonnelCode
            LEFT JOIN core.Device AS device ON device.DeviceToken = source.DeviceToken
            WHERE person.PersonId IS NULL OR device.DeviceId IS NULL
        )
            THROW 51315, 'An assignment references an unknown person or device.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @Assignment AS source
            INNER JOIN core.Person AS person ON person.PersonnelCode = source.PersonnelCode
            WHERE source.ValidFrom < CONVERT(datetime2(3), person.ValidFrom)
               OR
               (
                   person.ValidTo IS NOT NULL
                   AND
                   (
                       source.ValidFrom >= CONVERT(datetime2(3), person.ValidTo)
                       OR source.ValidTo IS NULL
                       OR source.ValidTo > CONVERT(datetime2(3), person.ValidTo)
                   )
               )
        )
            THROW 51316, 'An assignment falls outside its person validity period.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @Assignment AS source
            INNER JOIN core.Person AS person ON person.PersonnelCode = source.PersonnelCode
            INNER JOIN core.Device AS device ON device.DeviceToken = source.DeviceToken
            INNER JOIN core.PersonDeviceAssignment AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.DeviceId = device.DeviceId
               AND target.ValidFrom = source.ValidFrom
            WHERE target.PersonId <> person.PersonId
               OR (target.ValidTo <> source.ValidTo)
               OR (target.ValidTo IS NULL AND source.ValidTo IS NOT NULL)
               OR (target.ValidTo IS NOT NULL AND source.ValidTo IS NULL)
        )
            THROW 51317, 'Existing device assignment conflicts with the reference payload.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @Assignment AS source
            INNER JOIN core.Device AS device ON device.DeviceToken = source.DeviceToken
            INNER JOIN core.PersonDeviceAssignment AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.DeviceId = device.DeviceId
               AND target.ValidFrom < COALESCE(source.ValidTo, @Infinity)
               AND source.ValidFrom < COALESCE(target.ValidTo, @Infinity)
               AND target.ValidFrom <> source.ValidFrom
        )
            THROW 51318, 'A payload assignment overlaps an existing assignment.', 1;

        INSERT INTO core.PersonDeviceAssignment (PersonId, DeviceId, ValidFrom, ValidTo)
        SELECT person.PersonId, device.DeviceId, source.ValidFrom, source.ValidTo
        FROM @Assignment AS source
        INNER JOIN core.Person AS person ON person.PersonnelCode = source.PersonnelCode
        INNER JOIN core.Device AS device ON device.DeviceToken = source.DeviceToken
        WHERE NOT EXISTS
        (
            SELECT 1
            FROM core.PersonDeviceAssignment AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.DeviceId = device.DeviceId
              AND target.ValidFrom = source.ValidFrom
        );
        SET @AssignmentInserted = @@ROWCOUNT;

        IF EXISTS
        (
            SELECT 1
            FROM @AccessPoint AS source
            LEFT JOIN core.Office AS office ON office.OfficeCode = source.OfficeCode
            WHERE office.OfficeId IS NULL
        )
            THROW 51319, 'An access point references an unknown office.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM @AccessPoint AS source
            INNER JOIN core.Office AS office ON office.OfficeCode = source.OfficeCode
            INNER JOIN core.AccessPoint AS target WITH (UPDLOCK, HOLDLOCK)
                ON target.AccessPointCode = source.AccessPointCode
            WHERE target.OfficeId <> office.OfficeId
               OR target.AccessPointType <> source.AccessPointType
               OR target.DisplayLabel <> source.DisplayLabel
               OR target.IsActive <> source.IsActive
        )
            THROW 51320, 'Existing access point conflicts with the reference payload.', 1;

        INSERT INTO core.AccessPoint
        (
            OfficeId, AccessPointCode, AccessPointType, DisplayLabel, IsActive
        )
        SELECT
            office.OfficeId,
            source.AccessPointCode,
            source.AccessPointType,
            source.DisplayLabel,
            source.IsActive
        FROM @AccessPoint AS source
        INNER JOIN core.Office AS office ON office.OfficeCode = source.OfficeCode
        WHERE NOT EXISTS
        (
            SELECT 1 FROM core.AccessPoint AS target WITH (UPDLOCK, HOLDLOCK)
            WHERE target.AccessPointCode = source.AccessPointCode
        );
        SET @AccessPointInserted = @@ROWCOUNT;

        IF @InitialTranCount = 0
            COMMIT TRANSACTION;

        SELECT
            @OfficeInserted AS OfficesInserted,
            @DepartmentInserted AS DepartmentsInserted,
            @PersonInserted AS PeopleInserted,
            @DeviceInserted AS DevicesInserted,
            @AssignmentInserted AS AssignmentsInserted,
            @AccessPointInserted AS AccessPointsInserted,
            CAST
            (
                CASE
                    WHEN @OfficeInserted + @DepartmentInserted + @PersonInserted
                       + @DeviceInserted + @AssignmentInserted + @AccessPointInserted = 0
                        THEN 'UNCHANGED'
                    ELSE 'APPLIED'
                END
                AS varchar(10)
            ) AS BootstrapResult;
    END TRY
    BEGIN CATCH
        IF @InitialTranCount = 0 AND XACT_STATE() <> 0
            ROLLBACK TRANSACTION;
        ELSE IF @InitialTranCount > 0 AND XACT_STATE() = 1
            ROLLBACK TRANSACTION BootstrapReferenceSavepoint;

        THROW;
    END CATCH;
END;
GO
