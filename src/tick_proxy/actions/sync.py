"""Sync actions — flat task list and the full workspace sync."""

from pydantic import BaseModel

from ..client import TickClient
from .base import ActionDef


class EmptyPayload(BaseModel):
    pass


def task_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """Every active task across every project, in one V2 sync call.

    Much faster than iterating projects. Returns a flat list.

    Parameters:
        - (no payload)

    Examples:
        - All active tasks:
            `tick-proxy do task-list`
            → [{"id":"68f1","title":"Ship v1","projectId":"6xxx","priority":5}]
        - Count them:
            `tick-proxy do task-list -f table`
            → [{"id":"68f1","title":"Ship v1"}]
    """
    sync = client.full_sync()
    return (sync.get("syncTaskBean") or {}).get("update") or []


def sync_full(client: TickClient, p: EmptyPayload) -> dict:
    """Projects + tasks + tags + folders in ONE call — the complete picture.

    This is also the only reliable way to read a project's real `groupId`
    (V1 always returns null for it).

    Parameters:
        - (no payload)

    Examples:
        - Full snapshot:
            `tick-proxy do sync-full`
            → {"inboxId":"inbox1275839472","projects":[{"id":"6xxx","groupId":"5aaa"}],"tasks":[],"tags":[],"folders":[],"task_count":128}
        - Save it to a file:
            `tick-proxy do sync-full -o /tmp/tick-sync.json`
            → {"inboxId":"inbox1275839472","task_count":128}
    """
    sync = client.full_sync()
    tasks = (sync.get("syncTaskBean") or {}).get("update") or []
    return {
        "inboxId": sync.get("inboxId"),
        "projects": sync.get("projectProfiles") or [],
        "folders": sync.get("projectGroups") or [],
        "tags": sync.get("tags") or [],
        "tasks": tasks,
        "task_count": len(tasks),
    }


ACTIONS = [
    ActionDef("task-list", EmptyPayload, task_list, v2=True, group="Sync"),
    ActionDef("sync-full", EmptyPayload, sync_full, v2=True, group="Sync"),
]
