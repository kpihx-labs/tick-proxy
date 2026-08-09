"""
Admin logic — setup, status, session-refresh. Single source of truth.

The TickTick password is NEVER stored: `session-refresh` collects it through the
HITL form, exchanges it for a session token, then drops it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import (
    ENV_API_TOKEN,
    ENV_PATH,
    ENV_SESSION_TOKEN,
    ENV_TOKEN_EXPIRES_AT,
    ENV_TOKEN_OBTAINED_AT,
    ENV_USERNAME,
    SESSION_COOKIE_NAME,
    SIGNON_PARAMS,
    USER_AGENT,
    V2_MFA_VERIFY_PATH,
    V2_SIGNON_PATH,
    api_timeout,
    get_api_token,
    get_session_token,
    get_username,
    load_env,
    v1_base_url,
    v2_base_url,
    web_origin,
    write_env,
)
from .exceptions import TickProxyError
from .hitl import request_approval


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


def setup() -> dict:
    """Collect the three persisted credentials through the HITL web form.

    The form shows API token, session token and username. A field left empty is
    cleared; a field left untouched keeps its current value. No password field
    exists here.

    Returns:
        dict: `{"config": …, "fields": [...]}` describing what was written.

    Raises:
        TickProxyError: When the reviewer rejects the form.

    Examples:
        >>> setup()
        {'config': '/home/kpihx/.config/tick-proxy/.env', 'fields': ['TICK_API_TOKEN']}
        >>> setup()
        {'config': '/home/kpihx/.config/tick-proxy/.env', 'fields': ['TICK_API_TOKEN', 'TICK_USERNAME']}
    """
    load_env()
    current = {
        ENV_API_TOKEN: get_api_token(),
        ENV_SESSION_TOKEN: get_session_token(),
        ENV_USERNAME: get_username(),
    }
    response = request_approval("admin setup", current)
    if response.status == "rejected":
        raise TickProxyError(f"Setup rejected: {response.comment or 'no reason given'}")

    values = response.payload if isinstance(response.payload, dict) else current
    kept = {k: str(v).strip() for k, v in values.items() if str(v or "").strip()}
    if not kept.get(ENV_API_TOKEN):
        raise TickProxyError(f"{ENV_API_TOKEN} is required — setup aborted.")
    write_env(kept)
    return {"config": str(ENV_PATH), "fields": sorted(kept)}


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
                        "User-Agent": USER_AGENT,
                    },
                )
            v2_reachable = r.status_code == 200
        except httpx.HTTPError:
            v2_reachable = False

    import os

    return {
        "config": str(ENV_PATH),
        "config_exists": ENV_PATH.exists(),
        "v1_token": _mask(api_token),
        "v1_reachable": v1_reachable,
        "v2_token": _mask(session_token),
        "v2_reachable": v2_reachable,
        "username": get_username(),
        "token_obtained_at": os.environ.get(ENV_TOKEN_OBTAINED_AT, ""),
        "token_expires_at": os.environ.get(ENV_TOKEN_EXPIRES_AT, ""),
    }


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
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Origin": web_origin(),
                "Referer": f"{web_origin()}/",
            },
        )
    if r.status_code != 200:
        raise TickProxyError(f"V2 login failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def session_refresh() -> dict:
    """Obtain a fresh V2 session token — credentials collected transiently.

    Opens the HITL form asking for username (pre-filled from TICK_USERNAME) and
    password. The password is used for exactly one `POST /user/signon` and is
    then dropped; only the resulting token and the e-mail are written to .env.
    A device/2FA challenge triggers a second HITL step asking for the code.

    Returns:
        dict: `{"session_token": "<masked>", "expires_at": …, "username": …}`.

    Raises:
        TickProxyError: When the form is rejected or TickTick refuses the login.

    Examples:
        >>> session_refresh()
        {'session_token': 'a1b2…e8f90', 'obtained_at': '2026-08-09T11:24:03Z', 'expires_at': '2026-09-08T11:24:03Z'}
        >>> session_refresh()
        {'session_token': 'c3d4…a1b2c', 'obtained_at': '2026-08-09T12:00:00Z', 'expires_at': '2026-09-08T12:00:00Z'}
    """
    load_env()
    form = {"username": get_username(), "password": ""}
    response = request_approval("admin session-refresh", form)
    if response.status == "rejected":
        raise TickProxyError(
            f"Session refresh rejected: {response.comment or 'no reason given'}"
        )
    values = response.payload if isinstance(response.payload, dict) else form
    username = str(values.get("username") or "").strip()
    password = str(values.get("password") or "")
    if not username or not password:
        raise TickProxyError("Username and password are both required to sign in.")

    data = _signon(username, password)
    token = data.get("token")

    if not token and data.get("authId"):
        code_form = {"authId": data["authId"], "code": ""}
        code_response = request_approval("admin session-refresh (2FA code)", code_form)
        if code_response.status == "rejected":
            raise TickProxyError("2FA verification rejected.")
        code_values = (
            code_response.payload if isinstance(code_response.payload, dict) else {}
        )
        with httpx.Client(timeout=api_timeout()) as c:
            r = c.post(
                f"{v2_base_url()}{V2_MFA_VERIFY_PATH}",
                json={
                    "authId": data["authId"],
                    "code": str(code_values.get("code") or "").strip(),
                },
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
        if r.status_code != 200:
            raise TickProxyError(f"2FA verification failed: {r.text[:200]}")
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
        ENV_USERNAME: username,
        ENV_TOKEN_OBTAINED_AT: now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ENV_TOKEN_EXPIRES_AT: expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_env(values_to_write)
    return {
        "session_token": _mask(str(token)),
        "obtained_at": values_to_write[ENV_TOKEN_OBTAINED_AT],
        "expires_at": values_to_write[ENV_TOKEN_EXPIRES_AT],
        "username": username,
    }
