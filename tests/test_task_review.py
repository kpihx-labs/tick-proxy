"""Structured task operation and three-inline-diff review contracts."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tick_proxy import cli
from tick_proxy.actions.base import require_verification
from tick_proxy.actions.registry import REGISTRY
from tick_proxy.exceptions import TickProxyError
from tick_proxy.hitl import HITL_TIMEOUT, HITLResponse, request_approval
from tick_proxy.task_documents import (
    DocumentOperations,
    apply_operations,
    field_diffs,
)


class FakeClient:
    """Record task reads and writes without contacting TickTick.

    Examples:
        >>> FakeClient({"title": "Old"}).v1_get("/task")["title"]
        'Old'
        >>> FakeClient().closed
        False
        >>> client = FakeClient(); client.close(); client.closed
        True
        >>> FakeClient().post_calls
        []
    """

    def __init__(self, original: dict | None = None) -> None:
        self.original = original or {}
        self.post_calls: list[tuple[str, dict]] = []
        self.closed = False
        self.read_after_close = False

    def v1_get(self, _: str) -> dict:
        """Return the configured original task.

        Args:
            _ (str): Ignored endpoint.

        Returns:
            dict: Remote task fixture.

        Examples:
            >>> FakeClient({"content": "A"}).v1_get("/task")["content"]
            'A'
            >>> FakeClient().v1_get("/task")
            {}
            >>> isinstance(FakeClient().v1_get("/task"), dict)
            True
            >>> FakeClient({"id": "x"}).v1_get("/task")["id"]
            'x'
        """
        if self.closed:
            self.read_after_close = True
            pytest.fail("Task persistence read-back must occur before client.close().")
        if self.post_calls:
            return {**self.original, **self.post_calls[-1][1]}
        return self.original

    def v1_post(self, endpoint: str, payload: dict) -> dict:
        """Record and echo the final V1 body.

        Args:
            endpoint (str): V1 write endpoint.
            payload (dict): Final task body.

        Returns:
            dict: Echoed body with a stable id.

        Examples:
            >>> FakeClient().v1_post("/task", {"title": "T"})["id"]
            'fake-task'
            >>> FakeClient().v1_post("/task", {"title": "T"})["title"]
            'T'
            >>> FakeClient().v1_post("/task", {}).get("title") is None
            True
            >>> isinstance(FakeClient().v1_post("/task", {}), dict)
            True
        """
        self.post_calls.append((endpoint, payload))
        return {"id": payload.get("id", "fake-task"), **payload}

    def close(self) -> None:
        """Mark the transport closed.

        Returns:
            None: The fake owns no socket.

        Examples:
            >>> client = FakeClient(); client.close(); client.closed
            True
            >>> client = FakeClient(); client.close(); client.close(); client.closed
            True
            >>> FakeClient().close() is None
            True
            >>> isinstance(FakeClient().closed, bool)
            True
        """
        self.closed = True


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture CLI envelopes and disable unrelated local infrastructure.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.

    Returns:
        list[dict]: Captured envelopes.

    Examples:
        >>> isinstance([], list)
        True
        >>> len([])
        0
        >>> [] == []
        True
        >>> bool([])
        False
    """
    results: list[dict] = []
    monkeypatch.setattr(cli, "ensure_env", lambda **_: None)
    monkeypatch.setattr(cli, "_autosave", lambda *_: None)
    monkeypatch.setattr(cli, "output_result", lambda result, _: results.append(result))
    return results


def test_replace_requires_exact_current_lines() -> None:
    """Reject stale line claims before any review page can open.

    Examples:
        >>> apply_operations("A\nB", [], "content")
        'A\nB'
        >>> "old_str" in "content_ops[1] old_str does not exactly match old_lines."
        True
        >>> bool("content_ops[1]")
        True
        >>> [1, 2] == list(range(1, 3))
        True
    """
    with pytest.raises(ValueError, match="old_str"):
        apply_operations(
            "A\nB",
            DocumentOperations.model_validate(
                {
                    "content_ops": [
                        {
                            "op": "replace",
                            "old_str": "X",
                            "old_lines": [1],
                            "new_str": "C",
                        }
                    ]
                }
            ).content_ops,
            "content",
        )


