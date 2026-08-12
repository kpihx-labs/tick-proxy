"""Batch task actions — batch CRUD, move (cascading), parent link, subtask composite."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..models import Verification
from ..task_documents import materialize_document_fields
from .base import (
    ActionDef,
    action_def,
    compare,
    require_approval,
    require_preflight,
    require_reviews,
    require_verification,
)
from .tasks import TaskCreatePayload, task_create


class BatchCreatePayload(BaseModel):
    tasks: list[dict] = Field(..., description="Task dicts; each needs at least title")


@require_approval()
def task_batch_create(client: TickClient, p: BatchCreatePayload) -> dict:
    """Create several tasks in one V2 batch call after one full-JSON HITL review.

    This command is ideal for bulk insertions of tasks (e.g. bootstrapping a list
    of actions). It preserves the V2 batch format without document diffs. If you
    need precise document editing reviews, run individual `task-create` actions instead.

    Parameters:
        - tasks (list[dict]): Each task dictionary can contain:
          - title (str, required): Task title.
          - projectId (str, optional): Target project ID (defaults to Inbox).
          - content (str, optional): Description body.
          - priority (int, optional): 0=none, 1=low, 3=medium, 5=high.
          - dueDate (str, optional): ISO date string.
          - tags (list[str], optional): Array of tag strings.
          - columnId (str, optional): Kanban column ID.

    Examples:
        - Create two simple tasks in one batch call:
            `tick-proxy do task-batch-create '{"tasks":[{"title":"Task 1","priority":1},{"title":"Task 2","projectId":"6xxx","priority":3}]}'`
            → {"id2etag":{"6task1":"etag1","6task2":"etag2"},"id2error":{}}

        - Create a list of tagged tasks inside a specific project:
            `tick-proxy do task-batch-create '{"tasks":[{"title":"Boot system","projectId":"6xxx","tags":["homelab"]},{"title":"Verify logs","projectId":"6xxx","tags":["homelab","verify"]}]}'`
            → {"id2etag":{"6task1":"etag1","6task2":"etag2"},"id2error":{}}

    Note:
        `parentId` is silently ignored by TickTick during batch creation.
        Always follow up with `task-parent-set` if you need to build subtask structures.
    """
    return client.v2_post("/batch/task", {"add": p.tasks})


class BatchUpdatePayload(BaseModel):
    tasks: list[dict] = Field(..., description="Task dicts; each needs id + projectId")


@require_approval()
def task_batch_update(client: TickClient, p: BatchUpdatePayload) -> dict:
    """Update several tasks in one V2 batch call after one full-JSON HITL review.

    This command is ideal for bulk metadata updates (e.g. shifting projects, adding
    tags, or changing priorities in bulk). It uses V2 batch format without document
    diffs. If you need precise document editing reviews, run individual `task-update`
    actions instead.

    Parameters:
        - tasks (list[dict]): Each task dictionary MUST contain:
          - id (str, required): The ID of the task to update.
          - projectId (str, required): The project ID containing the task.
          And any fields to update, such as:
          - title (str, optional): New title.
          - content (str, optional): New description.
          - priority (int, optional): New priority.
          - dueDate (str, optional): New due date (ISO string).
          - tags (list[str], optional): New tags array.
          - status (int, optional): 0=active, 2=completed, -1=abandoned.
          - columnId (str, optional): Move to a kanban column.

    Examples:
        - Complete one task and change the priority of another:
            `tick-proxy do task-batch-update '{"tasks":[{"id":"6t1","projectId":"6xxx","status":2},{"id":"6t2","projectId":"6xxx","priority":5}]}'`
            → {"id2etag":{"6t1":"etag1","6t2":"etag2"},"id2error":{}}

        - Shift multiple tasks to a kanban column and append a tag:
            `tick-proxy do task-batch-update '{"tasks":[{"id":"6t1","projectId":"6xxx","columnId":"col1","tags":["work"]},{"id":"6t2","projectId":"6xxx","columnId":"col1","tags":["work","urgent"]}]}'`
            → {"id2etag":{"6t1":"etag1","6t2":"etag2"},"id2error":{}}

    Note:
        V2 batch task update cannot reliably set reminders on existing tasks due to
        V2 API dueDate anchoring constraints. Always use `task-update` (V1) for
        setting reminders on individual tasks.
    """
    return client.v2_post("/batch/task", {"update": p.tasks})


class BatchDeletePayload(BaseModel):
    tasks: list[dict] = Field(..., description="[{taskId, projectId}, …]")


def _require_tasks(client: TickClient, tasks: list[dict]) -> None:
    """Ensure every batch deletion target exists before its shared HITL review.

    Args:
        client (TickClient): Authenticated V1 client used for task reads.
        tasks (list[dict]): Deletion records containing `projectId` and `taskId`.

    Returns:
        None: Returns normally only after every target read succeeds.

    Examples:
        >>> _require_tasks(type("C", (), {"v1_get": lambda *_: {"id": "t1"}})(), [{"projectId": "p1", "taskId": "t1"}])
        >>> _require_tasks(type("C", (), {"v1_get": lambda *_: {"id": "t1"}})(), [])
        >>> bool("taskId")
        True
    """
    for task in tasks:
        client.v1_get(f"/project/{task['projectId']}/task/{task['taskId']}")


@require_approval()
@require_preflight(
    check=lambda client, payload: _require_tasks(client, payload.tasks),
    identity_fields=("tasks",),
)
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


@require_verification("projectId")
def task_move(client: TickClient, p: MovePayload) -> tuple[dict, Verification]:
    """Move tasks between projects — children are moved too, then verified.

    The V2 API never cascades to subtasks: moving a parent alone leaves its
    children stranded in the old project. This action fetches `childIds` from
    each source project and appends the children to the same batch. The result
    is ALWAYS read back and compared (`@require_verification`) — a silent partial
    move turns into `data.verification.ok=false` and exit code 1.

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


