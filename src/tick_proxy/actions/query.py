"""Query actions — workspace map, project/folder search, and the full task filter."""

from pydantic import BaseModel, Field

from ..client import TickClient
from ..query import filter_tasks, resolve_project_ids
from .base import ActionDef


class EmptyPayload(BaseModel):
    pass


def workspace_map(client: TickClient, p: EmptyPayload) -> dict:
    """Navigable folder → project tree, with active task counts.

    Parameters:
        - (no payload)

    Examples:
        - See the workspace layout:
            `tick-proxy do workspace-map`
            → {"folders":[{"id":"5aaa","name":"🎓 X","projects":[{"id":"6xxx","name":"P3-Optim","tasks":7}]}],"orphans":[{"id":"6yyy","name":"Inbox","tasks":3}]}
        - Save the map:
            `tick-proxy do workspace-map -o /tmp/map.json`
            → {"folders":[],"orphans":[{"id":"6yyy","name":"Inbox","tasks":3}]}
    """
    sync = client.full_sync()
    tasks = (sync.get("syncTaskBean") or {}).get("update") or []
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t.get("projectId", "")] = counts.get(t.get("projectId", ""), 0) + 1

    folders = {
        g["id"]: {"id": g["id"], "name": g.get("name"), "projects": []}
        for g in (sync.get("projectGroups") or [])
    }
    orphans: list[dict] = []
    for pr in sync.get("projectProfiles") or []:
        entry = {
            "id": pr.get("id"),
            "name": pr.get("name"),
            "tasks": counts.get(pr.get("id", ""), 0),
        }
        group = folders.get(str(pr.get("groupId") or ""))
        (group["projects"] if group else orphans).append(entry)
    return {"folders": list(folders.values()), "orphans": orphans}


class QueryProjectsPayload(BaseModel):
    name_query: str | None = Field(None, description="Substring match on the name")
    regex: str | None = Field(None, description="Regex on the name")
    kinds: list[str] | str | None = Field(None, description="TASK | NOTE")
    include_closed: bool = Field(False, description="Include archived projects")
    limit: int = Field(50, description="Max results")


def query_projects(client: TickClient, p: QueryProjectsPayload) -> list[dict]:
    """Search projects by name / kind, folder-aware.

    Parameters:
        - name_query (str|null): Case-insensitive substring.
        - regex (str|null): Regex on the project name.
        - kinds (list[str]|str|null): TASK and/or NOTE.
        - include_closed (bool), limit (int).

    Examples:
        - Find the optimisation project:
            `tick-proxy do query-projects '{"name_query":"optim"}'`
            → [{"id":"6xxx","name":"P3-Optim","kind":"TASK","groupId":"5aaa"}]
        - List note projects:
            `tick-proxy do query-projects '{"kinds":["NOTE"]}'`
            → [{"id":"6nnn","name":"📓 Notes","kind":"NOTE"}]
    """
    import re

    projects = client.full_sync().get("projectProfiles") or []
    kinds = (
        {k.upper() for k in ([p.kinds] if isinstance(p.kinds, str) else p.kinds)}
        if p.kinds
        else set()
    )
    out = []
    for pr in projects:
        if not p.include_closed and pr.get("closed"):
            continue
        if kinds and str(pr.get("kind") or "TASK").upper() not in kinds:
            continue
        name = str(pr.get("name") or "")
        if p.name_query and p.name_query.lower() not in name.lower():
            continue
        if p.regex and not re.search(p.regex, name, re.IGNORECASE):
            continue
        out.append(pr)
    return out[: p.limit]


class QueryFoldersPayload(BaseModel):
    name_query: str | None = Field(None, description="Substring match on the name")
    include_project_counts: bool = Field(True, description="Add a project count")
    limit: int = Field(50, description="Max results")