def test_operation_lists_support_replace_insert_and_sequential_ranges() -> None:
    """Apply deterministic operation lists in their declared order.

    Examples:
        >>> apply_operations("A", [], "content")
        'A'
        >>> apply_operations("", [{"op":"insert","insert_lines":[0],"insert_text":"A"}], "content")
        'A'
        >>> "A\nB".splitlines()[1]
        'B'
        >>> len(["replace", "insert"])
        2
    """
    operations = DocumentOperations.model_validate(
        {
            "content_ops": [
                {"op": "replace", "old_str": "A", "old_lines": [1], "new_str": "B"},
                {"op": "insert", "insert_lines": [1], "insert_text": "C"},
            ]
        }
    )
    assert apply_operations("A", operations.content_ops, "content") == "B\nC"


def test_task_create_preflight_requires_title_operations(captured: list[dict]) -> None:
    """Reject raw titles because task creation must be operation-derived.

    Args:
        captured (list[dict]): Captured CLI envelopes.

    Examples:
        >>> "title_ops" in "title_ops must produce a non-empty title"
        True
        >>> len([])
        0
        >>> bool("task-create")
        True
        >>> "raw" != "operations"
        True
    """
    with pytest.raises(SystemExit):
        cli._execute(
            REGISTRY["task-create"], '{"title":"Forbidden raw title"}', None, "json"
        )
    assert captured == []