@require_verification("parentId")
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


@require_reviews
@require_approval("task")
@require_verification("title", "content", "desc", "parentId")
def subtask_create(
    client: TickClient, p: SubtaskCreatePayload
) -> tuple[dict, Verification]:
    """Create a task and link it under a parent — one safe composite, verified.

    This is `task-create` followed by `task-parent-set` in a single command, because
    passing `parentId` directly at task creation is silently ignored by TickTick.
    The relationship is read back and verified before the command returns.

    Parameters:
        - title_ops (list[dict]): Checked operations for title, e.g.
          `[{"op":"insert","insert_lines":[0],"insert_text":"Task Title"}]`.
        - content_ops (list[dict]|null): Checked operations for content description.
        - desc_ops (list[dict]|null): Checked operations for alt description.
        - parent_id (str): The ID of the parent task that will hold this subtask.
        - project_id (str): The project ID containing the parent task.
        - priority (int|null): 0=none, 1=low, 3=medium, 5=high.
        - due_date (str|null): ISO 8601 string, e.g. "2026-08-12T09:00:00+0000".
        - start_date (str|null): ISO 8601 string.
        - time_zone (str|null): IANA time zone name (highly recommended with dates).
        - tags (list[str]|null): Array of tag names.
        - reminder_minutes (list[int]|null): Reminders before due date, e.g. `[0, 30]`.
        - column_id (str|null): Target kanban column ID.

    Examples:
        - Add a basic subtask under a parent:
            `tick-proxy do subtask-create '{"title_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Draft outline"}],"parent_id":"68parent","project_id":"6xxx"}'`
            → {"id":"68child","title":"Draft outline","parentId":"68parent","verification":{"checked":["content","desc","parentId","title"],"ok":true}}

        - Create a high-priority subtask with title/content operations, due date, and reminders:
            `tick-proxy do subtask-create '{"title_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Review draft"}],"content_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Check formatting"}],"parent_id":"68parent","project_id":"6xxx","priority":5,"due_date":"2026-08-12T15:00:00+0000","time_zone":"Europe/Paris","reminder_minutes":[0, 1440]}'`
            → {"id":"68child","title":"Review draft","parentId":"68parent","priority":5,"verification":{"checked":["content","desc","parentId","title"],"ok":true}}

    Note:
        This command is always HITL-required and utilizes the task-document review layout.
        The browser review page displays the full editable JSON beside three Monaco-patch editors
        for title, content, and description.
    """
    task_payload = p.model_dump(exclude={"parent_id"}, exclude_none=True)
    created = task_create(
        client,
        TaskCreatePayload(
            **materialize_document_fields({}, task_payload, require_title=True)
        ),
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
    action_def(
        "task-batch-create",
        BatchCreatePayload,
        task_batch_create,
        v2=True,
        group="Batch",
    ),
    action_def(
        "task-batch-update",
        BatchUpdatePayload,
        task_batch_update,
        v2=True,
        group="Batch",
    ),
    action_def(
        "task-batch-delete",
        BatchDeletePayload,
        task_batch_delete,
        v2=True,
        group="Batch",
    ),
    ActionDef("task-move", MovePayload, task_move, v2=True, group="Batch"),
    ActionDef(
        "task-parent-set",
        ParentSetPayload,
        task_parent_set,
        v2=True,
        group="Batch",
    ),
    action_def(
        "subtask-create",
        SubtaskCreatePayload,
        subtask_create,
        v2=True,
        group="Batch",
    ),
]
