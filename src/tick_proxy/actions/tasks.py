"""Task actions — create, update, complete, reopen, delete, info, list by project/inbox."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from .base import ActionDef


def _reminders(minutes: list[int] | None) -> list[str] | None:
    """Convert minutes-before into TickTick TRIGGER strings.

    Args:
        minutes (list[int] | None): Minutes before due, e.g. `[0, 30, 1440]`.

    Returns:
        list[str] | None: `["TRIGGER:PT0S", "TRIGGER:-PT30M", "TRIGGER:-P1D"]`,
        or None when `minutes` is None (field left untouched).

    Examples:
        >>> _reminders([0, 30])
        ['TRIGGER:PT0S', 'TRIGGER:-PT30M']
        >>> _reminders([1440, 2880])
        ['TRIGGER:-P1D', 'TRIGGER:-P2D']
    """
    if minutes is None:
        return None
    out = []
    for m in minutes:
        if m == 0:
            out.append("TRIGGER:PT0S")
        elif m % 1440 == 0:
            out.append(f"TRIGGER:-P{m // 1440}D")
        elif m % 60 == 0:
            out.append(f"TRIGGER:-PT{m // 60}H")
        else:
            out.append(f"TRIGGER:-PT{m}M")
    return out


class TaskCreatePayload(BaseModel):
    title: str = Field(..., description="Task title")
    project_id: str | None = Field(None, description="Target project id; omit → Inbox")
    content: str | None = Field(None, description="Body / description")
    desc: str | None = Field(None, description="Checklist description field")
    priority: int = Field(0, description="0=none 1=low 3=medium 5=high")
    due_date: str | None = Field(
        None, description="ISO 8601, e.g. 2026-08-12T09:00:00+0000"
    )
    start_date: str | None = Field(None, description="ISO 8601")
    time_zone: str | None = Field(None, description="IANA tz, e.g. Europe/Paris")
    tags: list[str] | None = Field(None, description="Tag names")
    all_day: bool | None = Field(None, description="True for an all-day task")
    kind: str | None = Field(None, description="TEXT | NOTE | CHECKLIST")
    checklist_items: list[str] | None = Field(None, description="Checklist item titles")
    reminder_minutes: list[int] | None = Field(
        None, description="Minutes before due: 0=at time, 30, 1440=1 day"
    )
    recurrence: str | None = Field(
        None, description="RRULE, e.g. RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"
    )
    column_id: str | None = Field(None, description="Kanban column id")


def task_create(client: TickClient, p: TaskCreatePayload) -> dict:
    """Create a task.

    `parentId` is NOT accepted here — the V1 endpoint silently ignores it.
    Use `task-parent-set` afterwards (or `subtask-create`, which does both and
    verifies). When you pass `reminder_minutes`, always pass `due_date` too:
    V1 anchors the trigger on the due date, and drops the reminder without it.

    Parameters:
        - title (str): Task title.
        - project_id (str|null): Target project; omit → Inbox.
        - priority (int): 0=none, 1=low, 3=medium, 5=high.
        - due_date/start_date (str|null): ISO 8601. time_zone: IANA name.
        - tags (list[str]|null), all_day (bool|null), kind (str|null).
        - checklist_items (list[str]|null): auto-sets kind=CHECKLIST.
        - reminder_minutes (list[int]|null): minutes before due.
        - recurrence (str|null): RRULE string. column_id (str|null): kanban column.

    Examples:
        - Simple task in the Inbox:
            `tick-proxy do task-create '{"title":"Buy bread"}'`
            → {"id":"68f1a2b3c4d5e6f708192a3b","title":"Buy bread","projectId":"inbox1275839472"}
        - High-priority task with a reminder one day before:
            `tick-proxy do task-create '{"title":"Ship v1","project_id":"6xxx","priority":5,"due_date":"2026-08-12T09:00:00+0000","time_zone":"Europe/Paris","reminder_minutes":[1440]}'`
            → {"id":"68f1a2b3c4d5e6f708192a3c","title":"Ship v1","priority":5,"reminders":["TRIGGER:-P1D"]}
    """
    body: dict[str, Any] = {"title": p.title}
    if p.project_id:
        body["projectId"] = p.project_id
    for src, dst in (
        ("content", "content"),
        ("desc", "desc"),
        ("due_date", "dueDate"),
        ("start_date", "startDate"),
        ("time_zone", "timeZone"),
        ("tags", "tags"),
        ("all_day", "isAllDay"),
        ("kind", "kind"),
        ("recurrence", "repeatFlag"),
        ("column_id", "columnId"),
    ):
        v = getattr(p, src)
        if v is not None:
            body[dst] = v
    if p.priority:
        body["priority"] = p.priority
    if p.checklist_items:
        body["kind"] = "CHECKLIST"
        body["items"] = [{"title": t, "status": 0} for t in p.checklist_items]
    rem = _reminders(p.reminder_minutes)
    if rem is not None:
        body["reminders"] = rem
    return client.v1_post("/task", body)


class TaskIdPayload(BaseModel):
    task_id: str = Field(..., description="Task id")
    project_id: str = Field(..., description="Project containing the task")


class TaskUpdatePayload(TaskCreatePayload):
    task_id: str = Field(..., description="Task id to update")
    project_id: str = Field(..., description="Project containing the task")  # type: ignore[assignment]
    title: str | None = Field(None, description="New title")  # type: ignore[assignment]
    status: int | None = Field(None, description="0=active 2=completed")


def task_update(client: TickClient, p: TaskUpdatePayload) -> dict:
    """Update a task (read-modify-write — only the fields you pass change).

    ⚠️ Reminder anchor: `reminder_minutes` is silently dropped when the task has
    no due date visible to V1. Always pass `due_date` + `time_zone` alongside
    `reminder_minutes`, even when you are not changing the date.

    Parameters:
        - task_id (str), project_id (str): required identifiers.
        - title/content/desc/priority/due_date/start_date/time_zone/tags/
          all_day/kind/recurrence/column_id: same semantics as `task-create`.
        - status (int|null): 0=active, 2=completed.
        - reminder_minutes (list[int]|null): pass [] to clear all reminders.

    Examples:
        - Raise the priority:
            `tick-proxy do task-update '{"task_id":"68f1","project_id":"6xxx","priority":5}'`
            → {"id":"68f1","priority":5,"title":"Ship v1"}
        - Set a 2-day reminder (with the mandatory due-date anchor):
            `tick-proxy do task-update '{"task_id":"68f1","project_id":"6xxx","due_date":"2026-08-12T09:00:00+0000","time_zone":"Europe/Paris","reminder_minutes":[2880]}'`
            → {"id":"68f1","reminders":["TRIGGER:-P2D"]}
    """
    current = client.v1_get(f"/project/{p.project_id}/task/{p.task_id}")
    body: dict[str, Any] = {"id": p.task_id, "projectId": p.project_id}
    for src, dst in (
        ("title", "title"),
        ("content", "content"),
        ("desc", "desc"),
        ("due_date", "dueDate"),
        ("start_date", "startDate"),
        ("time_zone", "timeZone"),
        ("tags", "tags"),
        ("all_day", "isAllDay"),
        ("kind", "kind"),
        ("recurrence", "repeatFlag"),
        ("column_id", "columnId"),
        ("status", "status"),
    ):
        v = getattr(p, src, None)
        if v is not None:
            body[dst] = v
    if p.priority:
        body["priority"] = p.priority
    if p.checklist_items is not None:
        body["kind"] = "CHECKLIST"
        body["items"] = [{"title": t, "status": 0} for t in p.checklist_items]
    rem = _reminders(p.reminder_minutes)
    if rem is not None:
        # V1 needs the due date as the trigger anchor — reuse the current one.
        body.setdefault("dueDate", current.get("dueDate"))
        body["reminders"] = rem
    return client.v1_post(f"/task/{p.task_id}", body)


def task_complete(client: TickClient, p: TaskIdPayload) -> dict:
    """Mark a task as completed (status → 2).

    Parameters:
        - task_id (str): The task to complete.
        - project_id (str): Project containing the task.

    Examples:
        - Complete a task:
            `tick-proxy do task-complete '{"task_id":"68f1","project_id":"6xxx"}'`
            → {"completed":"68f1"}
        - Complete an inbox task:
            `tick-proxy do task-complete '{"task_id":"68f2","project_id":"inbox1275839472"}'`
            → {"completed":"68f2"}
    """
    client.v1_post(f"/project/{p.project_id}/task/{p.task_id}/complete")
    return {"completed": p.task_id}


def task_reopen(client: TickClient, p: TaskIdPayload) -> dict:
    """Reopen a completed task (status → 0).

    Parameters:
        - task_id (str): The task to reopen.
        - project_id (str): Project containing the task.

    Examples:
        - Reopen a task:
            `tick-proxy do task-reopen '{"task_id":"68f1","project_id":"6xxx"}'`
            → {"id":"68f1","status":0}
        - Reopen then re-prioritise (two calls):
            `tick-proxy do task-reopen '{"task_id":"68f1","project_id":"6xxx"}'`
            → {"id":"68f1","status":0}
    """
    return client.v1_post(
        f"/task/{p.task_id}", {"id": p.task_id, "projectId": p.project_id, "status": 0}
    )


def task_delete(client: TickClient, p: TaskIdPayload) -> dict:
    """Delete a task permanently. IRREVERSIBLE — HITL required.

    Parameters:
        - task_id (str): The task to delete.
        - project_id (str): Project containing the task.

    Examples:
        - Delete a task (opens the HITL review form):
            `tick-proxy do task-delete '{"task_id":"68f1","project_id":"6xxx"}'`
            → {"deleted":"68f1"}
        - Delete an inbox task:
            `tick-proxy do task-delete '{"task_id":"68f2","project_id":"inbox1275839472"}'`
            → {"deleted":"68f2"}
    """
    client.v1_delete(f"/project/{p.project_id}/task/{p.task_id}")
    return {"deleted": p.task_id}


def task_info(client: TickClient, p: TaskIdPayload) -> dict:
    """Full detail of one task: checklist, reminders, recurrence, tags, dates.

    Parameters:
        - task_id (str): The task to read.
        - project_id (str): Project containing the task.

    Examples:
        - Read a task:
            `tick-proxy do task-info '{"task_id":"68f1","project_id":"6xxx"}'`
            → {"id":"68f1","title":"Ship v1","priority":5,"tags":["work"],"reminders":["TRIGGER:-P1D"]}
        - Read a checklist task:
            `tick-proxy do task-info '{"task_id":"68f3","project_id":"6xxx"}'`
            → {"id":"68f3","kind":"CHECKLIST","items":[{"title":"step 1","status":0}]}
    """
    return client.v1_get(f"/project/{p.project_id}/task/{p.task_id}")


class ProjectTasksPayload(BaseModel):
    project_id: str = Field(..., description="Project id")
    include_completed: bool = Field(False, description="Include completed tasks")


def project_tasks(client: TickClient, p: ProjectTasksPayload) -> dict:
    """All tasks of one project, with its kanban columns when defined.

    Parameters:
        - project_id (str): The project to read.
        - include_completed (bool): Keep completed tasks in the result.

    Examples:
        - Active tasks of a project:
            `tick-proxy do project-tasks '{"project_id":"6xxx"}'`
            → {"project":{"id":"6xxx","name":"🛠️ Tech & Science"},"tasks":[{"id":"68f1","title":"Ship v1"}],"columns":[]}
        - Including completed ones:
            `tick-proxy do project-tasks '{"project_id":"6xxx","include_completed":true}'`
            → {"project":{"id":"6xxx"},"tasks":[{"id":"68f1","status":2}],"columns":[]}
    """
    data = client.v1_get(f"/project/{p.project_id}/data")
    tasks = data.get("tasks") or []
    if not p.include_completed:
        tasks = [t for t in tasks if int(t.get("status") or 0) == 0]
    return {
        "project": data.get("project", {}),
        "tasks": tasks,
        "columns": data.get("columns") or [],
    }


class InboxListPayload(BaseModel):
    include_completed: bool = Field(False, description="Include completed tasks")


def inbox_list(client: TickClient, p: InboxListPayload) -> dict:
    """Tasks sitting in the Inbox (the default project).

    Parameters:
        - include_completed (bool): Keep completed tasks in the result.

    Examples:
        - Inbox triage:
            `tick-proxy do inbox-list`
            → {"project":{"id":"inbox1275839472","name":"Inbox"},"tasks":[{"id":"68f2","title":"Call back"}],"columns":[]}
        - Including completed:
            `tick-proxy do inbox-list '{"include_completed":true}'`
            → {"project":{"id":"inbox1275839472"},"tasks":[{"id":"68f2","status":2}],"columns":[]}
    """
    return project_tasks(
        client,
        ProjectTasksPayload(
            project_id=client.inbox_id(), include_completed=p.include_completed
        ),
    )


ACTIONS = [
    ActionDef("task-create", TaskCreatePayload, task_create, group="Tasks"),
    ActionDef("task-update", TaskUpdatePayload, task_update, group="Tasks"),
    ActionDef("task-complete", TaskIdPayload, task_complete, group="Tasks"),
    ActionDef("task-reopen", TaskIdPayload, task_reopen, group="Tasks"),
    ActionDef("task-delete", TaskIdPayload, task_delete, hitl=True, group="Tasks"),
    ActionDef("task-info", TaskIdPayload, task_info, group="Tasks"),
    ActionDef("project-tasks", ProjectTasksPayload, project_tasks, group="Tasks"),
    ActionDef("inbox-list", InboxListPayload, inbox_list, v2=True, group="Tasks"),
]
