"""
Admin logic — setup, status, session-refresh. Single source of truth.

The TickTick password is NEVER stored: `session-refresh` collects it through the
HITL form, exchanges it for a session token, then drops it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from .config import (
    CONFIG_DIR,
    DIR_PERMISSIONS,
    ENV_API_TOKEN,
    ENV_EMAIL,
    ENV_PATH,
    ENV_SESSION_TOKEN,
    ENV_TOKEN_EXPIRES_AT,
    ENV_TOKEN_OBTAINED_AT,
    FILE_PERMISSIONS,
    SESSION_COOKIE_NAME,
    SIGNON_PARAMS,
    V2_MFA_VERIFY_PATH,
    V2_SIGNON_PATH,
    api_timeout,
    get_api_token,
    get_email,
    get_session_token,
    load_env,
    v1_base_url,
    v2_base_url,
    v2_login_headers,
    write_env,
)
from .exceptions import TickProxyError
from .hitl import request_approval
from .models import Status

AdminResult = tuple[dict | None, Status, bool, str]


def _mask(value: str) -> str:
    """Mask a secret, keeping only its head and tail.

    Args:
        value (str): The secret to mask.

    Returns:
        str: `6f8a…f6a7b`, or an empty string when there is nothing to mask.

    Examples:
        >>> _mask("6f8a1c2e-4b7d-4e9f-8a1b-2c3d4e5f6a7b")
        '6f8a…f6a7b'
        >>> _mask("")
        ''
    """
    if not value:
        return ""
    return f"{value[:4]}…{value[-5:]}" if len(value) > 12 else "…"


def setup() -> AdminResult:
    """Collect the three persisted credentials through the HITL web form.

    The form shows API token, session token and username. A field left empty is
    cleared; a field left untouched keeps its current value. No password field
    exists here.

    Returns:
        tuple[dict, Status, bool, str]: A tuple containing the written fields dict,
        the HITL response status, a boolean indicating whether it was edited,
        and the HITL reviewer comment.

    Examples:
        >>> setup()[0]['config']
        '~/.config/tick-proxy/.env'
    """
    load_env()
    current = {
        ENV_API_TOKEN: get_api_token(),
        ENV_SESSION_TOKEN: get_session_token(),
        ENV_EMAIL: get_email(),
    }
    response = request_approval("admin setup", current)
    if response.status == "rejected":
        return None, "rejected", response.edited, response.comment

    values = response.payload if isinstance(response.payload, dict) else current
    kept = {k: str(v).strip() for k, v in values.items() if str(v or "").strip()}
    if not kept.get(ENV_API_TOKEN):
        raise TickProxyError(f"{ENV_API_TOKEN} is required — setup aborted.")
    write_env(kept)
    return (
        {"config": str(ENV_PATH), "fields": sorted(kept)},
        cast(Status, response.status),
        response.edited,
        response.comment,
    )


def status() -> dict:
    """Report the auth state: masked secrets, timestamps and live reachability.

    Returns:
        dict: Presence flags, masked values, token timestamps and the result of
        a real V1 and V2 probe.

    Examples:
        >>> status()
        {'config': '…/.env', 'v1_token': '6f8a…f6a7b', 'v1_reachable': True, 'v2_token': 'a1b2…e8f90', 'v2_reachable': True}
        >>> status()
        {'config': '…/.env', 'v1_token': '', 'v1_reachable': False, 'v2_token': '', 'v2_reachable': False}
    """
    load_env()
    api_token, session_token = get_api_token(), get_session_token()

    v1_reachable = False
    if api_token:
        try:
            with httpx.Client(timeout=api_timeout()) as c:
                r = c.get(
                    f"{v1_base_url()}/project",
                    headers={"Authorization": f"Bearer {api_token}"},
                )
            v1_reachable = r.status_code == 200
        except httpx.HTTPError:
            v1_reachable = False

    v2_reachable = False
    if session_token:
        try:
            with httpx.Client(timeout=api_timeout()) as c:
                r = c.get(
                    f"{v2_base_url()}/user/status",
                    headers={
                        "Cookie": f"{SESSION_COOKIE_NAME}={session_token}",
                        **v2_login_headers(),
                    },
                )
            v2_reachable = r.status_code == 200
        except httpx.HTTPError:
            v2_reachable = False

    import os
    import shutil
    import sys

    # Check directory permissions (should be DIR_PERMISSIONS)
    dir_mode = None
    dir_status = "absent"
    dir_fix = None
    if CONFIG_DIR.exists():
        dir_mode = os.stat(CONFIG_DIR).st_mode & 0o777
        if dir_mode == DIR_PERMISSIONS:
            dir_status = "ok"
        else:
            dir_status = "warning"
            dir_fix = f"chmod {oct(DIR_PERMISSIONS)[2:]} {CONFIG_DIR}"

    # Check file permissions (should be FILE_PERMISSIONS)
    file_mode = None
    file_status = "absent"
    file_fix = None
    if ENV_PATH.exists():
        file_mode = os.stat(ENV_PATH).st_mode & 0o777
        if file_mode == FILE_PERMISSIONS:
            file_status = "ok"
        else:
            file_status = "warning"
            file_fix = f"chmod {oct(FILE_PERMISSIONS)[2:]} {ENV_PATH}"

    binary_path = shutil.which("tick-proxy") or os.path.abspath(sys.argv[0])

    return {
        "config": str(ENV_PATH),
        "config_exists": ENV_PATH.exists(),
        "v1_token": _mask(api_token),
        "v1_reachable": v1_reachable,
        "v2_token": _mask(session_token),
        "v2_reachable": v2_reachable,
        "username": get_email(),
        "token_obtained_at": os.environ.get(ENV_TOKEN_OBTAINED_AT, ""),
        "token_expires_at": os.environ.get(ENV_TOKEN_EXPIRES_AT, ""),
        "binary": binary_path,
        "permissions": {
            "config_dir": {
                "path": str(CONFIG_DIR),
                "mode": oct(dir_mode) if dir_mode is not None else None,
                "status": dir_status,
                "fix": dir_fix,
            },
            "config_file": {
                "path": str(ENV_PATH),
                "mode": oct(file_mode) if file_mode is not None else None,
                "status": file_status,
                "fix": file_fix,
            },
        },
    }


def _login_error(response: httpx.Response, action: str) -> TickProxyError:
    """Return a safe, actionable error for a failed V2 login exchange.

    TickTick may return JSON error payloads. Only the documented
    ``access_forbidden`` classification is surfaced; arbitrary server text is
    intentionally never echoed because it can contain account-specific data.
    """
    error_code = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error_code = str(body.get("errorCode") or body.get("code") or "").lower()
    except (ValueError, TypeError):
        pass
    if error_code == "access_forbidden":
        return TickProxyError(
            "TickTick denied this login from the current device or network. "
            "Approve the sign-in email/link if one was sent, then retry "
            "'tick-proxy admin session-refresh'."
        )
    return TickProxyError(f"{action} failed ({response.status_code}). Please retry.")


def _signon(username: str, password: str) -> dict[str, Any]:
    """Exchange credentials for a V2 session token.

    Args:
        username (str): The TickTick account e-mail.
        password (str): The account password — used once, never stored.

    Returns:
        dict[str, Any]: The raw signon response (`token`, or `authId` when the
        account requires a device/2FA verification code).

    Raises:
        TickProxyError: When TickTick rejects the credentials.

    Examples:
        >>> _signon("me@example.com", "…")["token"][:6]
        'a1b2c3'
        >>> sorted(_signon("me@example.com", "…"))[:2]
        ['authId', 'username']
    """
    with httpx.Client(timeout=api_timeout()) as c:
        r = c.post(
            f"{v2_base_url()}{V2_SIGNON_PATH}",
            params=SIGNON_PARAMS,
            json={"username": username, "password": password},
            headers=v2_login_headers(),
        )
    if r.status_code != 200:
        raise _login_error(r, "V2 login")
    return r.json()


def _complete_device_approval(
    username: str, password: str
) -> tuple[dict[str, Any] | None, str]:
    """Wait for an email-link device approval, then retry sign-on once.

    Neither credentials nor ``authId`` enter the review payload or persistent
    configuration. The user only acknowledges that they opened TickTick's
    approval link; credentials remain local to this refresh invocation.
    """
    response = request_approval(
        "admin session-refresh (device approval)",
        {
            "instruction": "Open TickTick's sign-in approval email and click its link.",
            "confirm": "",
        },
    )
    if response.status == "rejected":
        return None, response.comment or "Device approval was not confirmed."
    return _signon(username, password), ""


def session_refresh() -> AdminResult:
    """Obtain a fresh V2 session token — credentials collected transiently.

    Opens the HITL form asking for username (pre-filled from TICK_EMAIL) and
    password. The password is used for exactly one `POST /user/signon` and is
    then dropped; only the resulting token and the e-mail are written to .env.
    A device/2FA challenge triggers a second HITL step asking for the code.

    Returns:
        tuple[dict, Status, bool, str]: A tuple containing the session token metadata dict,
        the HITL response status, a boolean indicating whether the payload was edited,
        and the HITL reviewer comment.

    Examples:
        >>> session_refresh()[0]['session_token'][:4]
        'a1b2'
    """
    load_env()
    form = {"username": get_email(), "password": ""}
    response = request_approval("admin session-refresh", form)
    if response.status == "rejected":
        return None, "rejected", response.edited, response.comment
    edited = response.edited
    comment = response.comment
    status = response.status
    values = response.payload if isinstance(response.payload, dict) else form
    username = str(values.get("username") or "").strip()
    password = str(values.get("password") or "")
    if not username or not password:
        raise TickProxyError("Username and password are both required to sign in.")

    data = _signon(username, password)
    token = data.get("token")

    if not token and data.get("authId"):
        expire_time = data.get("expireTime")
        if isinstance(expire_time, (int, float)) and expire_time > 3600:
            data, device_comment = _complete_device_approval(username, password)
            if data is None:
                return None, "rejected", edited, device_comment
            token = data.get("token")

    if not token and data.get("authId"):
        auth_id = str(data["authId"])
        code_form = {"code": ""}
        code_response = request_approval("admin session-refresh (2FA code)", code_form)
        if code_response.status == "rejected":
            return (
                None,
                "rejected",
                edited or code_response.edited,
                code_response.comment,
            )
        edited = edited or code_response.edited
        if code_response.comment:
            comment = (
                f"{comment} | 2FA: {code_response.comment}"
                if comment
                else code_response.comment
            )
        status = code_response.status
        code_values = (
            code_response.payload if isinstance(code_response.payload, dict) else {}
        )
        with httpx.Client(timeout=api_timeout()) as c:
            r = c.post(
                f"{v2_base_url()}{V2_MFA_VERIFY_PATH}",
                params=SIGNON_PARAMS,
                json={
                    "code": str(code_values.get("code") or "").strip(),
                    "method": "app",
                },
                headers={**v2_login_headers(), "x-verify-id": auth_id},
            )
        if r.status_code != 200:
            raise _login_error(r, "2FA verification")
        token = r.json().get("token")

    del password  # the password never outlives this call

    if not token:
        raise TickProxyError(
            f"Login succeeded but no token was returned. Keys: {sorted(data)}"
        )

    now = datetime.now(UTC)
    expires = now + timedelta(days=30)
    values_to_write = {
        ENV_API_TOKEN: get_api_token(),
        ENV_SESSION_TOKEN: str(token),
        ENV_EMAIL: username,
        ENV_TOKEN_OBTAINED_AT: now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ENV_TOKEN_EXPIRES_AT: expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_env(values_to_write)
    return (
        {
            "session_token": _mask(str(token)),
            "obtained_at": values_to_write[ENV_TOKEN_OBTAINED_AT],
            "expires_at": values_to_write[ENV_TOKEN_EXPIRES_AT],
            "username": username,
        },
        cast(Status, status),
        edited,
        comment,
    )


def reset() -> AdminResult:
    """Clear all credentials from the configuration file, leaving only headers.

    Returns:
        tuple[dict, Status, bool, str]: A tuple containing the reset status dict,
        the HITL response status, a boolean indicating whether it was edited,
        and the HITL reviewer comment.

    """
    form = {
        "action": "clear_credentials",
        "config_file": str(ENV_PATH),
        "confirm": "Yes, clear all credentials",
    }
    response = request_approval("admin reset", form)
    if response.status == "rejected":
        return None, "rejected", response.edited, response.comment

    write_env({})
    return (
        {"status": "cleared", "config": str(ENV_PATH)},
        cast(Status, response.status),
        response.edited,
        response.comment,
    )


def purge() -> AdminResult:
    """Delete the configuration directory and uninstall the CLI tool.

    Returns:
        tuple[dict, Status, bool, str]: A tuple containing the purge status dict,
        the HITL response status, a boolean indicating whether it was edited,
        and the HITL reviewer comment.

    """
    form = {
        "action": "delete_config_and_uninstall",
        "config_dir": str(CONFIG_DIR),
        "uninstalled_tool": "tick-proxy",
        "confirm": "Yes, delete config and uninstall the CLI",
    }
    response = request_approval("admin purge", form)
    if response.status == "rejected":
        return None, "rejected", response.edited, response.comment

    import shutil

    config_dir_deleted = False
    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR)
        config_dir_deleted = True

    # Intelligent purge: we do NOT uninstall the tool from within this running
    # process — that would wipe this package's own site-packages mid-execution
    # (rich, typer, ...) and crash before the envelope can be printed. The config
    # is removed here; the operator finishes the uninstall explicitly with:
    #   uv tool uninstall tick-proxy
    return (
        {
            "status": "purged",
            "config_dir": str(CONFIG_DIR),
            "config_dir_deleted": config_dir_deleted,
            "uninstalled": False,
            "note": "Config removed. To fully uninstall the CLI, run: uv tool uninstall tick-proxy",
        },
        cast(Status, response.status),
        response.edited,
        response.comment,
    )
