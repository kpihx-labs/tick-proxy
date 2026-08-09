"""View actions — thin shortcuts over the filter engine (today, week, upcoming, overdue)."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from ..client import TickClient
from ..query import day_bounds, filter_tasks, resolve_project_ids
from .base import ActionDef


class ViewPayload(BaseModel):
    local_date: str | None = Field(None, description="YYYY-MM-DD; today when omitted")
    project_names: list[str] | str | None = Field(
        None, description="Restrict to projects"
    )
    project_ids: list[str] | str | None = Field(
        None, description="Restrict to project ids"
    )
    tags: list[str] | str | None = Field(None, description="Filter by tags")
    text_query: str | None = Field(None, description="Keyword search")
    timed_only: bool = Field(False, description="Keep only items with a time of day")
    limit: int = Field(50, description="Max results")


def _scope(client: TickClient, p: BaseModel) -> tuple[list[dict], dict]:
    """Fetch active tasks and resolve the payload's project scope.

    Args:
        client (TickClient): The API client.
        p (BaseModel): Any view payload.

    Returns:
        tuple[list[dict], dict]: `(tasks, filter_dict)`.

    Examples:
        >>> tasks, f = _scope(client, ViewPayload())
        >>> f["project_ids"]
        []
    """
    sync = client.full_sync()
    tasks = (sync.get("syncTaskBean") or {}).get("update") or []
    f = p.model_dump(exclude_none=True)
    f["project_ids"] = resolve_project_ids(
        f, sync.get("projectProfiles") or [], sync.get("projectGroups") or []
    )
    return tasks, f


def view_today(client: TickClient, p: ViewPayload) -> dict:
    """Today's scheduled items. `timed_only:true` returns the events only.

    Parameters:
        - local_date (str|null): YYYY-MM-DD; today when omitted.
        - timed_only (bool): keep only items with a real time of day (events).
        - project_names/project_ids/tags/text_query: optional scope filters.

    Examples:
        - What is on today?
            `tick-proxy do view-today`
            → {"date":"2026-08-09","count":4,"tasks":[{"id":"68f1","title":"Ship v1","dueDate":"2026-08-09T21:00:00.000+0000"}]}
        - Only today's timed events:
            `tick-proxy do view-today '{"timed_only":true}'`
            → {"date":"2026-08-09","count":1,"tasks":[{"id":"68f2","title":"Standup","dueDate":"2026-08-09T09:30:00.000+0000"}]}
    """
    start, end = day_bounds(p.local_date)
    tasks, f = _scope(client, p)
    f["due_from"], f["due_to"] = start.isoformat(), end.isoformat()
    matched = filter_tasks(tasks, f)
    return {"date": start.date().isoformat(), "count": len(matched), "tasks": matched}


class WeekPayload(ViewPayload):
    days: int = Field(7, description="Window length in days")


def view_week(client: TickClient, p: WeekPayload) -> dict:
    """Scheduled items over a multi-day window (7 days by default).

    Parameters:
        - days (int): Window length. local_date (str|null): first day.
        - timed_only (bool) and the usual scope filters.

    Examples:
        - The coming week:
            `tick-proxy do view-week`
            → {"from":"2026-08-09","to":"2026-08-15","count":12,"tasks":[{"id":"68f1","title":"Ship v1"}]}
        - The next 3 days only:
            `tick-proxy do view-week '{"days":3}'`
            → {"from":"2026-08-09","to":"2026-08-11","count":5,"tasks":[{"id":"68f1","title":"Ship v1"}]}
    """
    start, _ = day_bounds(p.local_date)
    end = start + timedelta(days=p.days) - timedelta(microseconds=1)
    tasks, f = _scope(client, p)
    f["due_from"], f["due_to"] = start.isoformat(), end.isoformat()
    matched = filter_tasks(tasks, f)
    return {
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "count": len(matched),
        "tasks": matched,
    }


def view_week_overview(client: TickClient, p: WeekPayload) -> dict:
    """Planning split of the week: timed events, due tasks, and overdue backlog.

    Parameters:
        - days (int): Window length (7 by default). local_date (str|null).
        - Usual scope filters (project_names, tags, text_query).

    Examples:
        - Weekly planning view:
            `tick-proxy do view-week-overview`
            → {"events":[{"id":"68f2","title":"Standup"}],"due":[{"id":"68f1","title":"Ship v1"}],"overdue":[{"id":"68f7","title":"Late report"}]}
        - Two-week horizon:
            `tick-proxy do view-week-overview '{"days":14}'`
            → {"events":[],"due":[{"id":"68f1","title":"Ship v1"}],"overdue":[]}
    """
    start, _ = day_bounds(p.local_date)
    end = start + timedelta(days=p.days) - timedelta(microseconds=1)
    tasks, f = _scope(client, p)

    window = dict(f, due_from=start.isoformat(), due_to=end.isoformat())
    due = filter_tasks(tasks, window)
    events = filter_tasks(tasks, dict(window, timed_only=True))
    overdue = filter_tasks(tasks, dict(f, due_to=start.isoformat()))
    return {
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "events": events,
        "due": due,
        "overdue": overdue,
    }


class UpcomingPayload(ViewPayload):
    days: int = Field(7, description="Look-ahead window in days")
    min_priority: int | None = Field(None, description="Minimum priority")


def view_upcoming(client: TickClient, p: UpcomingPayload) -> dict:
    """Tasks due within the next N days (starting today).

    Parameters:
        - days (int): Look-ahead window. min_priority (int|null).
        - Usual scope filters.

    Examples:
        - What is coming this week?
            `tick-proxy do view-upcoming`
            → {"days":7,"count":9,"tasks":[{"id":"68f1","title":"Ship v1","dueDate":"2026-08-12T09:00:00.000+0000"}]}
        - Only the urgent ones in 3 days:
            `tick-proxy do view-upcoming '{"days":3,"min_priority":5}'`
            → {"days":3,"count":1,"tasks":[{"id":"68f1","title":"Ship v1","priority":5}]}
    """
    start, _ = day_bounds(p.local_date)
    end = start + timedelta(days=p.days) - timedelta(microseconds=1)
    tasks, f = _scope(client, p)
    f["due_from"], f["due_to"] = start.isoformat(), end.isoformat()
    matched = filter_tasks(tasks, f)
    return {"days": p.days, "count": len(matched), "tasks": matched}


def view_overdue(client: TickClient, p: ViewPayload) -> dict:
    """Active tasks whose due date is already in the past.

    Parameters:
        - local_date (str|null): the "now" reference; today when omitted.
        - Usual scope filters (project_names, tags, text_query, limit).

    Examples:
        - The backlog:
            `tick-proxy do view-overdue`
            → {"count":3,"tasks":[{"id":"68f7","title":"Late report","dueDate":"2026-08-01T09:00:00.000+0000"}]}
        - Overdue in one project:
            `tick-proxy do view-overdue '{"project_names":["Tech"]}'`
            → {"count":1,"tasks":[{"id":"68f7","title":"Late report"}]}
    """
    now = day_bounds(p.local_date)[0] if p.local_date else datetime.now(UTC)
    tasks, f = _scope(client, p)
    f["due_to"] = now.isoformat()
    matched = filter_tasks(tasks, f)
    return {"count": len(matched), "tasks": matched}


ACTIONS = [
    ActionDef("view-today", ViewPayload, view_today, v2=True, group="Views"),
    ActionDef("view-week", WeekPayload, view_week, v2=True, group="Views"),
    ActionDef(
        "view-week-overview", WeekPayload, view_week_overview, v2=True, group="Views"
    ),
    ActionDef("view-upcoming", UpcomingPayload, view_upcoming, v2=True, group="Views"),
    ActionDef("view-overdue", ViewPayload, view_overdue, v2=True, group="Views"),
]
