"""
Action framework — `ActionDef`, the `@always_verify` decorator and helpers.

`@always_verify` is the structural twin of `hitl.request_approval`: it wraps a
write handler so a read-back verification ALWAYS runs after the write,
regardless of any CLI flag. There is no `--verify` option anywhere — `cli.py`
has no code path to skip it, and `make smoke` fails (AST-free attribute check)
when an action declared `verify="always"` lacks the decorator.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Literal

from pydantic import BaseModel

from ..models import Verification

Verify = Literal["always", "never"]


@dataclass(frozen=True)
class ActionDef:
    """One `do` action: name, payload model, handler and its policies.

    Attributes:
        name (str): The flat kebab-case action name, e.g. `task-create`.
        payload (type[BaseModel] | None): Pydantic model validating the payload
            (None when the action takes no payload).
        handler (Callable): `handler(client, payload) -> dict` (or `-> (dict, Verification)`
            when decorated with `@always_verify`).
        hitl (bool): True when the action must pass through the HITL web form.
        verify (Verify): "always" when the handler carries `@always_verify`.
        v2 (bool): True when the action requires V2 (session token) auth.
        group (str): Catalog group used by `do --help`, e.g. "Tasks".

    Examples:
        >>> ActionDef("task-create", None, lambda c, p: {}, group="Tasks").name
        'task-create'
        >>> ActionDef("task-delete", None, lambda c, p: {}, hitl=True).hitl
        True
    """

    name: str
    payload: type[BaseModel] | None
    handler: Callable[..., Any]
    hitl: bool = False
    verify: Verify = "never"
    v2: bool = False
    group: str = "Misc"
    aliases: tuple[str, ...] = field(default_factory=tuple)


def always_verify(*checks: str) -> Callable:
    """Make read-back verification mandatory for a write handler.

    The wrapped handler must return `(data, verification)` where `verification`
    is a `Verification` model built by the handler itself (it knows what to
    re-read). The decorator only guarantees the contract: the attribute
    `__always_verify__` is set so `make smoke` can prove the decorator is present
    on every action declared `verify="always"`.

    Args:
        *checks (str): Field names the handler is expected to compare, e.g.
            `"parentId", "childIds"`. Recorded on the function for documentation
            and for the smoke test.

    Returns:
        Callable: The decorator.

    Examples:
        >>> @always_verify("parentId")
        ... def h(client, payload): return {}, None
        >>> h.__always_verify__
        True
        >>> h.__verify_checks__
        ('parentId',)
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.__always_verify__ = True  # type: ignore[attr-defined]
        wrapper.__verify_checks__ = checks  # type: ignore[attr-defined]
        return wrapper

    return decorator


def compare(
    method: str, expected: dict[str, Any], actual: dict[str, Any]
) -> Verification:
    """Build a `Verification` by comparing the intended and observed states.

    Args:
        method (str): The read-back performed, for the audit trail.
        expected (dict[str, Any]): What the caller asked for.
        actual (dict[str, Any]): What the server really holds.

    Returns:
        Verification: `ok=True` only when every expected key matches.

    Examples:
        >>> compare("GET /task/68f1", {"parentId": "68e0"}, {"parentId": "68e0"}).ok
        True
        >>> compare("GET /task/68f1", {"parentId": "68e0"}, {"parentId": None}).ok
        False
    """
    ok = all(actual.get(k) == v for k, v in expected.items())
    return Verification(
        method=method,
        checked=sorted(expected),
        expected=expected,
        actual={k: actual.get(k) for k in expected},
        ok=ok,
    )
