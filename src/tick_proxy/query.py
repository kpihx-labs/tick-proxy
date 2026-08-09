"""
The filter engine — ported from `tick_mcp/services/query.py`, flat at
`src/tick_proxy/` (no `services/` package).

Role: turn a declarative filter description into the right V1/V2 reads plus
client-side post-filtering. Every `query-*` and `view-*` action is a thin
wrapper over this module; the engine itself never talks to the CLI.

No password ever flows through here — credentials live only in
`~/.config/tick-proxy/.env` and in the transient `admin session-refresh` HITL
exchange.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ── date helpers ──────────────────────────────────────────────────────────────


def parse_dt(value: Any) -> datetime | None:
    """Parse a TickTick / ISO timestamp into an aware UTC datetime.

    TickTick returns `2026-08-09T21:00:00.000+0000`; users pass `2026-08-09` or
    `2026-08-09T23:59:59`. All shapes are accepted.

    Args:
        value (Any): A string timestamp, a `datetime`, a `date`, or None.

    Returns:
        datetime | None: An aware UTC datetime, or None when unparsable.

    Examples:
        >>> parse_dt("2026-08-09T21:00:00.000+0000").hour
        21
        >>> parse_dt("2026-08-09").date().isoformat()
        '2026-08-09'
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip()
    text = re.sub(r"\.(\d{3})(\d*)", r".\1", text)  # trim over-long milliseconds
    candidates = [
        text,
        text.replace("Z", "+00:00"),
        re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text),
        re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00")),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def day_bounds(local_date: str | None = None) -> tuple[datetime, datetime]:
    """Return the UTC start/end datetimes of a calendar day.

    Args:
        local_date (str | None): `YYYY-MM-DD`; today when omitted.

    Returns:
        tuple[datetime, datetime]: `(00:00:00, 23:59:59.999999)` for that day.

    Examples:
        >>> s, e = day_bounds("2026-08-09"); s.isoformat()[:10]
        '2026-08-09'
        >>> s, e = day_bounds(); (e - s).days
        0
    """
    base = parse_dt(local_date) or datetime.now(UTC)
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


# ── field accessors ───────────────────────────────────────────────────────────


def task_due(task: dict) -> datetime | None:
    """Return a task's effective scheduled datetime (dueDate, else startDate).

    Args:
        task (dict): A TickTick task object.

    Returns:
        datetime | None: The scheduled moment, or None when unscheduled.

    Examples:
        >>> task_due({"dueDate": "2026-08-09T21:00:00.000+0000"}).hour
        21
        >>> task_due({"title": "no date"}) is None
        True
    """
    return parse_dt(task.get("dueDate")) or parse_dt(task.get("startDate"))


def task_tags(task: dict) -> list[str]:
    """Return a task's tags, lower-cased.

    Args:
        task (dict): A TickTick task object.

    Returns:
        list[str]: Lower-cased tag names (empty when untagged).

    Examples:
        >>> task_tags({"tags": ["Revision", "EXAM"]})
        ['revision', 'exam']
        >>> task_tags({})
        []
    """
    return [str(t).lower() for t in (task.get("tags") or [])]


def is_timed(task: dict) -> bool:
    """Whether a task has a real time-of-day (not an all-day item).

    Args:
        task (dict): A TickTick task object.

    Returns:
        bool: True when the task is scheduled at a specific time.

    Examples:
        >>> is_timed({"dueDate": "2026-08-09T21:00:00.000+0000", "isAllDay": False})
        True
        >>> is_timed({"dueDate": "2026-08-09T00:00:00.000+0000", "isAllDay": True})
        False
    """
    if task.get("isAllDay") or task.get("allDay"):
        return False
    return task_due(task) is not None


# ── matching primitives ───────────────────────────────────────────────────────


