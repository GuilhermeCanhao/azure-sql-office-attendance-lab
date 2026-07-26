/*
    Phase 7 controlled audit probe.

    This suite generates principal, role-membership, object, and permission
    change activity inside one transaction, rolls it all back, and then proves
    that no fixture survived. Raw audit records remain private in Azure Storage.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @UserName sysname = N'tst_phase7_audit';
DECLARE @ViewName nvarchar(256) = N'report.vw_Phase7AuditProbe';
DECLARE @Rollback varchar(10) = 'FAIL';

IF DATABASE_PRINCIPAL_ID(@UserName) IS NOT NULL
   OR OBJECT_ID(@ViewName, N'V') IS NOT NULL
    THROW 52100, 'A Phase 7 audit fixture already exists.', 1;

IF HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL') <> 1
    THROW 52101, 'The audit-probe caller lacks administrator control.', 1;

BEGIN TRANSACTION;

BEGIN TRY
    EXEC(N'CREATE USER tst_phase7_audit WITHOUT LOGIN;');
    EXEC(N'CREATE VIEW report.vw_Phase7AuditProbe AS SELECT CAST(1 AS int) AS ProbeValue;');
    ALTER ROLE report_reader ADD MEMBER tst_phase7_audit;
    GRANT CONNECT TO tst_phase7_audit;
    DENY CREATE TABLE TO tst_phase7_audit;

    ROLLBACK TRANSACTION;
    SET @Rollback = 'PASS';
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF DATABASE_PRINCIPAL_ID(@UserName) IS NOT NULL
   OR OBJECT_ID(@ViewName, N'V') IS NOT NULL
   OR IS_ROLEMEMBER(N'report_reader', @UserName) IS NOT NULL
    THROW 52102, 'A Phase 7 audit fixture survived rollback.', 1;

SELECT
    CAST('audit_probe' AS varchar(20)) AS ComponentName,
    CAST('PASS' AS varchar(10)) AS PrincipalChange,
    CAST('PASS' AS varchar(10)) AS RoleMembershipChange,
    CAST('PASS' AS varchar(10)) AS ObjectChange,
    CAST('PASS' AS varchar(10)) AS PermissionChange,
    @Rollback AS TransactionRollback,
    CAST('PASS' AS varchar(10)) AS FixtureCleanup;
