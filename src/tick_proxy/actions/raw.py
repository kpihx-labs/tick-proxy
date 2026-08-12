"""Raw escape hatch — any TickTick endpoint, any method. HITL required."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from .base import action_def, require_approval


class RawPayload(BaseModel):
    api: str = Field("v2", description="v1 | v2")
    method: str = Field("get", description="get | post | put | delete")
    endpoint: str = Field(
        ..., description="Path, e.g. /batch/task or /project/6xxx/data"
    )
    params: dict[str, Any] | None = Field(None, description="Query parameters")
    payload: dict[str, Any] | list[Any] | None = Field(None, description="JSON body")


@require_approval()
def raw(client: TickClient, p: RawPayload) -> Any:
    """Call ANY TickTick endpoint directly — 100 % API coverage. HITL required.

    Whatever the 52 business actions do not cover is reachable here: both APIs,
    every method, arbitrary path, query parameters, and payload body. Because it
    is arbitrary, it always passes through the HITL review form and is never
    automatically verified.

    Parameters:
        - api (str): Chosen API layer: `v1` (Bearer token auth) or `v2` (session
          cookie auth). Defaults to `v2`.
        - method (str): HTTP method: `get` | `post` | `put` | `delete`. Defaults
          to `get`.
        - endpoint (str): Path appended to the selected base URL (must start
          with a slash, e.g. `/project` or `/batch/taskParent`).
        - params (dict|null): Query string parameters appended as key-value
          pairs.
        - payload (dict|list|null): JSON body sent for post/put requests.

    Examples:
        - Read a project's raw data (V1 GET):
            `tick-proxy do raw '{"api":"v1","method":"get","endpoint":"/project/6xxx/data"}'`
            → {"project":{"id":"6xxx","name":"🛠️ Tech & Science"},"tasks":[],"columns":[]}

        - List project groups (V2 GET with query parameters):
            `tick-proxy do raw '{"api":"v2","method":"get","endpoint":"/projectGroup","params":{"showAll":"true"}}'`
            → [{"id":"5aaa","name":"🎓 X","sortOrder":-1099511627776}]

        - Create a custom task (V1 POST):
            `tick-proxy do raw '{"api":"v1","method":"post","endpoint":"/task","payload":{"title":"Custom Task","projectId":"6xxx","content":"Description"}}'`
            → {"id":"6yyy","title":"Custom Task","projectId":"6xxx","content":"Description"}

        - Set task parent relationship (V2 POST batch):
            `tick-proxy do raw '{"api":"v2","method":"post","endpoint":"/batch/taskParent","payload":[{"taskId":"6child","projectId":"6xxx","parentId":"6parent"}]}'`
            → {"id2etag":{"6child":{"parentId":"6parent"}},"id2error":{}}

        - Delete a project group/folder (V2 POST batch delete):
            `tick-proxy do raw '{"api":"v2","method":"post","endpoint":"/batch/projectGroup","payload":{"delete":["5folder"]}}'`
            → {"id2etag":{},"id2error":{}}

        - Delete a task directly (V1 DELETE):
            `tick-proxy do raw '{"api":"v1","method":"delete","endpoint":"/project/6xxx/task/6yyy"}'`
            → {}

    Note:
        Since the raw action is arbitrary and can execute mutations, it always
        requires HITL validation. Run it from a tmux ops pane so you can receive
        and approve the browser review page:
        `tick-proxy do raw ./raw_payload.json`
    """
    call = client.transport.v1 if p.api == "v1" else client.transport.v2
    return call(p.method, p.endpoint, params=p.params, payload=p.payload)  # type: ignore[arg-type]


ACTIONS = [action_def("raw", RawPayload, raw, group="Escape hatch")]
