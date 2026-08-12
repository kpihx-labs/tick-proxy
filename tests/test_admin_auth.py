"""V2 admin authentication requests are exact and never expose server bodies."""

from __future__ import annotations

from typing import Self

import httpx
import pytest

from tick_proxy import admin, cli
from tick_proxy.config import SIGNON_PARAMS, V2_DEVICE_HEADER, v2_login_headers
from tick_proxy.exceptions import TickProxyError
from tick_proxy.hitl import HITLResponse


class FakeClient:
    """Capture one synchronous httpx request without opening a network socket."""

    def __init__(self, response: httpx.Response, calls: list[dict]) -> None:
        self.response = response
        self.calls = calls

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_signon_uses_canonical_login_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: FakeClient(httpx.Response(200, json={"token": "test"}), calls),
    )

    assert admin._signon("person@example.test", "password") == {"token": "test"}

    request = calls[0]
    assert request["params"] == SIGNON_PARAMS
    assert request["headers"] == v2_login_headers()
    assert request["headers"]["X-Device"] == V2_DEVICE_HEADER


def test_mfa_request_uses_verify_header_and_canonical_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    responses = iter(
        [
            httpx.Response(200, json={"authId": "auth-id", "expireTime": 300}),
            httpx.Response(200, json={"token": "test"}),
        ]
    )
    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient(next(responses), calls))
    approvals = iter(
        [
            HITLResponse(
                "approved", {"username": "person@example.test", "password": "password"}
            ),
            HITLResponse("approved", {"code": "123456"}),
        ]
    )
    monkeypatch.setattr(admin, "request_approval", lambda *_: next(approvals))
    monkeypatch.setattr(admin, "load_env", dict)
    monkeypatch.setattr(admin, "get_api_token", lambda: "api-token")
    monkeypatch.setattr(admin, "get_email", lambda: "")
    monkeypatch.setattr(admin, "write_env", lambda _: None)

    admin.session_refresh()

    request = calls[1]
    assert request["params"] == SIGNON_PARAMS
    assert request["json"] == {"code": "123456", "method": "app"}
    assert request["headers"] == {**v2_login_headers(), "x-verify-id": "auth-id"}
    assert "authId" not in request["json"]


def test_access_forbidden_error_is_safe_and_actionable() -> None:
    response = httpx.Response(
        403,
        json={"errorCode": "access_forbidden", "errorMessage": "private detail"},
    )

    with pytest.raises(
        TickProxyError, match="Approve the sign-in email/link"
    ) as exc_info:
        raise admin._login_error(response, "V2 login")

    assert "private detail" not in str(exc_info.value)


def test_device_approval_retries_signon_without_exposing_auth_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    approvals: list[dict] = []
    responses = iter(
        [
            httpx.Response(200, json={"authId": "auth-id", "expireTime": 86400}),
            httpx.Response(200, json={"token": "test"}),
        ]
    )
    hitl_responses = iter(
        [
            HITLResponse(
                "approved", {"username": "person@example.test", "password": "password"}
            ),
            HITLResponse("approved", {"confirm": ""}),
        ]
    )
    monkeypatch.setattr(httpx, "Client", lambda **_: FakeClient(next(responses), calls))

    def approve(_action: str, payload: dict) -> HITLResponse:
        approvals.append(payload)
        return next(hitl_responses)

    monkeypatch.setattr(admin, "request_approval", approve)
    monkeypatch.setattr(admin, "load_env", dict)
    monkeypatch.setattr(admin, "get_api_token", lambda: "api-token")
    monkeypatch.setattr(admin, "get_email", lambda: "")
    monkeypatch.setattr(admin, "write_env", lambda _: None)

    admin.session_refresh()

    assert len(calls) == 2
    assert "authId" not in approvals[1]


def test_admin_rejection_uses_the_same_envelope_as_do_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize manager HITL rejection without imposing the `do` env gate.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest replacement helper.

    Returns:
        None: Both paths print the shared rejected envelope and exit one.

    Examples:
        >>> {"status": "rejected"}["status"]
        'rejected'
        >>> None is None
        True
    """
    printed: list[dict] = []
    monkeypatch.setattr(cli, "output_result", lambda result, _: printed.append(result))

    with pytest.raises(Exception) as exit_info:
        cli._run_admin(lambda: (None, "rejected", False, "cancelled"))

    assert getattr(exit_info.value, "exit_code", None) == 1
    assert printed == [
        {"meta": {"status": "rejected", "comment": "cancelled", "edited": False}, "data": None}
    ]
