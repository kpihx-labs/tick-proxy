"""
SHARED types for tick-proxy — the output envelope and common enums.

Per-action payload models live next to their handler in `actions/<domain>.py`
(colocation), so this module stays small and has exactly one responsibility:
describe what every command returns.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "approved", "rejected", "error"]


class Verification(BaseModel):
    """Read-back verification block, produced by the `@always_verify` decorator.

    Lives in `meta.verification`, never in `data`: it describes *how trustworthy
    the result is*, which is audit metadata, not business content.

    Attributes:
        method (str): The read-back performed, e.g. ``GET /open/v1/project/6xxx/task/68f1``.
        checked (list[str]): Field names compared after the write.
        expected (dict): What the caller asked for.
        actual (dict): What the server really holds after the write.
        ok (bool): True when expected == actual on every checked field.

    Examples:
        >>> Verification(method="GET /task/68f1", checked=["parentId"],
        ...              expected={"parentId": "68e0"}, actual={"parentId": "68e0"}, ok=True).ok
        True
        >>> Verification(method="GET /task/68f1", checked=["parentId"],
        ...              expected={"parentId": "68e0"}, actual={"parentId": None}, ok=False).ok
        False
    """

    method: str = Field(..., description="The read-back call performed")
    checked: list[str] = Field(default_factory=list, description="Compared fields")
    expected: dict[str, Any] = Field(default_factory=dict, description="Intended state")
    actual: dict[str, Any] = Field(default_factory=dict, description="Observed state")
    ok: bool = Field(..., description="True when every checked field matches")


class OutputMeta(BaseModel):
    """The `meta` half of every response envelope.

    There is deliberately **no `verified` boolean**: a non-empty `verification`
    object means verified, `None`/empty means not verified.

    Attributes:
        status (Status): ok · approved · rejected · error. `approved`/`rejected`
            only appear when HITL was involved.
        comment (str): The HITL reviewer's comment (empty when none).
        edited (bool): True when the HITL reviewer modified the payload.
        verification (Verification | None): Read-back detail, or None when the
            action carries no `@always_verify` decorator.

    Examples:
        >>> OutputMeta().model_dump()
        {'status': 'ok', 'comment': '', 'edited': False, 'verification': None}
        >>> OutputMeta(status="rejected", comment="too risky").status
        'rejected'
    """

    status: Status = Field(default="ok", description="Result status")
    comment: str = Field(default="", description="HITL reviewer comment")
    edited: bool = Field(default=False, description="HITL reviewer edited the payload")
    verification: Verification | None = Field(
        default=None, description="Read-back verification (meta, never data)"
    )


class Output(BaseModel):
    """The full response envelope printed on stdout.

    Attributes:
        meta (OutputMeta): Audit metadata (status, HITL, verification).
        data (Any): The pure TickTick payload — never mixed with metadata.

    Examples:
        >>> Output(data={"id": "68f1"}).model_dump()["meta"]["status"]
        'ok'
        >>> Output(meta=OutputMeta(status="rejected"), data=None).data is None
        True
    """

    meta: OutputMeta = Field(default_factory=OutputMeta)
    data: Any = Field(default=None)


def ok(data: Any, verification: Verification | None = None) -> dict:
    """Build a successful envelope as a plain dict.

    Args:
        data (Any): The business payload to return.
        verification (Verification | None): Optional read-back proof.

    Returns:
        dict: ``{"meta": {...}, "data": ...}`` ready to print.

    Examples:
        >>> ok({"id": "68f1"})["meta"]["status"]
        'ok'
        >>> ok([], None)["data"]
        []
    """
    return Output(
        meta=OutputMeta(verification=verification),
        data=data,
    ).model_dump()


def rejected(comment: str = "", edited: bool = False) -> dict:
    """Build a HITL-rejected envelope.

    Args:
        comment (str): Reviewer's reason.
        edited (bool): Whether the reviewer had edited the payload.

    Returns:
        dict: Envelope with `status="rejected"` and `data=None`.

    Examples:
        >>> rejected("not now")["meta"]["status"]
        'rejected'
        >>> rejected()["data"] is None
        True
    """
    return Output(
        meta=OutputMeta(status="rejected", comment=comment, edited=edited),
        data=None,
    ).model_dump()


PRIORITY_LABELS: dict[int, str] = {0: "none", 1: "low", 3: "medium", 5: "high"}
"""TickTick priority scale — 0 none, 1 low, 3 medium, 5 high (there is no 2 or 4)."""
