"""Raw escape hatch — any TickTick endpoint, any method. HITL required."""

from typing import Any

from pydantic import BaseModel, Field

from ..client import TickClient
from .base import ActionDef


class RawPayload(BaseModel):
    api: str = Field("v2", description="v1 | v2")
    method: str = Field("get", description="get | post | put | delete")
    endpoint: str = Field(
        ..., description="Path, e.g. /batch/task or /project/6xxx/data"
    )
    params: dict[str, Any] | None = Field(None, description="Query parameters")
    payload: dict[str, Any] | list[Any] | None = Field(None, description="JSON body")


def raw(client: TickClient, p: RawPayload) -> Any:
    """Call ANY TickTick endpoint directly — 100 % API coverage. HITL required.

    Whatever the 52 business actions do not cover is reachable here: both APIs,
    every method, arbitrary path, params and body. Because it is arbitrary, it
    always passes through the HITL review form and is never auto-verified.

    Parameters:
        - api (str): `v1` (Bearer token) or `v2` (session cookie).
        - method (str): get | post | put | delete.
        - endpoint (str): Path appended to the chosen base URL.
        - params (dict|null): Query string parameters.
        - payload (dict|list|null): JSON body for post/put.

    Examples:
        - Read a project's raw data through V1:
            `tick-proxy do raw '{"api":"v1","method":"get","endpoint":"/project/6xxx/data"}'`
            → {"project":{"id":"6xxx","name":"🛠️ Tech & Science"},"tasks":[],"columns":[]}
        - Hit an uncovered V2 batch endpoint:
            `tick-proxy do raw '{"api":"v2","method":"post","endpoint":"/batch/task","payload":{"update":[]}}'`
            → {"id2etag":{},"id2error":{}}
    """
    call = client.transport.v1 if p.api == "v1" else client.transport.v2
    return call(p.method, p.endpoint, params=p.params, payload=p.payload)  # type: ignore[arg-type]


ACTIONS = [
    ActionDef("raw", RawPayload, raw, hitl=True, group="Escape hatch"),
]
