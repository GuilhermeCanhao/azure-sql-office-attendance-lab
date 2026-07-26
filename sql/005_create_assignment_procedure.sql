/*
    Azure SQL Dual-Signal Office Attendance Analytics Lab
    Script: 005_create_assignment_procedure.sql
    Purpose: Create the controlled, concurrency-safe device-assignment procedure.

    Assignment periods are half-open intervals: [ValidFrom, ValidTo).
    Therefore, adjacent assignments are valid, but overlapping assignments for
    the same device are rejected. NULL ValidTo represents an open-ended period.
*/

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE core.usp_AssignDevice
    @PersonId int,
    @DeviceId int,
    @ValidFrom datetime2(3),
    @ValidTo datetime2(3) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @InitialTranCount int = @@TRANCOUNT;
    DECLARE @PersonValidFrom date;
    DECLARE @PersonValidTo date;
    DECLARE @DeviceStatus varchar(10);
    DECLARE @PersonValidFromBoundary datetime2(3);
    DECLARE @PersonValidToBoundary datetime2(3);
    DECLARE @PersonDeviceAssignmentId bigint;
    DECLARE @Infinity datetime2(3) = CONVERT(datetime2(3), '9999-12-31 23:59:59.999');

    IF @ValidTo IS NOT NULL AND @ValidTo <= @ValidFrom
        THROW 51100, 'ValidTo must be later than ValidFrom.', 1;

    BEGIN TRY
        IF @InitialTranCount = 0
            BEGIN TRANSACTION;
        ELSE
            SAVE TRANSACTION AssignDeviceSavepoint;

        /*
            Lock the reference rows in a consistent order before validating the
            assignment range. This prevents their relevant state from changing
            during the operation.
        */
        SELECT
            @PersonValidFrom = person.ValidFrom,
            @PersonValidTo = person.ValidTo
        FROM core.Person AS person WITH (UPDLOCK, HOLDLOCK)
        WHERE person.PersonId = @PersonId;

        IF @PersonValidFrom IS NULL
            THROW 51101, 'The specified person does not exist.', 1;

        SET @PersonValidFromBoundary = CONVERT(datetime2(3), @PersonValidFrom);
        SET @PersonValidToBoundary = CONVERT(datetime2(3), @PersonValidTo);

        IF @ValidFrom < @PersonValidFromBoundary
           OR
           (
               @PersonValidTo IS NOT NULL
               AND
               (
                   @ValidFrom >= @PersonValidToBoundary
                   OR @ValidTo IS NULL
                   OR @ValidTo > @PersonValidToBoundary
               )
           )
            THROW 51102, 'The assignment must remain within the person validity period.', 1;

        SELECT @DeviceStatus = device.DeviceStatus
        FROM core.Device AS device WITH (UPDLOCK, HOLDLOCK)
        WHERE device.DeviceId = @DeviceId;

        IF @DeviceStatus IS NULL
            THROW 51103, 'The specified device does not exist.', 1;

        IF @DeviceStatus <> 'ACTIVE'
            THROW 51104, 'Only an active device can receive a new assignment.', 1;

        /*
            HOLDLOCK applies serializable semantics. Together with UPDLOCK and
            the DeviceId/ValidFrom index, this protects the searched key range
            so concurrent sessions cannot both pass the overlap check.
        */
        IF EXISTS
        (
            SELECT 1
            FROM core.PersonDeviceAssignment WITH
            (
                UPDLOCK,
                HOLDLOCK,
                INDEX(UX_core_PersonDeviceAssignment_Device_ValidFrom)
            )
            WHERE DeviceId = @DeviceId
              AND ValidFrom < COALESCE(@ValidTo, @Infinity)
              AND @ValidFrom < COALESCE(ValidTo, @Infinity)
        )
            THROW 51105, 'The device already has an overlapping assignment.', 1;

        INSERT INTO core.PersonDeviceAssignment
        (
            PersonId,
            DeviceId,
            ValidFrom,
            ValidTo
        )
        VALUES
        (
            @PersonId,
            @DeviceId,
            @ValidFrom,
            @ValidTo
        );

        SET @PersonDeviceAssignmentId = CONVERT(bigint, SCOPE_IDENTITY());

        IF @InitialTranCount = 0
            COMMIT TRANSACTION;

        SELECT
            @PersonDeviceAssignmentId AS PersonDeviceAssignmentId,
            @PersonId AS PersonId,
            @DeviceId AS DeviceId,
            @ValidFrom AS ValidFrom,
            @ValidTo AS ValidTo;
    END TRY
    BEGIN CATCH
        IF @InitialTranCount = 0 AND XACT_STATE() <> 0
            ROLLBACK TRANSACTION;
        ELSE IF @InitialTranCount > 0 AND XACT_STATE() = 1
            ROLLBACK TRANSACTION AssignDeviceSavepoint;

        /*
            If an ambient transaction is uncommittable, its owner must perform
            the full rollback. The original error is preserved for diagnosis.
        */
        THROW;
    END CATCH;
END;
GO
