"""
Action framework — declarative approval, review, and verification policies.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from time import monotonic, sleep
from typing import Any, Literal

from pydantic import BaseModel

from ..exceptions import TickProxyError, TickTickAPIError
from ..models import Verification

ReviewMode = Literal["default", "task"]
Preflight = Callable[[Any, BaseModel], None]
DELETE_CONFIRM_TIMEOUT_SECONDS = 10.0
DELETE_CONFIRM_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class ActionDef:
    """One `do` action: name, payload model, handler and its policies.

    Attributes:
        name (str): The flat kebab-case action name, e.g. `task-create`.
        payload (type[BaseModel] | None): Pydantic model validating the payload
            (None when the action takes no payload).
        handler (Callable): `handler(client, payload) -> dict` or a
            `(dict, Verification)` tuple for required post-write checks.
        hitl (bool): Derived from the handler's `@require_approval` declaration.
        v2 (bool): True when the action requires V2 (session token) auth.
        review_mode (ReviewMode): `"task"` selects the structured task-document
            review; `"default"`
            always uses the standard editable JSON form.
        group (str): Catalog group used by `do --help`, e.g. "Tasks".

    Examples:
        >>> ActionDef("task-create", None, lambda c, p: {}, group="Tasks").name
        'task-create'
        >>> ActionDef("task-read", None, lambda c, p: {}).hitl
        False
    """

    name: str
    payload: type[BaseModel] | None
    handler: Callable[..., Any]
    hitl: bool = False
    v2: bool = False
    review_mode: ReviewMode = "default"
    group: str = "Misc"
    aliases: tuple[str, ...] = field(default_factory=tuple)


def require_verification(*checks: str) -> Callable:
    """Declare fields a write must read back and compare before returning.

    The wrapped handler must return `(data, verification)` where `verification`
    is a `Verification` model built by the handler itself (it knows what to
    re-read). The decorator only guarantees the contract: the attribute
        `__require_verification__` is set so registry tests can prove the policy.

    Args:
        *checks (str): Field names the handler must compare, e.g.
            `"parentId", "childIds"`.

    Returns:
        Callable: The decorator.

    Examples:
        >>> @require_verification("parentId")
        ... def h(client, payload): return {}, None
        >>> h.__require_verification__
        True
        >>> h.__verification_checks__
        ('parentId',)
        >>> @require_verification("title", "content")
        ... def task(client, payload): return {}
        >>> task.__verification_checks__
        ('title', 'content')
        >>> callable(task)
        True
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.__require_verification__ = True  # type: ignore[attr-defined]
        wrapper.__verification_checks__ = checks  # type: ignore[attr-defined]
        return wrapper

    return decorator


def require_approval(review_mode: ReviewMode = "default") -> Callable:
    """Declare a handler's mandatory centralized HITL review policy.

    Args:
        review_mode (ReviewMode): `"task"` for structured title/content/desc
            review, or `"default"` for the shared full-JSON review.

    Returns:
        Callable: A decorator carrying auditable review metadata.

    Examples:
        >>> @require_approval("task")
        ... def write_task(client, payload): return {}
        >>> write_task.__require_approval__
        True
        >>> write_task.__review_mode__
        'task'
        >>> @require_approval()
        ... def remove(client, payload): return {}
        >>> remove.__review_mode__
        'default'
        >>> callable(remove)
        True
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.__require_approval__ = True  # type: ignore[attr-defined]
        wrapper.__review_mode__ = review_mode  # type: ignore[attr-defined]
        return wrapper

    return decorator


def require_preflight(
    *, check: Preflight, identity_fields: tuple[str, ...]
) -> Callable:
    """Require a resource-safety read before HITL and lock its identity in review.

    Args:
        check (Preflight): Read-only guard receiving the API client and validated
            payload. It raises `TickProxyError` when the requested resource is
            not safe to act on.
        identity_fields (tuple[str, ...]): Payload fields that identify the
            reviewed target and cannot change between preflight and approval.

    Returns:
        Callable: Decorator that declares the preflight and identity policy.

    Examples:
        >>> def exists(client, payload): return None
        >>> @require_preflight(check=exists, identity_fields=("project_id",))
        ... def delete(client, payload): return {}
        >>> delete.__preflight_identity_fields__
        ('project_id',)
        >>> delete.__preflight_check__ is exists
        True
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.__preflight_check__ = check  # type: ignore[attr-defined]
        wrapper.__preflight_identity_fields__ = identity_fields  # type: ignore[attr-defined]
        return wrapper

    return decorator


