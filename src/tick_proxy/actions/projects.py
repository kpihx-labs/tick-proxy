"""Project, folder and kanban-column actions."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from ..models import Verification
from .base import ActionDef, always_verify, compare

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


@always_verify("groupId")
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


@always_verify("groupId")
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


def project_delete(client: TickClient, p: ProjectIdPayload) -> dict:
    """Delete a project AND all its tasks. IRREVERSIBLE — HITL required.

    Parameters:
        - project_id (str): The project to delete.

    Examples:
        - Delete a project (opens the HITL review form):
            `tick-proxy do project-delete '{"project_id":"6zzz"}'`
            → {"deleted":"6zzz"}
        - Delete a scratch project:
            `tick-proxy do project-delete '{"project_id":"6www"}'`
            → {"deleted":"6www"}
    """
    client.v1_delete(f"/project/{p.project_id}")
    return {"deleted": p.project_id}


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


def folder_manage(client: TickClient, p: FolderManagePayload) -> dict:
    """Create / rename / delete folders in one batch. HITL when `delete` is present.

    Parameters:
        - add (list[dict]|null): `[{"name": "Work"}]`.
        - update (list[dict]|null): `[{"id": "5aaa", "name": "New name"}]`.
        - delete (list[str]|null): `["5aaa"]` — triggers the HITL review.

    Examples:
        - Create a folder:
            `tick-proxy do folder-manage '{"add":[{"name":"Work"}]}'`
            → {"id2etag":{"5bbb":"abc"},"id2error":{}}
        - Rename a folder:
            `tick-proxy do folder-manage '{"update":[{"id":"5aaa","name":"🎓 X"}]}'`
            → {"id2etag":{"5aaa":"def"},"id2error":{}}
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


def column_manage(client: TickClient, p: ColumnManagePayload) -> dict:
    """Create / rename / delete kanban columns. HITL when `delete` is present.

    ⚠️ `projectId` must travel with every entry — without it the V2 endpoint
    answers 200 with an empty `id2etag` and silently drops the update.

    Parameters:
        - project_id (str): The project owning the columns.
        - add (list[dict]|null): `[{"name": "To Do", "sortOrder": 0}]`.
        - update (list[dict]|null): `[{"id": "c1", "name": "Done"}]`.
        - delete (list[str]|null): `["c1"]` — triggers the HITL review.

    Examples:
        - Create two columns:
            `tick-proxy do column-manage '{"project_id":"6xxx","add":[{"name":"To Do","sortOrder":0},{"name":"Doing","sortOrder":1}]}'`
            → {"id2etag":{"c1":"abc","c2":"def"},"id2error":{}}
        - Rename one:
            `tick-proxy do column-manage '{"project_id":"6xxx","update":[{"id":"c1","name":"Backlog"}]}'`
            → {"id2etag":{"c1":"ghi"},"id2error":{}}
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
        verify="always",
        group="Projects",
    ),
    ActionDef(
        "project-update",
        ProjectUpdatePayload,
        project_update,
        verify="always",
        group="Projects",
    ),
    ActionDef(
        "project-delete", ProjectIdPayload, project_delete, hitl=True, group="Projects"
    ),
    ActionDef("folder-list", EmptyPayload, folder_list, v2=True, group="Folders"),
    ActionDef(
        "folder-manage", FolderManagePayload, folder_manage, v2=True, group="Folders"
    ),
    ActionDef("column-list", ProjectIdPayload, column_list, group="Columns"),
    ActionDef(
        "column-manage", ColumnManagePayload, column_manage, v2=True, group="Columns"
    ),
]
