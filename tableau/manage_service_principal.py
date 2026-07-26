#!/usr/bin/env python3
"""Guarded temporary service-principal lifecycle for the private Tableau proof."""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Tuple


TABLEAU_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TABLEAU_DIR.parent
LOADER_DIR = PROJECT_ROOT / "loader"
REPORTING_DIR = PROJECT_ROOT / "reporting"
for directory in (TABLEAU_DIR, LOADER_DIR, REPORTING_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from loader_common import (  # noqa: E402
    DEFAULT_DATA_DIR,
    SafeLoaderError,
    acquire_azure_sql_token,
    build_dataset_plan,
    connect_ready_target,
    runtime_target,
    safe_main_error,
)
from reporting_common import build_reporting_expectations  # noqa: E402
from service_principal_common import (  # noqa: E402
    APPROVED_POLICY,
    CREDENTIAL_NAME,
    IDENTITY_NAME,
    bind_database_user,
    connect_service_principal,
    unbind_database_user,
    validate_policy,
    verify_database_preflight,
    verify_service_principal_boundary,
)


CREATE_CONFIRMATION = "CREATE TEMPORARY TABLEAU IDENTITY"
CLEANUP_CONFIRMATION = "DELETE TEMPORARY TABLEAU IDENTITY"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SafeLoaderError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute-preflight", action="store_true")
    modes.add_argument("--execute-create-bind-verify", action="store_true")
    modes.add_argument("--execute-cleanup", action="store_true")
    return parser.parse_args()


def _azure_cli() -> str:
    executable = shutil.which("az")
    require(executable is not None, "Azure CLI is unavailable.")
    return executable


def _run_az_json(arguments: Sequence[str]) -> object:
    command = [_azure_cli(), *arguments, "--only-show-errors", "--output", "json"]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=90
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SafeLoaderError("A private Entra operation could not be started.") from exc
    require(completed.returncode == 0, "A private Entra operation failed safely.")
    try:
        return json.loads(completed.stdout or "null")
    except json.JSONDecodeError as exc:
        raise SafeLoaderError("Azure CLI returned an unusable private response.") from exc


def _run_az_none(arguments: Sequence[str]) -> None:
    command = [_azure_cli(), *arguments, "--only-show-errors", "--output", "none"]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=90
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SafeLoaderError("A private Entra cleanup could not be started.") from exc
    require(completed.returncode == 0, "A private Entra cleanup failed safely.")


def _inventory() -> Tuple[list, list]:
    applications = _run_az_json(["ad", "app", "list", "--display-name", IDENTITY_NAME])
    principals = _run_az_json(["ad", "sp", "list", "--display-name", IDENTITY_NAME])
    require(isinstance(applications, list) and isinstance(principals, list), "Identity inventory is invalid.")
    return applications, principals


def _private_identifier(record: dict, key: str, label: str) -> str:
    value = record.get(key)
    require(isinstance(value, str) and bool(value), f"{label} identifier is unavailable.")
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise SafeLoaderError(f"{label} identifier format is invalid.") from exc
    return value


def verify_directory_preflight() -> None:
    applications, principals = _inventory()
    require(len(applications) == 0 and len(principals) == 0, "Temporary identity inventory is not clean.")


def create_identity() -> Tuple[str, str, str]:
    application = _run_az_json(
        ["ad", "app", "create", "--display-name", IDENTITY_NAME, "--sign-in-audience", "AzureADMyOrg"]
    )
    require(isinstance(application, dict), "Application creation response is invalid.")
    object_id = _private_identifier(application, "id", "Application object")
    client_id = _private_identifier(application, "appId", "Application client")
    require(application.get("displayName") == IDENTITY_NAME, "Application name is not exact.")
    require(application.get("signInAudience") == "AzureADMyOrg", "Application is not single-tenant.")
    require(application.get("requiredResourceAccess") == [], "Application API-permission inventory is not exact.")
    principal = _run_az_json(["ad", "sp", "create", "--id", client_id])
    require(isinstance(principal, dict), "Service-principal creation response is invalid.")
    principal_id = _private_identifier(principal, "id", "Service principal")
    require(principal.get("appId") == client_id, "Service principal does not match its application.")
    require(principal.get("displayName") == IDENTITY_NAME, "Service-principal name is not exact.")

    assignments = _run_az_json(
        ["role", "assignment", "list", "--assignee-object-id", principal_id, "--all"]
    )
    require(assignments == [], "Temporary service principal has an Azure RBAC assignment.")
    directory_response = _run_az_json(
        [
            "rest", "--method", "get", "--url",
            "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments"
            f"?$filter=principalId%20eq%20'{principal_id}'",
        ]
    )
    require(
        isinstance(directory_response, dict) and directory_response.get("value") == [],
        "Temporary service principal has a directory-role assignment.",
    )
    expiry = datetime.now(timezone.utc) + timedelta(minutes=APPROVED_POLICY.secret_lifetime_minutes)
    credential = _run_az_json(
        [
            "ad", "app", "credential", "reset", "--id", object_id, "--append",
            "--display-name", CREDENTIAL_NAME, "--end-date", expiry.isoformat(timespec="seconds"),
        ]
    )
    require(isinstance(credential, dict), "Credential creation response is invalid.")
    require(credential.get("appId") == client_id, "Credential does not match the application.")
    secret = credential.get("password")
    require(
        isinstance(secret, str) and 16 <= len(secret) <= 64 and "\x00" not in secret and "\n" not in secret,
        "Credential creation returned no usable secret.",
    )
    metadata = _run_az_json(["ad", "app", "credential", "list", "--id", object_id])
    require(isinstance(metadata, list) and len(metadata) == 1, "Credential inventory is not exact.")
    require(metadata[0].get("displayName") == CREDENTIAL_NAME, "Credential label is not exact.")
    try:
        observed_expiry = datetime.fromisoformat(
            str(metadata[0]["endDateTime"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SafeLoaderError("Credential expiry metadata is unusable.") from exc
    require(observed_expiry.tzinfo is not None, "Credential expiry has no UTC offset.")
    require(
        abs((observed_expiry - expiry).total_seconds()) <= 120,
        "Credential expiry is outside the one-hour boundary.",
    )
    return object_id, client_id, secret


def delete_identity(object_id: str) -> None:
    _run_az_none(["ad", "app", "delete", "--id", object_id])


def cleanup_directory_identity() -> None:
    applications, principals = _inventory()
    require(len(applications) <= 1 and len(principals) <= 1, "Temporary identity cleanup inventory is ambiguous.")
    if applications:
        delete_identity(_private_identifier(applications[0], "id", "Temporary application"))
    for _ in range(5):
        applications, principals = _inventory()
        if not applications and not principals:
            return
        time.sleep(2)
    if not applications and len(principals) == 1:
        principal_id = _private_identifier(principals[0], "id", "Temporary service principal")
        _run_az_none(["ad", "sp", "delete", "--id", principal_id])
    applications, principals = _inventory()
    require(not applications and not principals, "Temporary identity directory cleanup did not complete.")


def _confirmation(expected: str) -> None:
    try:
        actual = getpass.getpass(f"Privately enter '{expected}' to continue: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SafeLoaderError("Private lifecycle confirmation was not completed.") from exc
    require(actual == expected, "Private lifecycle confirmation did not match.")


def _clipboard(value: str) -> None:
    executable = shutil.which("pbcopy")
    require(executable is not None, "The private clipboard helper is unavailable.")
    completed = subprocess.run(
        [executable], input=value, text=True, capture_output=True, check=False, timeout=10
    )
    require(completed.returncode == 0, "Private clipboard handoff failed safely.")


def clear_clipboard() -> None:
    executable = shutil.which("pbcopy")
    if executable is None:
        return
    try:
        subprocess.run(
            [executable], input="", text=True, capture_output=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        pass


HANDOFF_MAX_COPIES = 3


def _handoff_value(value: str, value_label: str) -> None:
    for copy_number in range(1, HANDOFF_MAX_COPIES + 1):
        _clipboard(value)
        timer = threading.Timer(APPROVED_POLICY.clipboard_max_seconds, clear_clipboard)
        timer.daemon = True
        timer.start()
        try:
            response = input(
                f"Paste the {value_label} without saving it, then enter PASTED or COPY AGAIN; "
                "the clipboard auto-clears within 60 seconds: "
            ).strip().upper()
        finally:
            timer.cancel()
            clear_clipboard()
        if response == "PASTED":
            return
        if response != "COPY AGAIN":
            raise SafeLoaderError("Private Tableau clipboard response was not recognized.")
        if copy_number == HANDOFF_MAX_COPIES:
            raise SafeLoaderError("Private Tableau clipboard copy limit was reached.")


def handoff_to_tableau(client_id: str, secret: str) -> None:
    try:
        input("Press Enter to copy the private client identifier for Tableau: ")
        _handoff_value(client_id, "client identifier")
        input("Press Enter to copy the one-hour client secret for Tableau: ")
        _handoff_value(secret, "client secret")
    except (EOFError, KeyboardInterrupt) as exc:
        raise SafeLoaderError("Private Tableau credential handoff was not completed.") from exc
    finally:
        clear_clipboard()


def _admin_connection():
    server, database = runtime_target()
    token = acquire_azure_sql_token()
    return server, database, connect_ready_target(server, database, token)


def execute_preflight() -> None:
    verify_directory_preflight()
    _, _, connection = _admin_connection()
    try:
        verify_database_preflight(connection)
    finally:
        connection.close()


def execute_create_bind_verify(data_dir: Path) -> None:
    _confirmation(CREATE_CONFIRMATION)
    plan = build_dataset_plan(data_dir)
    expectations = build_reporting_expectations(plan)
    verify_directory_preflight()
    server, database, admin = _admin_connection()
    database_bound = False
    try:
        verify_database_preflight(admin)
        _application_id, client_id, secret = create_identity()
        bind_database_user(admin)
        database_bound = True
        service_connection = connect_service_principal(server, database, client_id, secret)
        try:
            verify_service_principal_boundary(service_connection, expectations)
        finally:
            service_connection.close()
        handoff_to_tableau(client_id, secret)
        print("Temporary Tableau identity: READY — private SQL boundary passed; cleanup remains mandatory.")
    except Exception as exc:
        cleanup_failed = False
        try:
            cleanup_directory_identity()
        except Exception:
            cleanup_failed = True
        if database_bound:
            try:
                unbind_database_user(admin)
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise SafeLoaderError(
                "Automatic temporary-identity cleanup did not complete; explicit cleanup is required."
            ) from exc
        raise
    finally:
        clear_clipboard()
        admin.close()


def execute_cleanup() -> None:
    _confirmation(CLEANUP_CONFIRMATION)
    cleanup_directory_identity()
    _, _, admin = _admin_connection()
    try:
        unbind_database_user(admin)
    finally:
        admin.close()
    verify_directory_preflight()
    print("Temporary Tableau identity cleanup: PASS ApplicationIdentities=0 ReportRoleMembers=0")


def main() -> int:
    try:
        args = parse_args()
        result = validate_policy()
        if args.execute_preflight:
            execute_preflight()
            print("Temporary Tableau identity preflight: PASS Applications=0 Principals=0 ReportRoleMembers=0")
            return 0
        if args.execute_create_bind_verify:
            execute_create_bind_verify(args.data_dir)
            return 0
        if args.execute_cleanup:
            execute_cleanup()
            return 0
        print(
            "Temporary Tableau identity plan: PASS "
            "SecretMinutes={secret_minutes} ClipboardSeconds={clipboard_seconds} "
            "AzureRoles={azure_roles} DirectoryRoles={directory_roles} "
            "ApiPermissions={api_permissions} DatabaseRoles={database_roles}".format(**result)
        )
        print("Mode: DRY_RUN — no Entra, Azure SQL, clipboard, or Tableau action occurred.")
        return 0
    except Exception as exc:
        clear_clipboard()
        print(f"Temporary Tableau identity: FAIL — {safe_main_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