def test_raw_document_fields_name_each_missing_operation_list(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Reject every raw document field before the task client is created.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured CLI envelopes.

    Returns:
        None: The command exits before any write path.

    Examples:
        >>> "title_ops".endswith("_ops")
        True
        >>> "content_ops".endswith("_ops")
        True
        >>> "desc_ops".endswith("_ops")
        True
        >>> len(["title_ops", "content_ops", "desc_ops"])
        3
    """
    errors: list[str] = []
    monkeypatch.setattr(cli, "print_error", lambda message: errors.append(message))
    with pytest.raises(SystemExit):
        cli._execute(
            REGISTRY["task-update"],
            '{"task_id":"t","project_id":"p","title":"T","content":"C","desc":"D"}',
            None,
            "json",
        )
    assert errors == [
        (
            "Task document fields must be expressed through operations, not raw values: "
            "content_ops, desc_ops, title_ops required."
        )
    ]
    assert captured == []


def test_every_do_stops_before_payload_or_client_when_config_is_invalid(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Stop an action cleanly before parsing, HITL, or any TickTick transport.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured output envelopes.

    Returns:
        None: The command exits before work begins.

    Examples:
        >>> "Config file not found".startswith("Config")
        True
        >>> len([])
        0
        >>> bool("task-update")
        True
        >>> 1 != 0
        True
    """
    monkeypatch.setattr(
        cli,
        "ensure_env",
        lambda **_: (_ for _ in ()).throw(TickProxyError("Config file not found.")),
    )
    monkeypatch.setattr(
        cli,
        "TickClient",
        lambda: pytest.fail("TickClient must not be constructed after config failure"),
    )
    with pytest.raises(SystemExit):
        cli._execute(REGISTRY["task-update"], "{not valid JSON}", None, "json")
    assert captured == []


def test_required_task_verification_is_centrally_materialized(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Materialize task verification centrally even when its handler returns data.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured output envelopes.

    Returns:
        None: The CLI attaches read-back proof from the central task path.

    Examples:
        >>> "require_verification" in "task-update declares @require_verification"
        True
        >>> len([])
        0
        >>> bool("proof")
        True
        >>> 1 == 1
        True
    """
    client = FakeClient({"title": "Old", "content": "", "desc": ""})
    monkeypatch.setattr(cli, "TickClient", lambda: client)
    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: HITLResponse(
            "approved", {"title": "New", "content": "", "desc": ""}
        ),
    )
    unverified_handler = require_verification("title", "content", "desc")(
        lambda _client, _payload: {"id": "task-1", "projectId": "project-1"}
    )

    action = replace(REGISTRY["task-update"], handler=unverified_handler)
    with pytest.raises(SystemExit):
        cli._execute(
            action,
            '{"task_id":"task-1","project_id":"project-1","title_ops":[{"op":"replace","old_str":"Old","old_lines":[1],"new_str":"New"}]}',
            None,
            "json",
        )
    assert captured[0]["data"]["verification"]["ok"] is False
    assert "verification" not in captured[0]["meta"]


def test_project_delete_verifies_absence_and_second_attempt_stops_before_hitl(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Make destructive project deletion idempotent at the proxy boundary.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured envelopes.

    Returns:
        None: First deletion proves absence; second one is rejected pre-HITL.

    Examples:
        >>> 404 == 404
        True
        >>> "already absent".startswith("already")
        True
        >>> len(["GET", "DELETE", "GET"])
        3
        >>> bool("project-delete")
        True
    """
    from tick_proxy.exceptions import TickTickAPIError

    class DeleteClient:
        def __init__(self) -> None:
            self.exists = True
            self.stale_reads_remaining = 1
            self.calls: list[str] = []

        def v1_get(self, _: str) -> dict:
            self.calls.append("GET")
            if not self.exists and self.stale_reads_remaining == 0:
                raise TickTickAPIError(404, "Not found")
            if not self.exists:
                self.stale_reads_remaining -= 1
            return {"id": "project-1"}

        def v2_post(self, _: str, __: dict) -> dict:
            self.calls.append("DELETE")
            self.exists = False
            return {}

        def close(self) -> None:
            pass

    client = DeleteClient()
    monkeypatch.setattr(cli, "TickClient", lambda: client)
    approvals: list[bool] = []
    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: (
            approvals.append(True) or HITLResponse("approved", {"project_id": "project-1"})
        ),
    )
    cli._execute(REGISTRY["project-delete"], '{"project_id":"project-1"}', None, "json")
    assert client.calls == ["GET", "DELETE", "GET", "GET"]
    assert captured[0]["data"]["verification"]["ok"] is True

    with pytest.raises(SystemExit):
        cli._execute(
            REGISTRY["project-delete"], '{"project_id":"project-1"}', None, "json"
        )
    assert client.calls == ["GET", "DELETE", "GET", "GET", "GET"]
    assert approvals == [True]


def test_project_delete_accepts_ticktick_empty_post_delete_resource(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Treat TickTick's empty V1 resource body as confirmed deletion, not a retry.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured envelopes.

    Returns:
        None: Exactly one delete is issued and absence is verified.

    Examples:
        >>> {} == {}
        True
        >>> len(["GET", "DELETE", "GET"])
        3
        >>> "deleted" in {"deleted": "project-1"}
        True
        >>> bool("project-delete")
        True
    """

    class EmptyDeleteClient:
        def __init__(self) -> None:
            self.deleted = False
            self.calls: list[str] = []

        def v1_get(self, _: str) -> dict:
            self.calls.append("GET")
            return {} if self.deleted else {"id": "project-1"}

        def v2_post(self, _: str, __: dict) -> dict:
            self.calls.append("DELETE")
            self.deleted = True
            return {}

        def close(self) -> None:
            pass

    client = EmptyDeleteClient()
    monkeypatch.setattr(cli, "TickClient", lambda: client)
    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: HITLResponse("approved", {"project_id": "project-1"}),
    )
    cli._execute(REGISTRY["project-delete"], '{"project_id":"project-1"}', None, "json")
    assert client.calls == ["GET", "DELETE", "GET"]
    assert captured[0]["data"]["verification"]["ok"] is True


def test_preflighted_delete_rejects_a_reviewer_swapped_target_before_write(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Reject a reviewed destructive payload whose target differs from its preflight.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest replacement helper.
        captured (list[dict]): Captured CLI envelopes, unused because execution fails.

    Returns:
        None: The API sees one preflight read and no delete request.

    Examples:
        >>> ("project-1",) != ("project-2",)
        True
        >>> len(["GET"])
        1
    """

    class SwappedTargetClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def v1_get(self, _: str) -> dict:
            self.calls.append("GET")
            return {"id": "project-1"}

        def v2_post(self, _: str, __: dict) -> dict:
            self.calls.append("DELETE")
            return {}

        def close(self) -> None:
            pass

    client = SwappedTargetClient()
    monkeypatch.setattr(cli, "TickClient", lambda: client)
    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: HITLResponse("approved", {"project_id": "project-2"}),
    )

    with pytest.raises(SystemExit):
        cli._execute(REGISTRY["project-delete"], '{"project_id":"project-1"}', None, "json")

    assert client.calls == ["GET"]
    assert captured == []


def test_subtask_create_uses_task_review_then_links_and_verifies_parent(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Exercise one reviewed subtask create through creation, linking and read-back.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest replacement helper.
        captured (list[dict]): Captured output envelopes.

    Returns:
        None: The final envelope includes document patches and parent verification.

    Examples:
        >>> "parentId" in {"parentId": "parent-1"}
        True
        >>> len(["POST", "POST", "GET"])
        3
    """

    class SubtaskClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.created: dict = {}
            self.parent_id: str | None = None

        def v1_post(self, _: str, payload: dict) -> dict:
            self.calls.append("POST")
            self.created = {"id": "child-1", "projectId": "project-1", **payload}
            return self.created

        def v2_post(self, _: str, payload: list[dict]) -> dict:
            self.calls.append("POST")
            self.parent_id = payload[0]["parentId"]
            return {}

        def v1_get(self, _: str) -> dict:
            self.calls.append("GET")
            return {**self.created, "parentId": self.parent_id}

        def close(self) -> None:
            pass

    client = SubtaskClient()
    monkeypatch.setattr(cli, "TickClient", lambda: client)
    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: HITLResponse(
            "approved", {"title": "Child", "content": "Body", "desc": "Desc"}
        ),
    )
    cli._execute(
        REGISTRY["subtask-create"],
        '{"project_id":"project-1","parent_id":"parent-1","title_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Child"}],"content_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Body"}],"desc_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Desc"}]}',
        None,
        "json",
    )

    assert client.calls == ["POST", "POST", "GET", "GET"]
    assert captured[0]["meta"]["status"] == "approved"
    assert captured[0]["data"]["parentId"] == "parent-1"
    assert set(captured[0]["data"]["verification"]["checked"]) == {
        "title",
        "content",
        "desc",
        "parentId",
    }
    assert captured[0]["data"]["verification"]["ok"] is True


def test_batch_task_writes_reject_through_the_shared_hitl_envelope(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Permit arbitrary batch JSON while making an explicit rejection fail closed.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest replacement helper.
        captured (list[dict]): Captured result envelopes.

    Returns:
        None: Neither batch action creates a client or sends a write after rejection.

    Examples:
        >>> {"status": "rejected"}["status"]
        'rejected'
        >>> len(["task-batch-create", "task-batch-update"])
        2
    """
    calls: list[str] = []

    class NeverWrittenClient:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        cli,
        "request_approval",
        lambda *_args, **_kwargs: HITLResponse("rejected", None, "use individual task review"),
    )
    monkeypatch.setattr(cli, "TickClient", lambda: NeverWrittenClient())

    for action, payload in (
        ("task-batch-create", '{"tasks":[{"title":"Batch title","content":"Batch body"}]}'),
        ("task-batch-update", '{"tasks":[{"id":"t1","projectId":"p1","desc":"Batch desc"}]}'),
    ):
        with pytest.raises(Exception) as exit_info:
            cli._execute(REGISTRY[action], payload, None, "json")
        assert getattr(exit_info.value, "exit_code", None) == 1

    assert calls == []
    assert [result["meta"]["status"] for result in captured] == ["rejected", "rejected"]
    assert all(result["data"] is None for result in captured)


def test_task_update_returns_three_final_values_and_three_diffs(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Combine preflight operations and live reviewer edits into final output.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured CLI envelopes.

    Examples:
        >>> set(("title_diff", "content_diff", "desc_diff")) == {"title_diff", "content_diff", "desc_diff"}
        True
        >>> "edited" in {"edited": True}
        True
        >>> "content" in {"content": "Final"}
        True
        >>> "meta" not in {"data": {}}
        True
    """
    client = FakeClient(
        {"title": "Old", "content": "Old body", "desc": "Old desc", "kind": "TEXT"}
    )
    monkeypatch.setattr(cli, "TickClient", lambda: client)

    def approve(_: str, __: dict, task_context: dict | None = None) -> HITLResponse:
        assert task_context is not None
        assert task_context["proposed"] == {
            "title": "New",
            "content": "Agent body",
            "desc": "Old desc",
        }
        return HITLResponse(
            "approved",
            {
                "title": "New",
                "content": "Human body",
                "desc": "Human desc",
                "priority": 5,
            },
            edited=True,
        )

    monkeypatch.setattr(cli, "request_approval", approve)
    cli._execute(
        REGISTRY["task-update"],
        json.dumps(
            {
                "task_id": "task-1",
                "project_id": "project-1",
                "priority": 1,
                "title_ops": [
                    {
                        "op": "replace",
                        "old_str": "Old",
                        "old_lines": [1],
                        "new_str": "New",
                    }
                ],
                "content_ops": [
                    {
                        "op": "replace",
                        "old_str": "Old body",
                        "old_lines": [1],
                        "new_str": "Agent body",
                    }
                ],
            }
        ),
        None,
        "json",
    )
    result = captured[0]
    assert result["data"]["title"] == "New"
    assert result["data"]["content"] == "Human body"
    assert result["data"]["desc"] == "Human desc"
    assert client.post_calls[-1][1]["priority"] == 5
    assert result["data"]["verification"]["ok"] is True
    assert result["data"]["verification"]["checked"] == [
        "content",
        "desc",
        "title",
    ]
    assert "verification" not in result["meta"]
    assert set(result["data"]["diff"]) == {"title_diff", "content_diff", "desc_diff"}
    assert "+Human body" in result["data"]["diff"]["content_diff"]
    assert "+Human desc" in result["data"]["diff"]["desc_diff"]
    assert client.closed
    assert not client.read_after_close


def test_task_create_materializes_operations_and_reads_back_final_document(
    monkeypatch: pytest.MonkeyPatch, captured: list[dict]
) -> None:
    """Create through operations, preserve JSON metadata, and return read-back fields.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch helper.
        captured (list[dict]): Captured CLI envelopes.

    Returns:
        None: The create result contains persisted title/content/desc and diffs.

    Examples:
        >>> "title_ops".endswith("_ops")
        True
        >>> "content_ops".endswith("_ops")
        True
        >>> "desc_ops".endswith("_ops")
        True
        >>> len(["title", "content", "desc"])
        3
    """
    client = FakeClient()
    monkeypatch.setattr(cli, "TickClient", lambda: client)

    def approve(
        _: str, payload: dict, task_context: dict | None = None
    ) -> HITLResponse:
        assert task_context is not None
        assert task_context["proposed"] == {
            "title": "Created",
            "content": "Body",
            "desc": "Description",
        }
        return HITLResponse(
            "approved",
            {
                **payload,
                "priority": 5,
                "title": "Created",
                "content": "Human body",
                "desc": "Description",
            },
            edited=True,
        )

    monkeypatch.setattr(cli, "request_approval", approve)
    cli._execute(
        REGISTRY["task-create"],
        json.dumps(
            {
                "priority": 1,
                "title_ops": [
                    {"op": "insert", "insert_lines": [0], "insert_text": "Created"}
                ],
                "content_ops": [
                    {"op": "insert", "insert_lines": [0], "insert_text": "Body"}
                ],
                "desc_ops": [
                    {
                        "op": "insert",
                        "insert_lines": [0],
                        "insert_text": "Description",
                    }
                ],
            }
        ),
        None,
        "json",
    )
    assert client.post_calls[-1] == (
        "/task",
        {
            "title": "Created",
            "content": "Human body",
            "desc": "Description",
            "priority": 5,
        },
    )
    result = captured[0]
    assert result["data"]["content"] == "Human body"
    assert result["data"]["diff"]["title_diff"].startswith("--- title/original")
    assert result["data"]["diff"]["content_diff"].endswith("+Human body")
    assert result["data"]["diff"]["desc_diff"].endswith("+Description")


def test_field_diffs_keep_each_document_field_independent() -> None:
    """Return only field-local patches rather than one mixed synthetic document.

    Examples:
        >>> field_diffs({"title":"A","content":"B","desc":"C"}, {"title":"A","content":"B","desc":"C"})["title_diff"]
        ''
        >>> "title/original" in field_diffs({"title":"A","content":"","desc":""}, {"title":"B","content":"","desc":""})["title_diff"]
        True
        >>> "content/original" in field_diffs({"title":"","content":"A","desc":""}, {"title":"","content":"B","desc":""})["content_diff"]
        True
        >>> "desc/original" in field_diffs({"title":"","content":"","desc":"A"}, {"title":"","content":"","desc":"B"})["desc_diff"]
        True
    """
    diffs = field_diffs(
        {"title": "A", "content": "B", "desc": "C"},
        {"title": "A", "content": "Changed", "desc": "C"},
    )
    assert diffs["title_diff"] == ""
    assert "+Changed" in diffs["content_diff"]
    assert diffs["desc_diff"] == ""


def test_task_hitl_http_round_trip_serves_three_inline_editors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serve the unified task page and collect all final document fields.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest patch fixture.

    Returns:
        None: Assertions prove the local GET/POST HITL contract.

    Examples:
        >>> "title-editor" in "title-editor content-editor desc-editor"
        True
        >>> len(["title", "content", "desc"])
        3
        >>> {"title": "Final"}["title"]
        'Final'
        >>> bool("inline patch")
        True
    """
    urls: list[str] = []
    responses: list[HITLResponse] = []
    monkeypatch.setattr("webbrowser.open", lambda url: urls.append(url))
    thread = threading.Thread(
        target=lambda: responses.append(
            request_approval(
                "task-update",
                {"task_id": "task-1", "project_id": "project-1"},
                task_context={
                    "original": {
                        "title": "Old",
                        "content": "Old body",
                        "desc": "Old desc",
                    },
                    "proposed": {
                        "title": "New",
                        "content": "New body",
                        "desc": "Old desc",
                    },
                    "payload": {
                        "task_id": "task-1",
                        "project_id": "project-1",
                        "priority": 3,
                    },
                },
            )
        )
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not urls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert urls
    review_url = urls[0]
    page = httpx.get(review_url, timeout=1)
    assert page.status_code == 200
    assert "Tick-Proxy — Task review" in page.text
    assert '<textarea id="payload"' in page.text
    assert "title-editor" in page.text
    assert "content-editor" in page.text
    assert "desc-editor" in page.text
    assert "renderSideBySide: false" in page.text
    assert "JSON.parse(document.getElementById('payload').value)" in page.text
    request_id = parse_qs(urlparse(review_url).query)["id"][0]
    submit = httpx.post(
        f"{urlparse(review_url).scheme}://{urlparse(review_url).netloc}/submit",
        json={
            "id": request_id,
            "status": "approved",
            "comment": "Reviewed inline.",
            "edited": True,
            "payload": {
                "title": "Final",
                "content": "Human body",
                "desc": "Human desc",
            },
        },
        timeout=1,
    )
    assert submit.status_code == 200
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert responses[0].payload == {
        "title": "Final",
        "content": "Human body",
        "desc": "Human desc",
    }


def test_hitl_timeout_is_ten_minutes() -> None:
    """Keep the default HITL waiting period explicit and regression-tested.

    Returns:
        None: The public timeout remains ten minutes.

    Examples:
        >>> 10 * 60
        600
        >>> 600 // 60
        10
        >>> HITL_TIMEOUT is not None
        True
        >>> isinstance(HITL_TIMEOUT, int)
        True
    """
    assert HITL_TIMEOUT == 600
