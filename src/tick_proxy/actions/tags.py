"""Tag actions — list, create, update (incl. rename), merge, delete."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from .base import ActionDef


class EmptyPayload(BaseModel):
    pass


def tag_list(client: TickClient, p: EmptyPayload) -> list[dict]:
    """List every tag with its label, color, parent and sort type.

    Parameters:
        - (no payload)

    Examples:
        - List tags:
            `tick-proxy do tag-list`
            → [{"name":"revision","label":"revision","color":"#FF6B6B","parent":null}]
        - Table view:
            `tick-proxy do tag-list -f table`
            → [{"name":"revision","label":"revision"}]
    """
    return client.v2_get("/tags")


class TagCreatePayload(BaseModel):
    name: str = Field(..., description="Tag name / label")
    color: str | None = Field(None, description="Hex color, e.g. #FF6B6B")
    parent: str | None = Field(None, description="Parent tag name (nesting)")
    sort_type: str | None = Field(None, description="project|dueDate|title|priority")


def tag_create(client: TickClient, p: TagCreatePayload) -> dict:
    """Create a tag.

    Parameters:
        - name (str): Tag name. color (str|null): hex color.
        - parent (str|null): Parent tag name for nesting.
        - sort_type (str|null): project | dueDate | title | priority.

    Examples:
        - Simple tag:
            `tick-proxy do tag-create '{"name":"revision"}'`
            → {"id2etag":{"revision":"abc"},"id2error":{}}
        - Nested colored tag:
            `tick-proxy do tag-create '{"name":"exam","parent":"revision","color":"#FF6B6B"}'`
            → {"id2etag":{"exam":"def"},"id2error":{}}
    """
    tag: dict[str, Any] = {"name": p.name.lower(), "label": p.name}
    if p.color:
        tag["color"] = p.color
    if p.parent:
        tag["parent"] = p.parent.lower()
    if p.sort_type:
        tag["sortType"] = p.sort_type
    return client.v2_post("/batch/tag", {"add": [tag]})


class TagUpdatePayload(BaseModel):
    name: str = Field(..., description="Current tag name (from tag-list)")
    new_name: str | None = Field(None, description="New name — renames the tag")
    color: str | None = Field(None, description="Hex color")
    parent: str | None = Field(None, description='Parent tag; "" removes it')
    sort_type: str | None = Field(None, description="project|dueDate|title|priority")


def tag_update(client: TickClient, p: TagUpdatePayload) -> dict:
    """Update a tag — color, parent, sort type, and/or its name.

    Passing `new_name` renames the tag and cascades to every task carrying it
    (this replaces a separate rename action).

    Parameters:
        - name (str): Current tag name.
        - new_name (str|null): New name — performs the rename.
        - color (str|null), parent (str|null, "" clears), sort_type (str|null).

    Examples:
        - Recolor a tag:
            `tick-proxy do tag-update '{"name":"revision","color":"#4DB6AC"}'`
            → {"id2etag":{"revision":"abc"},"id2error":{}}
        - Rename a tag (cascades to all tasks):
            `tick-proxy do tag-update '{"name":"revision","new_name":"revisions"}'`
            → {"renamed":{"from":"revision","to":"revisions"}}
    """
    if p.new_name:
        client.v2_put(
            "/tag/rename", {"name": p.name.lower(), "newName": p.new_name.lower()}
        )
        return {"renamed": {"from": p.name, "to": p.new_name}}
    tag: dict[str, Any] = {"name": p.name.lower(), "label": p.name}
    if p.color:
        tag["color"] = p.color
    if p.parent is not None:
        tag["parent"] = p.parent.lower() or None
    if p.sort_type:
        tag["sortType"] = p.sort_type
    return client.v2_post("/batch/tag", {"update": [tag]})


class TagMergePayload(BaseModel):
    source: str = Field(..., description="Tag merged FROM (deleted)")
    target: str = Field(..., description="Tag merged INTO (kept)")


def tag_merge(client: TickClient, p: TagMergePayload) -> dict:
    """Merge one tag into another — the source tag is DELETED. HITL required.

    Parameters:
        - source (str): Tag to merge from (will disappear).
        - target (str): Tag to merge into (kept).

    Examples:
        - Merge a duplicate:
            `tick-proxy do tag-merge '{"source":"revisions","target":"revision"}'`
            → {"merged":{"from":"revisions","into":"revision"}}
        - Consolidate two work tags:
            `tick-proxy do tag-merge '{"source":"job","target":"work"}'`
            → {"merged":{"from":"job","into":"work"}}
    """
    client.v2_put("/tag/merge", {"name": p.source.lower(), "newName": p.target.lower()})
    return {"merged": {"from": p.source, "into": p.target}}


class TagDeletePayload(BaseModel):
    name: str = Field(..., description="Tag name to delete")


def tag_delete(client: TickClient, p: TagDeletePayload) -> dict:
    """Delete a tag and remove it from every task. IRREVERSIBLE — HITL required.

    Parameters:
        - name (str): Tag name to delete.

    Examples:
        - Delete an obsolete tag:
            `tick-proxy do tag-delete '{"name":"old-sprint"}'`
            → {"deleted":"old-sprint"}
        - Delete a test tag:
            `tick-proxy do tag-delete '{"name":"tmp"}'`
            → {"deleted":"tmp"}
    """
    client.v2_delete("/tag", params={"name": p.name.lower()})
    return {"deleted": p.name}


ACTIONS = [
    ActionDef("tag-list", EmptyPayload, tag_list, v2=True, group="Tags"),
    ActionDef("tag-create", TagCreatePayload, tag_create, v2=True, group="Tags"),
    ActionDef("tag-update", TagUpdatePayload, tag_update, v2=True, group="Tags"),
    ActionDef(
        "tag-merge", TagMergePayload, tag_merge, hitl=True, v2=True, group="Tags"
    ),
    ActionDef(
        "tag-delete", TagDeletePayload, tag_delete, hitl=True, v2=True, group="Tags"
    ),
]
