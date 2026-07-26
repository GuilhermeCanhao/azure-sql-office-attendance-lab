# Temporary Tableau Reporting Identity

This note documents the private Tableau connectivity check. It is separate from the public Tableau workbook.

The public workbook is built from synthetic aggregate extracts. It does not contain an Azure SQL connection or a saved credential. The temporary identity described here was used only to prove that Tableau Desktop could read the reporting layer through a restricted database role.

## Why a temporary identity was used

The existing administrator account was not a useful test of least privilege because it already had administrative authority. For the Tableau check, I needed a separate identity that could read the reporting views and nothing else.

On my macOS Tableau setup, the available Azure SQL authentication paths made a short-lived Microsoft Entra service principal the cleanest option for this lab. It was a test identity, not a recommendation for routine analyst access.

## Boundary

The temporary identity had:

- no Azure RBAC role;
- no directory role;
- no direct object permissions;
- membership only in the database `report_reader` role;
- a short-lived credential used only during the private Tableau connection test.

The check proved that the identity could read the four aggregate `report` views and could not read `stage` or `core`, write to `report`, run loader procedures, or refresh the daily summary.

## Cleanup

After the connection test, the temporary application, credential, database user, and role membership were removed. The final check found zero temporary application identities, zero temporary database users, and zero `report_reader` members.

If a connection attempt failed, the intended repair was not to broaden permissions. The safe path was to clean up the temporary identity, fix the local connector or driver problem, and try again only after the environment was understood.

## Offline tests

`tableau/test_service_principal.py` covers the temporary-identity workflow without connecting to Azure. It checks the policy, guarded SQL, permission boundary, cleanup behavior, and secret-handling assumptions.

The full offline project suite currently includes these tests together with the loader, reporting, performance, monitoring, recovery, and Tableau artifact checks.