def require_reviews(func: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a task write as requiring title/content/description patch reviews.

    Args:
        func (Callable[..., Any]): Task create/update handler to wrap.

    Returns:
        Callable: The same handler with explicit review-field metadata.

    Examples:
        >>> @require_reviews
        ... def write_task(client, payload): return {}
        >>> write_task.__require_reviews__
        True
        >>> write_task.__review_fields__
        ('title', 'content', 'desc')
        >>> write_task.__name__
        'write_task'
        >>> callable(write_task)
        True
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    wrapper.__require_reviews__ = True  # type: ignore[attr-defined]
    wrapper.__review_fields__ = ("title", "content", "desc")  # type: ignore[attr-defined]
    return wrapper


def action_def(
    name: str,
    payload: type[BaseModel] | None,
    handler: Callable[..., Any],
    *,
    v2: bool = False,
    group: str = "Misc",
    aliases: tuple[str, ...] = (),
) -> ActionDef:
    """Build an action definition from visible handler decorators.

    Args:
        name (str): Flat registered action name.
        payload (type[BaseModel] | None): Pydantic payload model.
        handler (Callable[..., Any]): Decorated implementation.
        v2 (bool): Whether V2 configuration is required.
        group (str): Help catalog group.
        aliases (tuple[str, ...]): Optional command aliases.

    Returns:
        ActionDef: HITL and review mode derived from the handler.

    Examples:
        >>> @require_approval()
        ... def delete(client, payload): return {}
        >>> action_def("delete", None, delete).hitl
        True
        >>> @require_reviews
        ... @require_approval("task")
        ... def task_write(client, payload): return {}
        >>> action_def("task-update", None, task_write).review_mode
        'task'
        >>> action_def("read", None, lambda client, payload: {}).hitl
        False
        >>> action_def("sync", None, lambda client, payload: {}, v2=True).v2
        True
    """
    review_mode: ReviewMode = getattr(handler, "__review_mode__", "default")
    if review_mode not in ("default", "task"):
        raise ValueError(f"{name} declares unsupported review mode: {review_mode!r}.")
    requires_reviews = bool(getattr(handler, "__require_reviews__", False))
    if requires_reviews and review_mode != "task":
        raise ValueError(
            f"{name} has @require_reviews without @require_approval('task')."
        )
    return ActionDef(
        name=name,
        payload=payload,
        handler=handler,
        hitl=bool(getattr(handler, "__require_approval__", False)),
        v2=v2,
        review_mode=review_mode,
        group=group,
        aliases=aliases,
    )


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


def verify_absence(
    read: Callable[[], Any],
    resource_id: str,
    method: str,
    *,
    timeout_seconds: float = DELETE_CONFIRM_TIMEOUT_SECONDS,
    interval_seconds: float = DELETE_CONFIRM_INTERVAL_SECONDS,
) -> Verification:
    """Poll a post-delete read until TickTick confirms the resource is absent.

    Args:
        read (Callable[[], Any]): Fresh API read that raises 404 after deletion.
        resource_id (str): Deleted resource identifier.
        method (str): Human-readable read endpoint recorded in the proof.
        timeout_seconds (float): Maximum eventual-consistency confirmation wait.
        interval_seconds (float): Delay between reads while the stale resource remains visible.

    Returns:
        Verification: `ok=True` after TickTick returns HTTP 404 or an empty
        resource body (`{}`), its observed V1 post-delete representation.

    Raises:
        TickProxyError: When the resource still exists after the confirmation deadline.

    Examples:
        >>> calls = iter([{}, TickTickAPIError(404, "Not found")])
        >>> verify_absence(lambda: next(calls), "p1", "GET /project/p1", timeout_seconds=1, interval_seconds=0).ok
        True
        >>> verify_absence(lambda: (_ for _ in ()).throw(TickTickAPIError(404, "Not found")), "t1", "GET /task/t1").actual
        {'deleted': 't1'}
        >>> verify_absence(lambda: {}, "p1", "GET /project/p1").ok
        True
        >>> verify_absence(lambda: (_ for _ in ()).throw(TickTickAPIError(403, "Forbidden")), "p1", "GET /project/p1")
        Traceback (most recent call last):
        ...
        tick_proxy.exceptions.TickTickAPIError: [403] Forbidden
        >>> verify_absence(lambda: {}, "p1", "GET /project/p1", timeout_seconds=0, interval_seconds=0)
        Traceback (most recent call last):
        ...
        tick_proxy.exceptions.TickProxyError: Delete was accepted but p1 still exists after 0.0 seconds.
    """
    deadline = monotonic() + timeout_seconds
    while True:
        try:
            observed = read()
        except TickTickAPIError as exc:
            if exc.status == 404:
                return compare(
                    method,
                    {"deleted": resource_id},
                    {"deleted": resource_id},
                )
            raise
        if observed in ({}, None):
            return compare(
                method,
                {"deleted": resource_id},
                {"deleted": resource_id},
            )
        if monotonic() >= deadline:
            raise TickProxyError(
                f"Delete was accepted but {resource_id} still exists after {timeout_seconds} seconds."
            )
        sleep(interval_seconds)
