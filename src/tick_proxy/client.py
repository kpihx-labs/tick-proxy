"""
TickClient — the single object every action handler receives.

It owns the HTTP transport and exposes the small set of verbs the handlers use
(`v1_get`, `v1_post`, `v1_delete`, `v2_get`, `v2_post`, `v2_put`, `v2_delete`)
plus the few shared lookups (inbox id, project index) that several domains need.
"""

from typing import Any

from .api.transport import Transport


class TickClient:
    """Facade over the TickTick V1/V2 APIs used by all 52 actions.

    Examples:
        >>> TickClient().v1_get("/project")[0]["name"]
        '🛠️ Tech & Science'
        >>> TickClient().inbox_id()
        'inbox1275839472'
    """

    def __init__(self) -> None:
        self.transport = Transport()
        self._inbox_id: str | None = None

    def close(self) -> None:
        """Release the HTTP connection pool.

        Returns:
            None

        Examples:
            >>> c = TickClient(); c.close()
            >>> c.close()      # idempotent
        """
        self.transport.close()

    # ── V1 verbs ─────────────────────────────────────────────────────────────

    def v1_get(self, endpoint: str, params: dict | None = None) -> Any:
        """GET on the V1 Open API.

        Args:
            endpoint (str): Path, e.g. `/project/6xxx/data`.
            params (dict | None): Query parameters.

        Returns:
            Any: Parsed JSON.

        Examples:
            >>> TickClient().v1_get("/project")[0]["id"]
            '6xxxxxxxxxxxxxxxxxxxxxxx'
            >>> TickClient().v1_get("/project/6xxx/task/68f1")["title"]
            'Buy bread'
        """
        return self.transport.v1("get", endpoint, params=params)

    def v1_post(self, endpoint: str, payload: dict | list | None = None) -> Any:
        """POST on the V1 Open API.

        Args:
            endpoint (str): Path, e.g. `/task`.
            payload (dict | list | None): JSON body.

        Returns:
            Any: Parsed JSON.

        Examples:
            >>> TickClient().v1_post("/task", {"title": "Buy bread"})["id"]
            '68f1a2b3c4d5e6f708192a3b'
            >>> TickClient().v1_post("/project", {"name": "New"})["id"]
            '6yyyyyyyyyyyyyyyyyyyyyyy'
        """
        return self.transport.v1("post", endpoint, payload=payload)

    def v1_delete(self, endpoint: str) -> Any:
        """DELETE on the V1 Open API.

        Args:
            endpoint (str): Path, e.g. `/project/6xxx/task/68f1`.

        Returns:
            Any: `{}` (TickTick answers 200/204 with an empty body).

        Examples:
            >>> TickClient().v1_delete("/project/6xxx/task/68f1")
            {}
            >>> TickClient().v1_delete("/project/6xxx")
            {}
        """
        return self.transport.v1("delete", endpoint)

    # ── V2 verbs ─────────────────────────────────────────────────────────────

    def v2_get(self, endpoint: str, params: dict | None = None) -> Any:
        """GET on the V2 web API.

        Args:
            endpoint (str): Path, e.g. `/tags`.
            params (dict | None): Query parameters.

        Returns:
            Any: Parsed JSON.

        Examples:
            >>> TickClient().v2_get("/tags")[0]["label"]
            'revision'
            >>> TickClient().v2_get("/user/status")["pro"]
            True
        """
        return self.transport.v2("get", endpoint, params=params)

    def v2_post(self, endpoint: str, payload: dict | list | None = None) -> Any:
        """POST on the V2 web API.

        Args:
            endpoint (str): Path, e.g. `/batch/task`.
            payload (dict | list | None): JSON body.

        Returns:
            Any: Parsed JSON — batch endpoints answer `{"id2etag": …, "id2error": …}`.

        Examples:
            >>> TickClient().v2_post("/batch/task", {"update": []})["id2etag"]
            {}
            >>> TickClient().v2_post("/batch/tag", {"add": [{"name": "x"}]})["id2error"]
            {}
        """
        return self.transport.v2("post", endpoint, payload=payload)

    def v2_put(self, endpoint: str, payload: dict | list | None = None) -> Any:
        """PUT on the V2 web API.

        Args:
            endpoint (str): Path.
            payload (dict | list | None): JSON body.

        Returns:
            Any: Parsed JSON.

        Examples:
            >>> TickClient().v2_put("/tag/rename", {"name": "a", "newName": "b"})
            {}
            >>> TickClient().v2_put("/habit/x", {"name": "Read"})["id"]
            '65f1a2b3c4d5e6f708192a3b'
        """
        return self.transport.v2("put", endpoint, payload=payload)

    def v2_delete(self, endpoint: str, params: dict | None = None) -> Any:
        """DELETE on the V2 web API.

        Args:
            endpoint (str): Path.
            params (dict | None): Query parameters (TickTick often passes ids here).

        Returns:
            Any: Parsed JSON or `{}`.

        Examples:
            >>> TickClient().v2_delete("/tag", params={"name": "old"})
            {}
            >>> TickClient().v2_delete("/project/6xxx")
            {}
        """
        return self.transport.v2("delete", endpoint, params=params)

    # ── shared lookups ───────────────────────────────────────────────────────

    def inbox_id(self) -> str:
        """Return the account's Inbox project id (cached for the process).

        Returns:
            str: The inbox project id, e.g. `inbox1275839472`.

        Examples:
            >>> TickClient().inbox_id()
            'inbox1275839472'
            >>> TickClient().inbox_id()      # second call is cached
            'inbox1275839472'
        """
        if self._inbox_id is None:
            status = self.v2_get("/user/status")
            self._inbox_id = str(status.get("inboxId", ""))
        return self._inbox_id

    def projects_index(self) -> dict[str, dict]:
        """Return every project keyed by id (V1 read — no V2 needed).

        Returns:
            dict[str, dict]: `{project_id: project_object}`.

        Examples:
            >>> TickClient().projects_index()["6xxx"]["name"]
            '🛠️ Tech & Science'
            >>> len(TickClient().projects_index())
            14
        """
        projects = self.v1_get("/project") or []
        return {p["id"]: p for p in projects if isinstance(p, dict) and p.get("id")}

    def full_sync(self) -> dict:
        """Return the complete V2 sync payload (projects, tasks, tags, folders).

        Returns:
            dict: The raw `/batch/check/0` response.

        Examples:
            >>> TickClient().full_sync()["syncTaskBean"]["update"][0]["title"]
            'Buy bread'
            >>> sorted(TickClient().full_sync())[:3]
            ['checkPoint', 'inboxId', 'projectGroups']
        """
        return self.v2_get("/batch/check/0")
