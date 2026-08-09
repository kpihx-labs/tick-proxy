"""
Minimal .env config loader for tick-proxy.

Single source of truth: ~/.config/tick-proxy/.env — no config.yaml, no in-repo
.env, no cache. Endpoint defaults live here as documented constants and every
one of them is overridable from that same .env file.

Password policy (KπX directive): the TickTick password is NEVER stored. The
.env holds at most TICK_API_TOKEN, TICK_SESSION_TOKEN and TICK_USERNAME.
"""

import os
from pathlib import Path

from .exceptions import TickProxyError

CONFIG_DIR = Path.home() / ".config" / "tick-proxy"
ENV_PATH = CONFIG_DIR / ".env"

# ── Environment variable names (single source of truth) ───────────────────────
ENV_API_TOKEN = "TICK_API_TOKEN"
ENV_SESSION_TOKEN = "TICK_SESSION_TOKEN"
ENV_USERNAME = "TICK_USERNAME"
ENV_TOKEN_OBTAINED_AT = "TICK_SESSION_TOKEN_OBTAINED_AT"
ENV_TOKEN_EXPIRES_AT = "TICK_SESSION_TOKEN_EXPIRES_AT"

# ── Endpoint defaults (documented constants, all overridable via .env) ─────────
DEFAULT_V1_BASE_URL = "https://api.ticktick.com/open/v1"
DEFAULT_V2_BASE_URL = "https://api.ticktick.com/api/v2"
DEFAULT_WEB_ORIGIN = "https://ticktick.com"
DEFAULT_TIMEOUT = 15.0

V2_SIGNON_PATH = "/user/signon"
V2_MFA_VERIFY_PATH = "/user/sign/mfa/code/verify"
SIGNON_PARAMS = {"wc": "true", "remember": "true"}
SESSION_COOKIE_NAME = "t"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"
V2_DEVICE_HEADER = (
    '{"platform":"web","os":"Linux x86_64","device":"Firefox 145.0",'
    '"name":"","version":8006,"id":"6790a0b0c1d2e3f4a5b6c7d8","channel":"website",'
    '"campaign":"","websocket":""}'
)


def load_env() -> dict[str, str]:
    """Load the .env file into os.environ and return it as a dict.

    Existing environment variables win (``setdefault``), so an operator can
    override any key for a single run without touching the file.

    Returns:
        dict[str, str]: The key/value pairs found in the file (empty when the
        file does not exist).

    Examples:
        >>> load_env()
        {'TICK_API_TOKEN': '6f8a…a7b', 'TICK_USERNAME': 'kapoivha@gmail.com'}
        >>> load_env()          # when ~/.config/tick-proxy/.env is absent
        {}
    """
    if not ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not val:
            continue
        os.environ.setdefault(key, val)
        result[key] = val
    return result


