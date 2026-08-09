"""Batch task actions — batch CRUD, move (cascading), parent link, subtask composite."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..models import Verification
from .base import ActionDef, always_verify, compare
from .tasks import TaskCreatePayload, task_create


class BatchCreatePayload(BaseModel):
    tasks: list[dict] = Field(..., description="Task dicts; each needs at least title")


def task_batch_create(client: TickClient, p: BatchCreatePayload) -> dict:
    """Create several tasks in one V2 batch call.

    ⚠️ `parentId` is silently ignored here — link children afterwards with
    `task-parent-set` (which verifies the link).

    Parameters:
        - tasks (list[dict]): Each entry needs `title`; optional `projectId`,
          `content`, `priority`, `dueDate`, `timeZone`, `tags`, `isAllDay`.

    Examples:
        - Create two tasks at once:
            `tick-proxy do task-batch-create '{"tasks":[{"title":"A","projectId":"6xxx"},{"title":"B","projectId":"6xxx"}]}'`
            → {"id2etag":{"68f1":"abc","68f2":"def"},"id2error":{}}
        - Create one task in the Inbox:
            `tick-proxy do task-batch-create '{"tasks":[{"title":"Call back"}]}'`
            → {"id2etag":{"68f3":"ghi"},"id2error":{}}
    """
    return client.v2_post("/batch/task", {"add": p.tasks})


class BatchUpdatePayload(BaseModel):
    tasks: list[dict] = Field(..., description="Task dicts; each needs id + projectId")


def task_batch_update(client: TickClient, p: BatchUpdatePayload) -> dict:
    """Update several tasks in one V2 batch call.

    ⚠️ This endpoint cannot set reminders reliably — use `task-update` (V1) with
    an explicit `due_date` anchor for anything reminder-related.

    Parameters:
        - tasks (list[dict]): Each entry MUST carry `id` and `projectId`, plus
          the fields to change (no read-modify-write: give full values).

    Examples:
        - Bump two tasks to high priority:
            `tick-proxy do task-batch-update '{"tasks":[{"id":"68f1","projectId":"6xxx","priority":5},{"id":"68f2","projectId":"6xxx","priority":5}]}'`
            → {"id2etag":{"68f1":"abc","68f2":"def"},"id2error":{}}
        - Retitle one task:
            `tick-proxy do task-batch-update '{"tasks":[{"id":"68f1","projectId":"6xxx","title":"New title"}]}'`
            → {"id2etag":{"68f1":"xyz"},"id2error":{}}
    """
    return client.v2_post("/batch/task", {"update": p.tasks})


class BatchDeletePayload(BaseModel):
    tasks: list[dict] = Field(..., description="[{taskId, projectId}, …]")


def task_batch_delete(client: TickClient, p: BatchDeletePayload) -> dict:
    """Delete several tasks in one V2 batch call. IRREVERSIBLE — HITL required.

    Parameters:
        - tasks (list[dict]): `[{"taskId": "...", "projectId": "..."}, …]`.

    Examples:
        - Delete two tasks:
            `tick-proxy do task-batch-delete '{"tasks":[{"taskId":"68f1","projectId":"6xxx"},{"taskId":"68f2","projectId":"6xxx"}]}'`
            → {"id2etag":{},"id2error":{}}
        - Delete one task:
            `tick-proxy do task-batch-delete '{"tasks":[{"taskId":"68f3","projectId":"6xxx"}]}'`
            → {"id2etag":{},"id2error":{}}
    """
    return client.v2_post("/batch/task", {"delete": p.tasks})


class MovePayload(BaseModel):
    moves: list[dict] = Field(
        ..., description="[{taskId, fromProjectId, toProjectId}, …]"
    )


@always_verify("projectId")
def task_move(client: TickClient, p: MovePayload) -> tuple[dict, Verification]:
    """Move tasks between projects — children are moved too, then verified.

    The V2 API never cascades to subtasks: moving a parent alone leaves its
    children stranded in the old project. This action fetches `childIds` from
    each source project and appends the children to the same batch. The result
    is ALWAYS read back and compared (`@always_verify`) — a silent partial move
    turns into `meta.verification.ok=false` and exit code 1.

    Parameters:
        - moves (list[dict]): `[{"taskId","fromProjectId","toProjectId"}, …]`.
          Provide parents only — children are added automatically.

    Examples:
        - Move a task with its subtasks:
            `tick-proxy do task-move '{"moves":[{"taskId":"68e0","fromProjectId":"6xxx","toProjectId":"7yyy"}]}'`
            → {"moved":{"68e0":"7yyy","68f1":"7yyy"},"cascaded_children":["68f1"]}
        - Move two independent tasks:
            `tick-proxy do task-move '{"moves":[{"taskId":"68f2","fromProjectId":"6xxx","toProjectId":"7yyy"},{"taskId":"68f3","fromProjectId":"6xxx","toProjectId":"7yyy"}]}'`
            → {"moved":{"68f2":"7yyy","68f3":"7yyy"},"cascaded_children":[]}
    """
    moves = [dict(m) for m in p.moves]
    known = {m["taskId"] for m in moves}
    cascaded: list[str] = []

    # One /data read per distinct source project gives us childIds for free.
    for project_id in {m["fromProjectId"] for m in moves}:
        data = client.v1_get(f"/project/{project_id}/data")
        index = {t["id"]: t for t in (data.get("tasks") or []) if t.get("id")}
        for m in list(moves):
            if m["fromProjectId"] != project_id:
                continue
            for child in index.get(m["taskId"], {}).get("childIds") or []:
                if child not in known:
                    known.add(child)
                    cascaded.append(child)
                    moves.append(
                        {
                            "taskId": child,
                            "fromProjectId": project_id,
                            "toProjectId": m["toProjectId"],
                        }
                    )

    client.v2_post("/batch/taskProject", moves)

    expected = {m["taskId"]: m["toProjectId"] for m in moves}
    actual: dict[str, Any] = {}
    for task_id, target in expected.items():
        try:
            actual[task_id] = client.v1_get(f"/project/{target}/task/{task_id}").get(
                "projectId"
            )
        except Exception:  # noqa: BLE001 — a miss means "not there", which is the verdict
            actual[task_id] = None
    verification = compare("GET /open/v1/project/{to}/task/{id}", expected, actual)
    return {"moved": expected, "cascaded_children": cascaded}, verification


class ParentSetPayload(BaseModel):
    task_id: str = Field(..., description="Child task id")
    project_id: str = Field(..., description="Project holding both tasks")
    parent_id: str | None = Field(None, description="New parent id (set)")
    old_parent_id: str | None = Field(None, description="Previous parent id (unset)")


@always_verify("parentId")
def task_parent_set(
    client: TickClient, p: ParentSetPayload
) -> tuple[dict, Verification]:
    """Set or unset a task's parent — always verified.

    `parentId` is silently ignored at creation time and unreliable through the
    V2 batch endpoint, so the relationship is systematically read back and
    compared. Provide EITHER `parent_id` (to link) OR `old_parent_id` (to unlink).

    Parameters:
        - task_id (str): The child task.
        - project_id (str): Project holding both tasks.
        - parent_id (str|null): New parent — sets the link.
        - old_parent_id (str|null): Previous parent — removes the link.

    Examples:
        - Link a child to its parent:
            `tick-proxy do task-parent-set '{"task_id":"68f1","project_id":"6xxx","parent_id":"68e0"}'`
            → {"task_id":"68f1","parent_id":"68e0"}
        - Detach a child:
            `tick-proxy do task-parent-set '{"task_id":"68f1","project_id":"6xxx","old_parent_id":"68e0"}'`
            → {"task_id":"68f1","parent_id":null}
    """
    entry: dict[str, Any] = {"taskId": p.task_id, "projectId": p.project_id}
    if p.parent_id:
        entry["parentId"] = p.parent_id
    if p.old_parent_id:
        entry["oldParentId"] = p.old_parent_id
    client.v2_post("/batch/taskParent", [entry])

    actual = client.v1_get(f"/project/{p.project_id}/task/{p.task_id}")
    verification = compare(
        f"GET /open/v1/project/{p.project_id}/task/{p.task_id}",
        {"parentId": p.parent_id},
        {"parentId": actual.get("parentId")},
    )
    return {"task_id": p.task_id, "parent_id": actual.get("parentId")}, verification


class SubtaskCreatePayload(TaskCreatePayload):
    parent_id: str = Field(..., description="Parent task id")
    project_id: str = Field(..., description="Project holding the parent")  # type: ignore[assignment]


@always_verify("parentId")
def subtask_create(
    client: TickClient, p: SubtaskCreatePayload
) -> tuple[dict, Verification]:
    """Create a task and link it under a parent — one safe composite, verified.

    This is `task-create` + `task-parent-set` in a single call, because passing
    `parentId` at creation silently does nothing. The parent link is read back
    and compared before the command returns.

    Parameters:
        - title (str): Subtask title. parent_id (str), project_id (str): required.
        - All other `task-create` fields are accepted (priority, due_date, tags…).

    Examples:
        - Add a subtask under a parent:
            `tick-proxy do subtask-create '{"title":"Draft outline","parent_id":"68e0","project_id":"6xxx"}'`
            → {"id":"68f1","title":"Draft outline","parentId":"68e0"}
        - Add a high-priority subtask with a due date:
            `tick-proxy do subtask-create '{"title":"Review","parent_id":"68e0","project_id":"6xxx","priority":5,"due_date":"2026-08-12T09:00:00+0000"}'`
            → {"id":"68f2","title":"Review","parentId":"68e0","priority":5}
    """
    created = task_create(
        client,
        TaskCreatePayload(**p.model_dump(exclude={"parent_id"}, exclude_none=True)),
    )
    task_id = created["id"]
    client.v2_post(
        "/batch/taskParent",
        [{"taskId": task_id, "projectId": p.project_id, "parentId": p.parent_id}],
    )
    actual = client.v1_get(f"/project/{p.project_id}/task/{task_id}")
    verification = compare(
        f"GET /open/v1/project/{p.project_id}/task/{task_id}",
        {"parentId": p.parent_id},
        {"parentId": actual.get("parentId")},
    )
    return {**created, "parentId": actual.get("parentId")}, verification


ACTIONS = [
    ActionDef(
        "task-batch-create",
        BatchCreatePayload,
        task_batch_create,
        v2=True,
        group="Batch",
    ),
    ActionDef(
        "task-batch-update",
        BatchUpdatePayload,
        task_batch_update,
        v2=True,
        group="Batch",
    ),
    ActionDef(
        "task-batch-delete",
        BatchDeletePayload,
        task_batch_delete,
        hitl=True,
        v2=True,
        group="Batch",
    ),
    ActionDef(
        "task-move", MovePayload, task_move, verify="always", v2=True, group="Batch"
    ),
    ActionDef(
        "task-parent-set",
        ParentSetPayload,
        task_parent_set,
        verify="always",
        v2=True,
        group="Batch",
    ),
    ActionDef(
        "subtask-create",
        SubtaskCreatePayload,
        subtask_create,
        verify="always",
        v2=True,
        group="Batch",
    ),
]
