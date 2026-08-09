"""History action — completed / abandoned / deleted tasks through one filter engine."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..query import filter_tasks, resolve_project_ids
from .base import ActionDef


class HistoryQueryPayload(BaseModel):
    history_source: str = Field(
        "completed", description="completed | abandoned | deleted"
    )
    from_date: str | None = Field(None, description="ISO date, e.g. 2026-08-01")
    to_date: str | None = Field(None, description="ISO date, e.g. 2026-08-31")
    project_ids: list[str] | str | None = Field(
        None, description="Restrict to projects"
    )
    project_names: list[str] | str | None = Field(
        None, description="Match project names"
    )
    tags: list[str] | str | None = Field(None, description="Filter by tags")
    text_query: str | None = Field(None, description="Keyword search")
    regex: str | None = Field(None, description="Inclusion regex")
    exclude_regex: str | None = Field(None, description="Exclusion regex")
    min_priority: int | None = Field(None, description="Minimum priority")
    sort_by: str = Field("completedTime", description="Sort key")
    descending: bool = Field(True, description="Newest first")
    limit: int = Field(50, description="Max results")


def history_query(client: TickClient, p: HistoryQueryPayload) -> dict:
    """Search completed, abandoned or deleted tasks with the full filter engine.

    `history_source` selects the bucket — this single action replaces the old
    separate completed/deleted listings.

    Parameters:
        - history_source (str): completed (default) | abandoned | deleted.
        - from_date/to_date (str|null): ISO dates bounding the search.
        - project_ids/project_names/tags: scope filters.
        - text_query/regex/exclude_regex: grep-like matching.
        - min_priority (int|null), sort_by (str), descending (bool), limit (int).

    Examples:
        - What did I finish in August?
            `tick-proxy do history-query '{"history_source":"completed","from_date":"2026-08-01","to_date":"2026-08-31"}'`
            → {"source":"completed","count":2,"tasks":[{"id":"68f1","title":"Ship v1","completedTime":"2026-08-09T18:00:00.000+0000"}]}
        - What is in the trash?
            `tick-proxy do history-query '{"history_source":"deleted","limit":10}'`
            → {"source":"deleted","count":1,"tasks":[{"id":"68f9","title":"Scratch"}]}
    """
    source = p.history_source.lower()
    if source == "deleted":
        raw = client.v2_get("/project/all/trash/pagination", {"start": 0, "limit": 200})
        tasks = raw.get("tasks") if isinstance(raw, dict) else raw
    else:
        status = "Abandoned" if source == "abandoned" else "Completed"
        params: dict[str, Any] = {"limit": 200, "status": status}
        if p.from_date:
            params["from"] = f"{p.from_date} 00:00:00"
        if p.to_date:
            params["to"] = f"{p.to_date} 23:59:59"
        tasks = client.v2_get("/project/all/completedInAll", params)

    tasks = tasks or []
    f = p.model_dump(exclude_none=True)
    f["project_ids"] = resolve_project_ids(f, client.v1_get("/project") or [])
    return {
        "source": source,
        "count": len(filter_tasks(tasks, f)),
        "tasks": filter_tasks(tasks, f),
    }


ACTIONS = [
    ActionDef(
        "history-query", HistoryQueryPayload, history_query, v2=True, group="History"
    ),
]
