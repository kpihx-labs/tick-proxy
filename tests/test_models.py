"""Envelope shape — meta carries the audit trail, data stays pure."""

from tick_proxy.models import OutputMeta, Verification, ok, rejected


def test_meta_has_no_verification_fields():
    assert "verified" not in OutputMeta().model_dump()
    assert "verification" not in OutputMeta().model_dump()


def test_default_meta():
    assert OutputMeta().model_dump() == {
        "status": "ok",
        "comment": "",
        "edited": False,
    }


def test_ok_envelope():
    env = ok({"id": "68f1"})
    assert env["meta"]["status"] == "ok"
    assert "verification" not in env["meta"]
    assert env["data"] == {"id": "68f1"}


def test_verification_lives_in_data_only():
    v = Verification(
        method="GET /task/68f1",
        checked=["parentId"],
        expected={"parentId": "68e0"},
        actual={"parentId": "68e0"},
        ok=True,
    )
    env = ok({"id": "68f1", "verification": v.model_dump()})
    assert "verification" not in env["meta"]
    assert env["data"]["verification"]["ok"] is True


def test_rejected_envelope():
    env = rejected("too risky", edited=True)
    assert env["meta"]["status"] == "rejected"
    assert env["meta"]["edited"] is True
    assert env["data"] is None
