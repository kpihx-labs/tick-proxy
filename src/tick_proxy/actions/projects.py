"""Project, folder and kanban-column actions."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..exceptions import TickProxyError
from ..models import Verification
from .base import (
    ActionDef,
    action_def,
    compare,
    require_approval,
    require_preflight,
    require_verification,
    verify_absence,
)

# V2 wants the literal string "NONE" to clear a folder — JSON null is ignored.
NO_FOLDER = "NONE"


class EmptyPayload(BaseModel):
    pass


def project_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """List every project (list).

    ⚠️ V1 always returns `groupId: null` even when the project sits in a folder —
    use `sync-full` or `workspace-map` when you need the real folder mapping.

    Parameters:
        - (no payload)

    Examples:
        - List projects:
            `tick-proxy do project-list`
            → [{"id":"6xxx","name":"🛠️ Tech & Science","kind":"TASK","closed":false}]
        - Render as a table:
            `tick-proxy do project-list -f table`
            → [{"id":"6xxx","name":"🛠️ Tech & Science"}]
    """
    return client.v1_get("/project")


class ProjectIdPayload(BaseModel):
    project_id: str = Field(..., description="Project id")


def _require_project(client: TickClient, project_id: str) -> None:
    """Ensure a project exists before an irreversible approval can be shown.

    Args:
        client (TickClient): Authenticated V1 client used for the project read.
        project_id (str): Project identifier that must exist.

    Returns:
        None: Returns normally when TickTick returns the project.

    Examples:
        >>> _require_project(type("C", (), {"v1_get": lambda *_: {"id": "p1"}})(), "p1")
        >>> bool("project_id")
        True
    """
    client.v1_get(f"/project/{project_id}")


def project_info(client: TickClient, p: ProjectIdPayload) -> dict:
    """Full detail of one project.

    Parameters:
        - project_id (str): The project to read.

    Examples:
        - Read a project:
            `tick-proxy do project-info '{"project_id":"6xxx"}'`
            → {"id":"6xxx","name":"🛠️ Tech & Science","viewMode":"list","kind":"TASK"}
        - Read the inbox project:
            `tick-proxy do project-info '{"project_id":"inbox1275839472"}'`
            → {"id":"inbox1275839472","name":"Inbox","kind":"TASK"}
    """
    return client.v1_get(f"/project/{p.project_id}")


class ProjectCreatePayload(BaseModel):
    name: str = Field(..., description="Project name")
    color: str | None = Field(None, description="Hex color, e.g. #F18181")
    kind: str = Field("TASK", description="TASK | NOTE")
    view_mode: str | None = Field(None, description="list | kanban | timeline")
    group_id: str | None = Field(None, description="Folder id (from folder-list)")


@require_verification("groupId")
def project_create(
    client: TickClient, p: ProjectCreatePayload
) -> tuple[dict, Verification]:
    """Create a project — folder assignment is applied via V2 and verified.

    V1 silently ignores `groupId` at creation (no error, it simply does not
    persist) and V1 reads always return `null` for it. This action therefore
    creates through V1, then applies the folder through V2 `batch/project`, then
    reads the truth back from the V2 sync.

    Parameters:
        - name (str): Project name. color (str|null): hex, e.g. `#F18181`.
        - kind (str): TASK (default) or NOTE. view_mode (str|null): list|kanban|timeline.
        - group_id (str|null): Folder id — applied through V2 and verified.

    Examples:
        - Simple project:
            `tick-proxy do project-create '{"name":"Sprint 12"}'`
            → {"id":"6zzz","name":"Sprint 12","kind":"TASK","groupId":null}
        - Project inside a folder:
            `tick-proxy do project-create '{"name":"Sprint 12","group_id":"5aaa","color":"#F18181"}'`
            → {"id":"6zzz","name":"Sprint 12","groupId":"5aaa"}
    """
    body: dict[str, Any] = {"name": p.name, "kind": p.kind}
    if p.color:
        body["color"] = p.color
    if p.view_mode:
        body["viewMode"] = p.view_mode
    created = client.v1_post("/project", body)
    project_id = created["id"]

    if p.group_id:
        client.v2_post(
            "/batch/project",
            {"update": [{"id": project_id, "name": p.name, "groupId": p.group_id}]},
        )

    actual_group = None
    if p.group_id:
        for project in client.full_sync().get("projectProfiles") or []:
            if project.get("id") == project_id:
                actual_group = project.get("groupId")
                break
    verification = compare(
        "GET /api/v2/batch/check/0 → projectProfiles",
        {"groupId": p.group_id},
        {"groupId": actual_group},
    )
    return {**created, "groupId": actual_group}, verification


class ProjectUpdatePayload(BaseModel):
    project_id: str = Field(..., description="Project id")
    name: str | None = Field(None, description="New name")
    color: str | None = Field(None, description="Hex color")
    kind: str | None = Field(None, description="TASK | NOTE")
    view_mode: str | None = Field(None, description="list | kanban | timeline")
    group_id: str | None = Field(None, description="Folder id; 'NONE' clears it")
    closed: bool | None = Field(None, description="True archives the project")


@require_verification("groupId")
def project_update(
    client: TickClient, p: ProjectUpdatePayload
) -> tuple[dict, Verification]:
    """Update a project — folder moves go through V2 and are verified.

    `group_id` never persists through V1, so it is applied with a V2
    read-modify-write and then read back from the sync. Pass `"NONE"` to take
    the project out of its folder (V2 ignores JSON null here).

    Parameters:
        - project_id (str): The project to update.
        - name/color/kind/view_mode (str|null), closed (bool|null).
        - group_id (str|null): Folder id, or the literal `"NONE"` to clear.

    Examples:
        - Rename a project:
            `tick-proxy do project-update '{"project_id":"6xxx","name":"Tech & Science"}'`
            → {"id":"6xxx","name":"Tech & Science","groupId":null}
        - Move it into a folder:
            `tick-proxy do project-update '{"project_id":"6xxx","group_id":"5aaa"}'`
            → {"id":"6xxx","groupId":"5aaa"}
    """
    current = client.v1_get(f"/project/{p.project_id}")
    body: dict[str, Any] = {}
    for src, dst in (
        ("name", "name"),
        ("color", "color"),
        ("kind", "kind"),
        ("view_mode", "viewMode"),
        ("closed", "closed"),
    ):
        v = getattr(p, src)
        if v is not None:
            body[dst] = v
    updated = client.v1_post(f"/project/{p.project_id}", body) if body else current

    actual_group = None
    if p.group_id is not None:
        client.v2_post(
            "/batch/project",
            {
                "update": [
                    {
                        "id": p.project_id,
                        "name": body.get("name") or current.get("name"),
                        "groupId": p.group_id,
                    }
                ]
            },
        )
        for project in client.full_sync().get("projectProfiles") or []:
            if project.get("id") == p.project_id:
                actual_group = project.get("groupId")
                break
    expected_group = None if p.group_id == NO_FOLDER else p.group_id
    verification = compare(
        "GET /api/v2/batch/check/0 → projectProfiles",
        {"groupId": expected_group},
        {"groupId": actual_group},
    )
    return {**updated, "groupId": actual_group}, verification


@require_approval()
@require_verification("deleted")
@require_preflight(
    check=lambda client, payload: _require_project(client, payload.project_id),
    identity_fields=("project_id",),
)
def project_delete(
    client: TickClient, p: ProjectIdPayload
) -> tuple[dict, Verification]:
    """Delete a project once through V2, poll its absence, and reject absent ids.

    Parameters:
        - project_id (str): The project to delete.

    Examples:
        - Delete a project (opens the HITL review form):
            `tick-proxy do project-delete '{"project_id":"6zzz"}'`
            → {"deleted":"6zzz"}
        - Delete a scratch project:
            `tick-proxy do project-delete '{"project_id":"6www"}'`
            → {"deleted":"6www"}
        - Retry the same deleted project:
            `tick-proxy do project-delete '{"project_id":"6www"}'`
            → exit 1 before HITL: `[404] Not found — check the ids in your payload.`
        - Observe eventual consistency safely:
            `tick-proxy do project-delete '{"project_id":"6test"}'`
            → one V2 batch delete, then bounded GET polling until `data.verification.ok=true`.
    """
    client.v2_post("/batch/project", {"delete": [p.project_id]})
    verification = verify_absence(
        lambda: client.v1_get(f"/project/{p.project_id}"),
        p.project_id,
        f"GET /open/v1/project/{p.project_id}",
    )
    return {"deleted": p.project_id}, verification


# ── folders (project groups) ─────────────────────────────────────────────────


def folder_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """List project folders (groups), with their id and sort order.

    Parameters:
        - (no payload)

    Examples:
        - List folders:
            `tick-proxy do folder-list`
            → [{"id":"5aaa","name":"🎓 X","sortOrder":-1099511627776}]
        - Table view:
            `tick-proxy do folder-list -f table`
            → [{"id":"5aaa","name":"🎓 X"}]
    """
    return client.full_sync().get("projectGroups") or []


class FolderManagePayload(BaseModel):
    add: list[dict] | None = Field(None, description='[{"name":"Work"}, …]')
    update: list[dict] | None = Field(None, description='[{"id":"…","name":"…"}, …]')
    delete: list[str] | None = Field(None, description='["folderId", …]')


def _require_deleted_folders(client: TickClient, folder_ids: list[str] | None) -> None:
    """Ensure every requested folder deletion target exists before HITL.

    Args:
        client (TickClient): Authenticated client used for the authoritative V2 sync.
        folder_ids (list[str] | None): Folder identifiers requested for deletion.

    Returns:
        None: Returns normally when all requested folders exist or no deletion is requested.

    Raises:
        TickProxyError: When any folder scheduled for deletion is absent.

    Examples:
        >>> _require_deleted_folders(type("C", (), {"full_sync": lambda _: {"projectGroups": [{"id": "f1"}]}})(), ["f1"])
        >>> _require_deleted_folders(type("C", (), {"full_sync": lambda _: {"projectGroups": []}})(), None)
    """
    if not folder_ids:
        return
    existing = {
        str(folder.get("id"))
        for folder in client.full_sync().get("projectGroups") or []
    }
    missing = sorted(set(folder_ids) - existing)
    if missing:
        raise TickProxyError(f"Folder not found: {', '.join(missing)}.")


@require_approval()
@require_preflight(
    check=lambda client, payload: _require_deleted_folders(client, payload.delete),
    identity_fields=("delete",),
)
def folder_manage(client: TickClient, p: FolderManagePayload) -> dict:
    """Create, rename, or delete folders in one mandatory full-JSON HITL review.

    Parameters:
        - add (list[dict]|null): List of new folders to create, e.g. `[{"name": "Work"}]`.
        - update (list[dict]|null): List of folder updates with id and new name,
          e.g. `[{"id": "5aaa", "name": "New Name"}]`.
        - delete (list[str]|null): List of folder IDs to delete, e.g. `["5aaa"]`.
          Any deletion targets are preflight-checked for existence before HITL.

    Examples:
        - Create a new project folder:
            `tick-proxy do folder-manage '{"add":[{"name":"Work"}]}'`
            → {"id2etag":{"5bbb":"abc"},"id2error":{}}

        - Rename an existing folder:
            `tick-proxy do folder-manage '{"update":[{"id":"5aaa","name":"🎓 X"}]}'`
            → {"id2etag":{"5aaa":"def"},"id2error":{}}

        - Delete an existing empty folder:
            `tick-proxy do folder-manage '{"delete":["5aaa"]}'`
            → {"id2etag":{},"id2error":{}}

        - Combine multiple operations (add, update, delete) in one call:
            `tick-proxy do folder-manage '{"add":[{"name":"Personal"}],"update":[{"id":"5aaa","name":"Careers"}],"delete":["5bbb"]}'`
            → {"id2etag":{"5ccc":"ghi","5aaa":"jkl"},"id2error":{}}

    Note:
        This command uses V2 batch project group updates and always requires HITL.
        If a deletion is specified, a read preflight is triggered and the target IDs
        are locked; deleting nonexistent folders will fail closed before HITL.
    """
    body: dict[str, Any] = {}
    if p.add:
        body["add"] = p.add
    if p.update:
        body["update"] = p.update
    if p.delete:
        body["delete"] = p.delete
    return client.v2_post("/batch/projectGroup", body)


# ── kanban columns ───────────────────────────────────────────────────────────


def column_list(client: TickClient, p: ProjectIdPayload) -> list[dict]:
    """List the kanban columns of a project.

    Parameters:
        - project_id (str): A project using the kanban view mode.

    Examples:
        - List columns:
            `tick-proxy do column-list '{"project_id":"6xxx"}'`
            → [{"id":"c1","name":"To Do","sortOrder":0},{"id":"c2","name":"Doing","sortOrder":1}]
        - Empty for a list-mode project:
            `tick-proxy do column-list '{"project_id":"6yyy"}'`
            → []
    """
    return client.v1_get(f"/project/{p.project_id}/data").get("columns") or []


class ColumnManagePayload(BaseModel):
    project_id: str = Field(..., description="Project the columns belong to")
    add: list[dict] | None = Field(None, description='[{"name":"To Do","sortOrder":0}]')
    update: list[dict] | None = Field(None, description='[{"id":"c1","name":"Done"}]')
    delete: list[str] | None = Field(None, description='["c1", …]')


def _require_deleted_columns(client: TickClient, payload: BaseModel) -> None:
    """Ensure a column mutation targets an existing project and requested columns.

    Args:
        client (TickClient): Authenticated V1 client used for the project data read.
        payload (BaseModel): Column mutation request to validate.

    Returns:
        None: Returns normally when the project and every deleted column exist.

    Raises:
        TickProxyError: When a requested deletion identifies no column in the project.

    Examples:
        >>> client = type("C", (), {"v1_get": lambda *_: {"columns": [{"id": "c1"}]}})()
        >>> _require_deleted_columns(client, ColumnManagePayload(project_id="p1", delete=["c1"]))
        >>> _require_deleted_columns(client, ColumnManagePayload(project_id="p1"))
    """
    columns_payload = ColumnManagePayload.model_validate(payload.model_dump())
    data = client.v1_get(f"/project/{columns_payload.project_id}/data")
    existing = {str(column.get("id")) for column in data.get("columns") or []}
    missing = sorted(set(columns_payload.delete or []) - existing)
    if missing:
        raise TickProxyError(
            f"Column not found in project {columns_payload.project_id}: {', '.join(missing)}."
        )


@require_approval()
@require_preflight(
    check=lambda client, payload: _require_deleted_columns(client, payload),
    identity_fields=("project_id", "delete"),
)
def column_manage(client: TickClient, p: ColumnManagePayload) -> dict:
    """Create, rename, or delete kanban columns in one mandatory full-JSON HITL review.

    ⚠️ `projectId` must travel with every entry — without it the V2 endpoint
    answers 200 with an empty `id2etag` and silently drops the update.

    Parameters:
        - project_id (str): The project owning the columns.
        - add (list[dict]|null): List of new columns to add,
          e.g. `[{"name": "To Do", "sortOrder": 0}]`.
        - update (list[dict]|null): List of columns to rename or reorder,
          e.g. `[{"id": "c1", "name": "Done", "sortOrder": 2}]`.
        - delete (list[str]|null): List of column IDs to remove, e.g. `["c1"]`.
          Any deletion targets are preflight-checked for existence inside the project before HITL.

    Examples:
        - Create two columns in a kanban project:
            `tick-proxy do column-manage '{"project_id":"6xxx","add":[{"name":"To Do","sortOrder":0},{"name":"Doing","sortOrder":1}]}'`
            → {"id2etag":{"c1":"abc","c2":"def"},"id2error":{}}

        - Rename a column:
            `tick-proxy do column-manage '{"project_id":"6xxx","update":[{"id":"c1","name":"Backlog"}]}'`
            → {"id2etag":{"c1":"ghi"},"id2error":{}}

        - Delete a column:
            `tick-proxy do column-manage '{"project_id":"6xxx","delete":["c1"]}'`
            → {"id2etag":{},"id2error":{}}

        - Run a combination of column updates and additions:
            `tick-proxy do column-manage '{"project_id":"6xxx","add":[{"name":"QA","sortOrder":2}],"update":[{"id":"c1","name":"Ready"}]}'`
            → {"id2etag":{"c2":"jkl","c1":"mno"},"id2error":{}}

    Note:
        This command uses V2 batch column mutations and always requires HITL.
        If a deletion is specified, a read preflight is triggered to ensure the column
        actually belongs to the specified project; invalid targets fail closed before HITL.
    """
    body: dict[str, Any] = {}
    if p.add:
        body["add"] = [{**c, "projectId": p.project_id} for c in p.add]
    if p.update:
        body["update"] = [{**c, "projectId": p.project_id} for c in p.update]
    if p.delete:
        body["delete"] = [{"id": c, "projectId": p.project_id} for c in p.delete]
    return client.v2_post("/batch/column", body)


ACTIONS = [
    ActionDef("project-list", EmptyPayload, project_list, group="Projects"),
    ActionDef("project-info", ProjectIdPayload, project_info, group="Projects"),
    ActionDef(
        "project-create",
        ProjectCreatePayload,
        project_create,
        group="Projects",
    ),
    ActionDef(
        "project-update",
        ProjectUpdatePayload,
        project_update,
        group="Projects",
    ),
    action_def(
        "project-delete", ProjectIdPayload, project_delete, v2=True, group="Projects"
    ),
    ActionDef("folder-list", EmptyPayload, folder_list, v2=True, group="Folders"),
    action_def(
        "folder-manage", FolderManagePayload, folder_manage, v2=True, group="Folders"
    ),
    ActionDef("column-list", ProjectIdPayload, column_list, group="Columns"),
    action_def(
        "column-manage", ColumnManagePayload, column_manage, v2=True, group="Columns"
    ),
]