def query_folders(client: TickClient, p: QueryFoldersPayload) -> list[dict]:
    """Search project folders, optionally with their project counts.

    Parameters:
        - name_query (str|null): Case-insensitive substring on the folder name.
        - include_project_counts (bool): Add `projects` count to each folder.
        - limit (int): Max results.

    Examples:
        - All folders with counts:
            `tick-proxy do query-folders`
            → [{"id":"5aaa","name":"🎓 X","projects":4}]
        - Find one folder:
            `tick-proxy do query-folders '{"name_query":"tech"}'`
            → [{"id":"5bbb","name":"🛠️ Tech","projects":2}]
    """
    sync = client.full_sync()
    counts: dict[str, int] = {}
    for pr in sync.get("projectProfiles") or []:
        gid = str(pr.get("groupId") or "")
        counts[gid] = counts.get(gid, 0) + 1
    out = []
    for g in sync.get("projectGroups") or []:
        name = str(g.get("name") or "")
        if p.name_query and p.name_query.lower() not in name.lower():
            continue
        entry = dict(g)
        if p.include_project_counts:
            entry["projects"] = counts.get(str(g.get("id")), 0)
        out.append(entry)
    return out[: p.limit]


class QueryTasksPayload(BaseModel):
    project_ids: list[str] | str | None = Field(
        None, description="Restrict to project ids"
    )
    project_names: list[str] | str | None = Field(
        None, description="Match project names"
    )
    folder_ids: list[str] | str | None = Field(
        None, description="Restrict to folder ids"
    )
    folder_names: list[str] | str | None = Field(None, description="Match folder names")
    tags: list[str] | str | None = Field(None, description="Filter by tags")
    tag_mode: str = Field("any", description="any | all")
    priorities: list[int] | None = Field(
        None, description="Exact priorities, e.g. [5,3]"
    )
    min_priority: int | None = Field(None, description="Minimum priority")
    kinds: list[str] | str | None = Field(
        None, description='TEXT|NOTE|CHECKLIST — ["NOTE"] = notes only'
    )
    text_query: str | None = Field(None, description="Keyword search")
    keyword_mode: str = Field("any", description="any | all | phrase")
    search_fields: list[str] | str | None = Field(None, description="Fields to search")
    regex: str | None = Field(None, description="Inclusion regex")
    exclude_regex: str | None = Field(None, description="Exclusion regex")
    due_from: str | None = Field(None, description="Due window start")
    due_to: str | None = Field(None, description="Due window end")
    start_from: str | None = Field(None, description="Start window start")
    start_to: str | None = Field(None, description="Start window end")
    created_from: str | None = Field(None, description="Created window start")
    created_to: str | None = Field(None, description="Created window end")
    modified_from: str | None = Field(None, description="Modified window start")
    modified_to: str | None = Field(
        None, description="Modified window end — stale tasks"
    )
    time_from: str | None = Field(None, description="Time of day HH:MM start")
    time_to: str | None = Field(None, description="Time of day HH:MM end")
    all_day: bool | None = Field(None, description="All-day tasks only / never")
    timed_only: bool = Field(False, description="Keep only timed items")
    has_reminders: bool | None = Field(None, description="With / without reminders")
    is_recurring: bool | None = Field(None, description="Recurring tasks only / never")
    has_checklist: bool | None = Field(None, description="With / without checklist")
    parent_only: bool = Field(False, description="Exclude subtasks")
    subtasks_only: bool = Field(False, description="Only subtasks")
    group_by: str | None = Field(None, description='"priority" groups the result')
    sort_by: str = Field(
        "dueDate", description="dueDate|priority|title|createdTime|modifiedTime"
    )
    descending: bool = Field(False, description="Reverse the sort")
    limit: int = Field(50, description="Max results")


def _tasks_and_scope(client: TickClient, p: BaseModel) -> tuple[list[dict], dict]:
    """Fetch every active task and resolve the payload's project scope.

    Args:
        client (TickClient): The API client.
        p (BaseModel): Any payload exposing the scope fields.

    Returns:
        tuple[list[dict], dict]: `(tasks, filter_dict_with_project_ids)`.

    Examples:
        >>> tasks, f = _tasks_and_scope(client, QueryTasksPayload(project_names="Tech"))
        >>> f["project_ids"]
        ['6xxx']
    """
    sync = client.full_sync()
    tasks = (sync.get("syncTaskBean") or {}).get("update") or []
    f = p.model_dump(exclude_none=True)
    f["project_ids"] = resolve_project_ids(
        f, sync.get("projectProfiles") or [], sync.get("projectGroups") or []
    )
    return tasks, f


