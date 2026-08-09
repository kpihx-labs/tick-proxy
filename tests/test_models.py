"""Envelope shape — meta carries the audit trail, data stays pure."""

from tick_proxy.models import OutputMeta, Verification, ok, rejected


def test_meta_has_no_verified_field():
    assert "verified" not in OutputMeta().model_dump()


def test_default_meta():
    assert OutputMeta().model_dump() == {
        "status": "ok",
        "comment": "",
        "edited": False,
        "verification": None,
    }


def test_ok_envelope():
    env = ok({"id": "68f1"})
    assert env["meta"]["status"] == "ok"
    assert env["meta"]["verification"] is None
    assert env["data"] == {"id": "68f1"}


def test_ok_envelope_with_verification():
    v = Verification(
        method="GET /task/68f1",
        checked=["parentId"],
        expected={"parentId": "68e0"},
        actual={"parentId": "68e0"},
        ok=True,
    )
    env = ok({"id": "68f1"}, v)
    assert env["meta"]["verification"]["ok"] is True
    assert "verification" not in env["data"]


def test_rejected_envelope():
    env = rejected("too risky", edited=True)
    assert env["meta"]["status"] == "rejected"
    assert env["meta"]["edited"] is True
    assert env["data"] is None