def _as_list(value: Any) -> list[str]:
    """Normalise a str-or-list filter argument into a list of strings.

    Args:
        value (Any): A string, a list, or None.

    Returns:
        list[str]: Always a list (empty when the input is falsy).

    Examples:
        >>> _as_list("work")
        ['work']
        >>> _as_list(["a", "b"])
        ['a', 'b']
    """
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def text_matches(
    task: dict,
    text_query: str | None,
    fields: Iterable[str] = ("title", "content", "desc"),
    mode: str = "any",
) -> bool:
    """Grep-like text match over the chosen task fields.

    Args:
        task (dict): A TickTick task object.
        text_query (str | None): Space-separated keywords; None matches everything.
        fields (Iterable[str]): Fields to search.
        mode (str): `any` (default), `all`, or `phrase`.

    Returns:
        bool: True when the task matches.

    Examples:
        >>> text_matches({"title": "Buy bread"}, "bread")
        True
        >>> text_matches({"title": "Buy bread"}, "bread milk", mode="all")
        False
    """
    if not text_query:
        return True
    haystack = " ".join(str(task.get(f, "") or "") for f in fields).lower()
    needle = text_query.lower().strip()
    if mode == "phrase":
        return needle in haystack
    words = [w for w in needle.split() if w]
    if mode == "all":
        return all(w in haystack for w in words)
    return any(w in haystack for w in words)


def regex_matches(
    task: dict,
    pattern: str | None,
    exclude: str | None = None,
    fields: Iterable[str] = ("title", "content", "desc"),
) -> bool:
    """Regex include/exclude match over the chosen task fields.

    Args:
        task (dict): A TickTick task object.
        pattern (str | None): Inclusion regex; None means "no constraint".
        exclude (str | None): Exclusion regex; a match rejects the task.
        fields (Iterable[str]): Fields to search.

    Returns:
        bool: True when the task passes both constraints.

    Examples:
        >>> regex_matches({"title": "TP Optimisation"}, r"^TP\\b")
        True
        >>> regex_matches({"title": "TP Optimisation"}, None, exclude="Optim")
        False
    """
    haystack = " ".join(str(task.get(f, "") or "") for f in fields)
    if pattern and not re.search(pattern, haystack, re.IGNORECASE):
        return False
    return not (exclude and re.search(exclude, haystack, re.IGNORECASE))


# ── the engine ────────────────────────────────────────────────────────────────

SORTABLE = {
    "dueDate": task_due,
    "priority": lambda t: int(t.get("priority") or 0),
    "title": lambda t: str(t.get("title") or "").lower(),
    "createdTime": lambda t: parse_dt(t.get("createdTime")),
    "modifiedTime": lambda t: parse_dt(t.get("modifiedTime")),
    "completedTime": lambda t: parse_dt(t.get("completedTime")),
}