def query_tasks(client: TickClient, p: QueryTasksPayload) -> dict:
    """The full task filter engine — dates, tags, regex, priorities, shape.

    This one action covers what used to be several: `kinds:["NOTE"]` returns
    notes only, `group_by:"priority"` gives the priority dashboard, and
    `modified_to` (a date in the past) gives stale tasks.

    Parameters:
        - Scope: project_ids, project_names, folder_ids, folder_names.
        - Content: tags + tag_mode, kinds, text_query + keyword_mode,
          search_fields, regex, exclude_regex.
        - Time: due_from/due_to, start_*, created_*, modified_* (stale),
          time_from/time_to, all_day, timed_only.
        - Shape: priorities, min_priority, has_reminders, is_recurring,
          has_checklist, parent_only, subtasks_only.
        - Output: group_by ("priority"), sort_by, descending, limit.

    Examples:
        - High-priority tasks of a project due today:
            `tick-proxy do query-tasks '{"project_names":["Tech"],"priorities":[5],"due_from":"2026-08-09","due_to":"2026-08-09T23:59:59"}'`
            → {"count":1,"tasks":[{"id":"68f1","title":"Ship v1","priority":5}]}
        - Stale tasks (untouched for a month) grouped by priority:
            `tick-proxy do query-tasks '{"modified_to":"2026-07-09","group_by":"priority"}'`
            → {"count":2,"groups":{"5":[{"id":"68f4","title":"Old urgent"}],"0":[{"id":"68f7","title":"Someday"}]}}
    """
    tasks, f = _tasks_and_scope(client, p)
    matched = filter_tasks(tasks, f)
    if p.group_by == "priority":
        groups: dict[str, list[dict]] = {}
        for t in matched:
            groups.setdefault(str(t.get("priority") or 0), []).append(t)
        return {"count": len(matched), "groups": groups}
    return {"count": len(matched), "tasks": matched}


class QueryAgendaPayload(QueryTasksPayload):
    from_dt: str = Field(..., description="Window start, ISO date or datetime")
    to_dt: str = Field(..., description="Window end, ISO date or datetime")


def query_agenda(client: TickClient, p: QueryAgendaPayload) -> dict:
    """Scheduled items inside an explicit date/time window.

    Parameters:
        - from_dt (str), to_dt (str): the window bounds (required).
        - Every `query-tasks` filter is also accepted (tags, priorities, …).
        - timed_only (bool): keep only items with a real time of day.

    Examples:
        - This weekend's agenda:
            `tick-proxy do query-agenda '{"from_dt":"2026-08-15","to_dt":"2026-08-16T23:59:59"}'`
            → {"count":3,"tasks":[{"id":"68f1","title":"Gym","dueDate":"2026-08-15T09:00:00.000+0000"}]}
        - Only timed events tomorrow morning:
            `tick-proxy do query-agenda '{"from_dt":"2026-08-10","to_dt":"2026-08-10T12:00:00","timed_only":true}'`
            → {"count":1,"tasks":[{"id":"68f2","title":"Standup","dueDate":"2026-08-10T09:30:00.000+0000"}]}
    """
    tasks, f = _tasks_and_scope(client, p)
    f["due_from"] = p.from_dt
    f["due_to"] = p.to_dt
    matched = filter_tasks(tasks, f)
    return {"count": len(matched), "tasks": matched}


ACTIONS = [
    ActionDef("workspace-map", EmptyPayload, workspace_map, v2=True, group="Query"),
    ActionDef(
        "query-projects", QueryProjectsPayload, query_projects, v2=True, group="Query"
    ),
    ActionDef(
        "query-folders", QueryFoldersPayload, query_folders, v2=True, group="Query"
    ),
    ActionDef("query-tasks", QueryTasksPayload, query_tasks, v2=True, group="Query"),
    ActionDef("query-agenda", QueryAgendaPayload, query_agenda, v2=True, group="Query"),
]
