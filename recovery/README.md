# Recovery Tooling

This directory contains the offline-default implementation for the completed point-in-time
recovery exercise. Nothing here creates a restore database. The live exercise used the
portal for the one reviewed restore and retained CLI and SQL clients as explicit guarded
verification and cleanup surfaces.

## Safe local checks

```bash
.venv/bin/python recovery/manage_recovery_marker.py
.venv/bin/python recovery/validate_restore_point.py
.venv/bin/python recovery/verify_recovery.py
.venv/bin/python -m unittest recovery/test_recovery.py
```

The default marker command checks its guarded create/drop SQL without acquiring a token.
The default verifier independently loads the generator oracle, reporting oracle, Basic
target policy, two 60-minute boundaries, and cost ceiling. Neither default accesses Azure
CLI or Azure SQL.

## Future explicit live boundaries

Live modes require the private environment variables `ATTENDANCE_SQL_SERVER` and
`ATTENDANCE_SQL_SOURCE_DATABASE`; restored-target verification additionally requires
`ATTENDANCE_SQL_RESTORE_DATABASE`. Each live command also privately prompts for the
corresponding database name. The response is not echoed, placed in command history, or
printed by the clients.

The verified live sequence is:

1. Run `verify_recovery.py --execute-source-preflight`.
2. Run `manage_recovery_marker.py --execute-create-marker`. It captures a private UTC
   restore point, waits at least five seconds, and commits exactly one fictional marker only
   after fresh canonical, reporting, structure, encryption, role, and fixture checks pass.
3. Allow at least one normal transaction-log-backup interval.
4. On the portal **Review + create** page, privately copy the complete displayed UTC value
   and run `validate_restore_point.py --execute-compare`. Do not submit unless it reports
   `ExactSecondMatch=1`; merely seeing a restore-point field is not sufficient. Submit the
   one reviewed Basic PITR only after that exact comparison.
5. Inspect the target database-level audit policy and leave it absent or disabled.
6. Run `verify_recovery.py --execute-restored-target --confirm-target-audit-safe`.
7. Delete the temporary target and prove it is absent before running
   `manage_recovery_marker.py --execute-remove-marker --confirm-restore-deleted` against
   the source. That confirmation is valid only after the control-plane inventory proves
   the temporary target is absent.
8. Rerun the complete source verifier after cleanup.

Exact runtime names, server endpoints, restore timestamps, deployment identifiers, tokens,
connection strings, raw errors, and provider messages remain private and must not be added
to public files. The restore timestamp is printed only for immediate private entry into the
portal and must not be committed.

The first live attempt completed its Basic restore but failed the independent marker check:
the restored database contained the post-restore-point marker. A focused deployment-parameter
inspection proved that the portal submitted its later default minute rather than the
captured pre-marker second. The target was deleted immediately, the source marker was
removed, all source and monitoring regressions passed, and cost and forecast remained zero.

The attempt is recorded as incomplete, not successful. The exact-second comparator above
was the focused correction. The second attempt completed successfully.
The portal time input displayed the intended second before its internal submitted value
changed; explicitly committing the time field with Enter updated the Review value. The
comparator then returned `ExactSecondMatch=1`, after which the Basic restore, independent
oracle, deletion-first cleanup, final regressions, and cost review all passed.