def filter_tasks(tasks: list[dict], f: dict[str, Any]) -> list[dict]:
    """Apply the full declarative filter set to a task list.

    Supported keys: `project_ids`, `project_names`, `folder_ids`, `folder_names`
    (pre-resolved by the caller into `project_ids`), `tags`, `tag_mode`,
    `priorities`, `min_priority`, `text_query`, `keyword_mode`, `search_fields`,
    `regex`, `exclude_regex`, `due_from`, `due_to`, `start_from`, `start_to`,
    `created_from`, `created_to`, `modified_from`, `modified_to`, `time_from`,
    `time_to`, `all_day`, `timed_only`, `has_reminders`, `is_recurring`,
    `has_checklist`, `parent_only`, `subtasks_only`, `kinds`, `sort_by`,
    `descending`, `limit`.

    Args:
        tasks (list[dict]): The candidate tasks (already fetched).
        f (dict[str, Any]): The filter object, straight from the payload.

    Returns:
        list[dict]: Matching tasks, sorted and limited.

    Examples:
        >>> filter_tasks([{"title": "Buy bread", "priority": 5}], {"min_priority": 3})
        [{'title': 'Buy bread', 'priority': 5}]
        >>> filter_tasks([{"title": "Buy bread", "priority": 0}], {"min_priority": 3})
        []
    """
    out: list[dict] = []
    project_ids = set(_as_list(f.get("project_ids")))
    tags_wanted = {t.lower() for t in _as_list(f.get("tags"))}
    tag_mode = f.get("tag_mode", "any")
    priorities = set(f.get("priorities") or [])
    min_priority = f.get("min_priority")
    kinds = {k.upper() for k in _as_list(f.get("kinds"))}
    fields = tuple(_as_list(f.get("search_fields")) or ("title", "content", "desc"))

    due_from, due_to = parse_dt(f.get("due_from")), parse_dt(f.get("due_to"))
    start_from, start_to = parse_dt(f.get("start_from")), parse_dt(f.get("start_to"))
    created_from = parse_dt(f.get("created_from"))
    created_to = parse_dt(f.get("created_to"))
    modified_from = parse_dt(f.get("modified_from"))
    modified_to = parse_dt(f.get("modified_to"))

    for t in tasks:
        if project_ids and t.get("projectId") not in project_ids:
            continue
        if kinds and str(t.get("kind") or "TEXT").upper() not in kinds:
            continue
        if priorities and int(t.get("priority") or 0) not in priorities:
            continue
        if min_priority is not None and int(t.get("priority") or 0) < int(min_priority):
            continue
        if tags_wanted:
            have = set(task_tags(t))
            if tag_mode == "all" and not tags_wanted.issubset(have):
                continue
            if tag_mode != "all" and not (tags_wanted & have):
                continue
        if not text_matches(
            t, f.get("text_query"), fields, f.get("keyword_mode", "any")
        ):
            continue
        if not regex_matches(t, f.get("regex"), f.get("exclude_regex"), fields):
            continue

        due = task_due(t)
        if due_from and (due is None or due < due_from):
            continue
        if due_to and (due is None or due > due_to):
            continue
        s = parse_dt(t.get("startDate"))
        if start_from and (s is None or s < start_from):
            continue
        if start_to and (s is None or s > start_to):
            continue
        c = parse_dt(t.get("createdTime"))
        if created_from and (c is None or c < created_from):
            continue
        if created_to and (c is None or c > created_to):
            continue
        m = parse_dt(t.get("modifiedTime"))
        if modified_from and (m is None or m < modified_from):
            continue
        if modified_to and (m is None or m > modified_to):
            continue

        if f.get("time_from") or f.get("time_to"):
            if due is None:
                continue
            hhmm = due.strftime("%H:%M")
            if f.get("time_from") and hhmm < str(f["time_from"]):
                continue
            if f.get("time_to") and hhmm > str(f["time_to"]):
                continue

        all_day = f.get("all_day")
        if all_day is not None and bool(t.get("isAllDay")) is not bool(all_day):
            continue
        if f.get("timed_only") and not is_timed(t):
            continue
        if f.get("has_reminders") is not None and bool(t.get("reminders")) is not bool(
            f["has_reminders"]
        ):
            continue
        if f.get("is_recurring") is not None and bool(t.get("repeatFlag")) is not bool(
            f["is_recurring"]
        ):
            continue
        if f.get("has_checklist") is not None and bool(t.get("items")) is not bool(
            f["has_checklist"]
        ):
            continue
        if f.get("parent_only") and t.get("parentId"):
            continue
        if f.get("subtasks_only") and not t.get("parentId"):
            continue

        out.append(t)

    sort_by = f.get("sort_by") or "dueDate"
    key = SORTABLE.get(sort_by, SORTABLE["dueDate"])
    out.sort(
        key=lambda t: (
            key(t) is None,
            key(t) or 0
            if sort_by == "priority"
            else key(t) or datetime.max.replace(tzinfo=UTC),
        ),
        reverse=bool(f.get("descending")),
    )
    limit = int(f.get("limit") or 0)
    return out[:limit] if limit > 0 else out


def resolve_project_ids(
    f: dict[str, Any], projects: list[dict], folders: list[dict] | None = None
) -> list[str]:
    """Turn `project_names` / `folder_ids` / `folder_names` into project ids.

    Args:
        f (dict[str, Any]): The filter object.
        projects (list[dict]): Every project (from `/project` or full sync).
        folders (list[dict] | None): Project groups (folders), when available.

    Returns:
        list[str]: Explicit project ids, merged with any given `project_ids`.
        Empty means "no project restriction".

    Examples:
        >>> resolve_project_ids({"project_names": ["Tech"]},
        ...                     [{"id": "6xxx", "name": "🛠️ Tech & Science"}])
        ['6xxx']
        >>> resolve_project_ids({}, [{"id": "6xxx", "name": "Tech"}])
        []
    """
    ids = set(_as_list(f.get("project_ids")))
    names = [n.lower() for n in _as_list(f.get("project_names"))]
    if names:
        for p in projects:
            label = str(p.get("name") or "").lower()
            if any(n in label for n in names):
                ids.add(str(p.get("id")))

    group_ids = set(_as_list(f.get("folder_ids")))
    folder_names = [n.lower() for n in _as_list(f.get("folder_names"))]
    if folder_names and folders:
        for g in folders:
            if any(n in str(g.get("name") or "").lower() for n in folder_names):
                group_ids.add(str(g.get("id")))
    if group_ids:
        for p in projects:
            if str(p.get("groupId") or "") in group_ids:
                ids.add(str(p.get("id")))
    return sorted(ids)