def write_env(values: dict[str, str]) -> None:
    """Rewrite ~/.config/tick-proxy/.env with the given values (chmod 600).

    Keys whose value is an empty string are omitted, which is how `admin setup`
    clears a credential.

    Args:
        values (dict[str, str]): Full desired content, e.g.
            ``{"TICK_API_TOKEN": "6f8a…", "TICK_USERNAME": "me@example.com"}``.

    Returns:
        None: Writes the file and sets 0600 permissions.

    Examples:
        >>> write_env({"TICK_API_TOKEN": "6f8a1c2e"})   # file now has 1 key
        >>> write_env({})                                # file now empty (all cleared)
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# tick-proxy configuration — managed by `tick-proxy admin setup`.",
        "# The TickTick password is NEVER stored here.",
        "",
    ]
    lines.extend(f"{k}={v}" for k, v in values.items() if v)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)
    CONFIG_DIR.chmod(0o700)


def ensure_env(require_v2: bool = False) -> None:
    """Check that the config exists and exposes the credentials we need.

    Args:
        require_v2 (bool): When True, also require a V2 session token (or a
            username able to refresh one). Used by V2-only actions.

    Returns:
        None: Returns silently when the configuration is usable.

    Raises:
        TickProxyError: With the exact command to run as a fix.

    Examples:
        >>> ensure_env()                  # V1 token present → returns None
        >>> ensure_env(require_v2=True)   # no session token
        TickProxyError: V2 auth missing. Run 'tick-proxy admin session-refresh'.
    """
    if not ENV_PATH.exists():
        raise TickProxyError(
            f"Config file not found at {ENV_PATH}. Run 'tick-proxy admin setup' first."
        )
    load_env()
    if not get_api_token():
        raise TickProxyError(
            f"{ENV_PATH} is missing {ENV_API_TOKEN}. "
            "Run 'tick-proxy admin setup' to configure."
        )
    if require_v2 and not has_v2_auth():
        raise TickProxyError(
            "This action requires V2 access. Provide "
            f"{ENV_SESSION_TOKEN} (or {ENV_USERNAME} then run "
            "'tick-proxy admin session-refresh')."
        )


def get_api_token() -> str:
    """Return the V1 Open API token from the environment.

    Returns:
        str: The token, or an empty string when unset.

    Examples:
        >>> get_api_token()
        '6f8a1c2e-4b7d-4e9f-8a1b-2c3d4e5f6a7b'
        >>> get_api_token()     # unset
        ''
    """
    return os.environ.get(ENV_API_TOKEN, "")


def get_session_token() -> str:
    """Return the V2 web session token from the environment.

    Returns:
        str: The session cookie value, or an empty string when unset.

    Examples:
        >>> get_session_token()
        'a1b2c3d4e5f60718293a4b5c6d7e8f90'
        >>> get_session_token()     # unset
        ''
    """
    return os.environ.get(ENV_SESSION_TOKEN, "")


def get_username() -> str:
    """Return the stored TickTick account e-mail (used only to pre-fill HITL).

    Returns:
        str: The e-mail, or an empty string when unset.

    Examples:
        >>> get_username()
        'kapoivha@gmail.com'
        >>> get_username()      # unset
        ''
    """
    return os.environ.get(ENV_USERNAME, "")


def has_v2_auth() -> bool:
    """Whether a V2 call can be attempted right now.

    Returns:
        bool: True when a session token is present. A username alone is not
        enough — refreshing requires the interactive HITL password prompt.

    Examples:
        >>> has_v2_auth()      # session token set
        True
        >>> has_v2_auth()      # only TICK_USERNAME set
        False
    """
    return bool(get_session_token())


def v1_base_url() -> str:
    """V1 Open API base URL (overridable via TICK_API_V1_BASE_URL).

    Returns:
        str: Base URL without trailing slash.

    Examples:
        >>> v1_base_url()
        'https://api.ticktick.com/open/v1'
        >>> v1_base_url()   # with TICK_API_V1_BASE_URL=https://stage.example/open/v1
        'https://stage.example/open/v1'
    """
    return os.environ.get("TICK_API_V1_BASE_URL") or DEFAULT_V1_BASE_URL


def v2_base_url() -> str:
    """V2 web API base URL (overridable via TICK_API_V2_BASE_URL).

    Returns:
        str: Base URL without trailing slash.

    Examples:
        >>> v2_base_url()
        'https://api.ticktick.com/api/v2'
        >>> v2_base_url()   # with TICK_API_V2_BASE_URL=https://stage.example/api/v2
        'https://stage.example/api/v2'
    """
    return os.environ.get("TICK_API_V2_BASE_URL") or DEFAULT_V2_BASE_URL


def web_origin() -> str:
    """Web origin used for Origin/Referer headers on V2 calls.

    Returns:
        str: The origin URL (overridable via TICK_WEB_ORIGIN).

    Examples:
        >>> web_origin()
        'https://ticktick.com'
        >>> web_origin()    # with TICK_WEB_ORIGIN=https://dida365.com
        'https://dida365.com'
    """
    return os.environ.get("TICK_WEB_ORIGIN") or DEFAULT_WEB_ORIGIN


def api_timeout() -> float:
    """HTTP timeout, in seconds, for every TickTick call.

    Returns:
        float: The timeout (overridable via TICK_API_TIMEOUT).

    Examples:
        >>> api_timeout()
        15.0
        >>> api_timeout()   # with TICK_API_TIMEOUT=30
        30.0
    """
    raw = os.environ.get("TICK_API_TIMEOUT")
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT
