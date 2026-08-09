"""
TickTick HTTP transport — V1 Open API (Bearer) and V2 web API (session cookie).

Difference with `tick-mcp`: there is **no credential-based auto-login**. The
password is never stored, so a 401 on V2 cannot be silently repaired — it is
surfaced with the exact command to run (`tick-proxy admin session-refresh`),
which collects the password transiently through the HITL form.
"""

from typing import Any, Literal

import httpx

from ..config import (
    ENV_API_TOKEN,
    ENV_SESSION_TOKEN,
    SESSION_COOKIE_NAME,
    USER_AGENT,
    V2_DEVICE_HEADER,
    api_timeout,
    get_api_token,
    get_session_token,
    has_v2_auth,
    v1_base_url,
    v2_base_url,
    web_origin,
)
from ..exceptions import TickTickAPIError

Method = Literal["get", "post", "put", "delete"]


class Transport:
    """Thin HTTP wrapper around the two TickTick APIs.

    One instance per CLI invocation; it owns a single `httpx.Client` so that
    connection reuse works across the read-back performed by `@always_verify`.

    Examples:
        >>> Transport().v1("get", "/project")[0]["name"]
        '🛠️ Tech & Science'
        >>> Transport().v2("get", "/user/status")["userId"]
        '5f8a1c2e4b7d4e9f8a1b2c3d'
    """

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=api_timeout())

    # ── lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying HTTP client.

        Returns:
            None

        Examples:
            >>> t = Transport(); t.close()
            >>> t.close()          # idempotent
        """
        self._client.close()

    # ── headers ──────────────────────────────────────────────────────────────

    def _v1_headers(self) -> dict[str, str]:
        """Build the V1 Bearer headers.

        Returns:
            dict[str, str]: Authorization + Content-Type.

        Examples:
            >>> Transport()._v1_headers()["Content-Type"]
            'application/json'
            >>> Transport()._v1_headers()["Authorization"].startswith("Bearer ")
            True
        """
        return {
            "Authorization": f"Bearer {get_api_token()}",
            "Content-Type": "application/json",
        }

    def _v2_headers(self) -> dict[str, str]:
        """Build the V2 web headers (session cookie + browser impersonation).

        Returns:
            dict[str, str]: Cookie, Content-Type, User-Agent, X-Device, Origin.

        Examples:
            >>> Transport()._v2_headers()["Cookie"].startswith("t=")
            True
            >>> Transport()._v2_headers()["Origin"]
            'https://ticktick.com'
        """
        return {
            "Cookie": f"{SESSION_COOKIE_NAME}={get_session_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Device": V2_DEVICE_HEADER,
            "Origin": web_origin(),
            "Referer": f"{web_origin()}/",
        }

    # ── error translation ────────────────────────────────────────────────────

    @staticmethod
    def _handle(r: httpx.Response, api: str) -> Any:
        """Translate an HTTP response into data or a clean TickTickAPIError.

        Args:
            r (httpx.Response): The raw response.
            api (str): "v1" or "v2" — selects the right remediation hint.

        Returns:
            Any: Parsed JSON, or `{}` on 204/empty bodies.

        Raises:
            TickTickAPIError: On 401/403/404/429/5xx or any non-2xx status.

        Examples:
            >>> Transport._handle(httpx.Response(204), "v1")
            {}
            >>> Transport._handle(httpx.Response(200, json={"id": "68f1"}), "v1")
            {'id': '68f1'}
        """
        if r.status_code == 401:
            if api == "v1":
                raise TickTickAPIError(
                    401,
                    f"V1 API token expired or invalid ({ENV_API_TOKEN}). "
                    "Fix: run 'tick-proxy admin setup' and paste a fresh token "
                    "from TickTick → Settings → Integrations → API.",
                )
            raise TickTickAPIError(
                401,
                f"V2 session expired ({ENV_SESSION_TOKEN}). "
                "Fix: run 'tick-proxy admin session-refresh' — it asks for your "
                "credentials in the HITL form, saves the new token, and discards "
                "the password (it is never stored).",
            )
        if r.status_code == 403:
            raise TickTickAPIError(
                403, "Forbidden — insufficient permissions for this resource."
            )
        if r.status_code == 404:
            raise TickTickAPIError(404, "Not found — check the ids in your payload.")
        if r.status_code == 429:
            raise TickTickAPIError(
                429,
                "Rate limit exceeded — wait a moment before retrying (no auto-retry).",
            )
        if r.status_code >= 500:
            raise TickTickAPIError(
                r.status_code, f"TickTick server error. Body: {r.text[:200]}"
            )
        if r.status_code >= 400:
            raise TickTickAPIError(r.status_code, r.text[:300] or "Request failed")
        if r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {"raw": r.text}

    # ── public verbs ─────────────────────────────────────────────────────────

    def v1(
        self,
        method: Method,
        endpoint: str,
        *,
        params: dict | None = None,
        payload: dict | list | None = None,
    ) -> Any:
        """Perform a V1 Open API call.

        Args:
            method (Method): get · post · put · delete.
            endpoint (str): Path appended to the V1 base URL, e.g. `/project`.
            params (dict | None): Query string parameters.
            payload (dict | list | None): JSON body for post/put.

        Returns:
            Any: Parsed JSON (`{}` for 204).

        Examples:
            >>> Transport().v1("get", "/project")[0]["id"]
            '6xxxxxxxxxxxxxxxxxxxxxxx'
            >>> Transport().v1("post", "/task", payload={"title": "Buy bread"})["id"]
            '68f1a2b3c4d5e6f708192a3b'
        """
        url = f"{v1_base_url()}{endpoint}"
        kwargs: dict[str, Any] = {"headers": self._v1_headers(), "params": params}
        if method in ("post", "put"):
            kwargs["json"] = payload if payload is not None else {}
        r = getattr(self._client, method)(url, **kwargs)
        return self._handle(r, "v1")

    def v2(
        self,
        method: Method,
        endpoint: str,
        *,
        params: dict | None = None,
        payload: dict | list | None = None,
    ) -> Any:
        """Perform a V2 web API call (requires a session token).

        Args:
            method (Method): get · post · put · delete.
            endpoint (str): Path appended to the V2 base URL, e.g. `/batch/check/0`.
            params (dict | None): Query string parameters.
            payload (dict | list | None): JSON body for post/put.

        Returns:
            Any: Parsed JSON (`{}` for 204).

        Raises:
            TickTickAPIError: 401 when the session token is missing/expired,
                with the `admin session-refresh` hint.

        Examples:
            >>> Transport().v2("get", "/user/status")["pro"]
            True
            >>> Transport().v2("get", "/tags")[0]["name"]
            'revision'
        """
        if not has_v2_auth():
            raise TickTickAPIError(
                401,
                f"V2 access requires {ENV_SESSION_TOKEN}. "
                "Fix: run 'tick-proxy admin session-refresh'.",
            )
        url = f"{v2_base_url()}{endpoint}"
        kwargs: dict[str, Any] = {"headers": self._v2_headers(), "params": params}
        if method in ("post", "put"):
            kwargs["json"] = payload if payload is not None else {}
        r = getattr(self._client, method)(url, **kwargs)
        return self._handle(r, "v2")
