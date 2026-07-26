/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 003_create_core_reference_tables.sql
    Purpose: Create the constrained core reference entities and relationships.

    ValidTo values are exclusive. Direct assignment DML will be restricted when
    the concurrency-safe assignment procedure and loader role are introduced.
    The script is rerunnable: existing tables and indexes are retained.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'core.Office', N'U') IS NULL
    BEGIN
        CREATE TABLE core.Office
        (
            OfficeId int IDENTITY(1, 1) NOT NULL,
            OfficeCode varchar(20) NOT NULL,
            DisplayName nvarchar(100) NOT NULL,
            TimeZoneName nvarchar(128) NOT NULL,
            Capacity int NOT NULL,
            IsActive bit NOT NULL
                CONSTRAINT DF_core_Office_IsActive DEFAULT (1),
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_Office_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_Office
                PRIMARY KEY CLUSTERED (OfficeId),
            CONSTRAINT UQ_core_Office_OfficeCode
                UNIQUE (OfficeCode),
            CONSTRAINT CK_core_Office_OfficeCode
                CHECK
                (
                    LEN(OfficeCode) > 0
                    AND OfficeCode COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z0-9-]%'
                    AND OfficeCode COLLATE Latin1_General_100_BIN2
                        LIKE '%[A-Z0-9]%'
                ),
            CONSTRAINT CK_core_Office_DisplayName
                CHECK (LEN(LTRIM(RTRIM(DisplayName))) > 0),
            CONSTRAINT CK_core_Office_TimeZoneName
                CHECK (LEN(LTRIM(RTRIM(TimeZoneName))) > 0),
            CONSTRAINT CK_core_Office_Capacity
                CHECK (Capacity > 0)
        );
    END;

    IF OBJECT_ID(N'core.Department', N'U') IS NULL
    BEGIN
        CREATE TABLE core.Department
        (
            DepartmentId int IDENTITY(1, 1) NOT NULL,
            DepartmentCode varchar(20) NOT NULL,
            DepartmentName nvarchar(100) NOT NULL,
            IsActive bit NOT NULL
                CONSTRAINT DF_core_Department_IsActive DEFAULT (1),
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_Department_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_Department
                PRIMARY KEY CLUSTERED (DepartmentId),
            CONSTRAINT UQ_core_Department_DepartmentCode
                UNIQUE (DepartmentCode),
            CONSTRAINT CK_core_Department_DepartmentCode
                CHECK
                (
                    LEN(DepartmentCode) > 0
                    AND DepartmentCode COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z0-9-]%'
                    AND DepartmentCode COLLATE Latin1_General_100_BIN2
                        LIKE '%[A-Z0-9]%'
                ),
            CONSTRAINT CK_core_Department_DepartmentName
                CHECK (LEN(LTRIM(RTRIM(DepartmentName))) > 0)
        );
    END;

    IF OBJECT_ID(N'core.Person', N'U') IS NULL
    BEGIN
        CREATE TABLE core.Person
        (
            PersonId int IDENTITY(1, 1) NOT NULL,
            PersonnelCode varchar(20) NOT NULL,
            DisplayName nvarchar(100) NOT NULL,
            SyntheticEmail varchar(254) NOT NULL,
            DepartmentId int NOT NULL,
            ValidFrom date NOT NULL,
            ValidTo date NULL,
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_Person_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_Person
                PRIMARY KEY CLUSTERED (PersonId),
            CONSTRAINT UQ_core_Person_PersonnelCode
                UNIQUE (PersonnelCode),
            CONSTRAINT UQ_core_Person_SyntheticEmail
                UNIQUE (SyntheticEmail),
            CONSTRAINT FK_core_Person_Department
                FOREIGN KEY (DepartmentId)
                REFERENCES core.Department (DepartmentId),
            CONSTRAINT CK_core_Person_PersonnelCode
                CHECK
                (
                    LEN(PersonnelCode) > 0
                    AND PersonnelCode COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z0-9-]%'
                    AND PersonnelCode COLLATE Latin1_General_100_BIN2
                        LIKE '%[A-Z0-9]%'
                ),
            CONSTRAINT CK_core_Person_DisplayName
                CHECK (LEN(LTRIM(RTRIM(DisplayName))) > 0),
            CONSTRAINT CK_core_Person_SyntheticEmail
                CHECK
                (
                    SyntheticEmail LIKE '_%@attendance-lab.example'
                    AND SyntheticEmail NOT LIKE '% %'
                    AND LEN(SyntheticEmail) - LEN(REPLACE(SyntheticEmail, '@', '')) = 1
                ),
            CONSTRAINT CK_core_Person_Validity
                CHECK (ValidTo IS NULL OR ValidTo > ValidFrom)
        );
    END;

    IF OBJECT_ID(N'core.Device', N'U') IS NULL
    BEGIN
        CREATE TABLE core.Device
        (
            DeviceId int IDENTITY(1, 1) NOT NULL,
            DeviceToken varchar(12) NOT NULL,
            DeviceStatus varchar(10) NOT NULL
                CONSTRAINT DF_core_Device_DeviceStatus DEFAULT ('ACTIVE'),
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_Device_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_Device
                PRIMARY KEY CLUSTERED (DeviceId),
            CONSTRAINT UQ_core_Device_DeviceToken
                UNIQUE (DeviceToken),
            CONSTRAINT CK_core_Device_DeviceToken
                CHECK
                (
                    LEN(DeviceToken) = 12
                    AND DeviceToken COLLATE Latin1_General_100_BIN2 LIKE
                        'DEV-[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'
                ),
            CONSTRAINT CK_core_Device_DeviceStatus
                CHECK (DeviceStatus IN ('ACTIVE', 'RETIRED'))
        );
    END;

    IF OBJECT_ID(N'core.PersonDeviceAssignment', N'U') IS NULL
    BEGIN
        CREATE TABLE core.PersonDeviceAssignment
        (
            PersonDeviceAssignmentId bigint IDENTITY(1, 1) NOT NULL,
            PersonId int NOT NULL,
            DeviceId int NOT NULL,
            ValidFrom datetime2(3) NOT NULL,
            ValidTo datetime2(3) NULL,
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_PersonDeviceAssignment_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_PersonDeviceAssignment
                PRIMARY KEY CLUSTERED (PersonDeviceAssignmentId),
            CONSTRAINT FK_core_PersonDeviceAssignment_Person
                FOREIGN KEY (PersonId)
                REFERENCES core.Person (PersonId),
            CONSTRAINT FK_core_PersonDeviceAssignment_Device
                FOREIGN KEY (DeviceId)
                REFERENCES core.Device (DeviceId),
            CONSTRAINT CK_core_PersonDeviceAssignment_Validity
                CHECK (ValidTo IS NULL OR ValidTo > ValidFrom)
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
          AND name = N'UX_core_PersonDeviceAssignment_Device_ValidFrom'
    )
    BEGIN
        CREATE UNIQUE INDEX UX_core_PersonDeviceAssignment_Device_ValidFrom
            ON core.PersonDeviceAssignment (DeviceId, ValidFrom)
            INCLUDE (ValidTo, PersonId);
    END;

    IF OBJECT_ID(N'core.AccessPoint', N'U') IS NULL
    BEGIN
        CREATE TABLE core.AccessPoint
        (
            AccessPointId int IDENTITY(1, 1) NOT NULL,
            OfficeId int NOT NULL,
            AccessPointCode varchar(30) NOT NULL,
            AccessPointType varchar(20) NOT NULL,
            DisplayLabel nvarchar(100) NOT NULL,
            IsActive bit NOT NULL
                CONSTRAINT DF_core_AccessPoint_IsActive DEFAULT (1),
            CreatedAt datetime2(3) NOT NULL
                CONSTRAINT DF_core_AccessPoint_CreatedAt DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_core_AccessPoint
                PRIMARY KEY CLUSTERED (AccessPointId),
            CONSTRAINT UQ_core_AccessPoint_AccessPointCode
                UNIQUE (AccessPointCode),
            CONSTRAINT FK_core_AccessPoint_Office
                FOREIGN KEY (OfficeId)
                REFERENCES core.Office (OfficeId),
            CONSTRAINT CK_core_AccessPoint_AccessPointCode
                CHECK
                (
                    LEN(AccessPointCode) > 0
                    AND AccessPointCode COLLATE Latin1_General_100_BIN2
                        NOT LIKE '%[^A-Z0-9-]%'
                    AND AccessPointCode COLLATE Latin1_General_100_BIN2
                        LIKE '%[A-Z0-9]%'
                ),
            CONSTRAINT CK_core_AccessPoint_AccessPointType
                CHECK (AccessPointType IN ('CARD_READER', 'WIFI_AP')),
            CONSTRAINT CK_core_AccessPoint_DisplayLabel
                CHECK (LEN(LTRIM(RTRIM(DisplayLabel))) > 0)
        );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    THROW;
END CATCH;
