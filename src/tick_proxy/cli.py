"""
tick-proxy CLI — Single binary, two namespaces.

Usage:
    tick-proxy admin setup|status|session-refresh
    tick-proxy do <action> [payload] [--output-file/-o] [--format/-f]

All output in JSON (default) or table format.
Admin is ALWAYS JSON. 'do' defaults to JSON, can switch to table.
Verification is structural (`@require_verification` on the handler) — there is no flag.
"""

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import BaseModel, ValidationError

from . import __version__, admin
from .actions.base import ActionDef, compare
from .actions.registry import REGISTRY, by_group
from .client import TickClient
from .config import ensure_env
from .display import console, print_error, print_json, print_meta, print_table
from .doc import get_compact_help, get_full_help
from .exceptions import TickProxyError
from .hitl import request_approval
from .logger import setup_logging
from .models import Status, Verification, ok, rejected
from .task_documents import (
    DOCUMENT_FIELDS,
    DocumentOperations,
    field_diffs,
    prepare_document_review,
)

AUTOSAVE_DIR = Path("/tmp/tick-proxy-autosave")

app = typer.Typer(
    name="tick-proxy",
    help="TickTick administrative proxy — RPC CLI for tasks, projects, habits and queries.",
    add_completion=False,
)
app_admin = typer.Typer(help="Admin commands: setup, status, session-refresh.")
app_do = typer.Typer(
    help="RPC actions: task-create, task-list, query-tasks, view-today, …",
    add_completion=False,
    add_help_option=False,
)
app.add_typer(app_admin, name="admin")
app.add_typer(app_do, name="do")


# ─── Helpers ───


def parse_payload(payload_str: str | None) -> dict:
    """Convert a JSON string or a file path into a dict.

    Args:
        payload_str (str | None): Inline JSON, or a path to a `.json` file.

    Returns:
        dict: The parsed payload (empty when nothing was given).

    Raises:
        TickProxyError: When the string is neither valid JSON nor an existing file.

    Examples:
        >>> parse_payload('{"title":"Buy bread"}')
        {'title': 'Buy bread'}
        >>> parse_payload('/path/to/k-tick/assets/revision-week.json')
        {'project_names': ['🎓 X/Revision'], 'priorities': [5]}
    """
    if not payload_str:
        return {}
    try:
        return json.loads(payload_str)
    except json.JSONDecodeError:
        path = Path(payload_str)
        if path.exists():
            return json.loads(path.read_text())
        raise TickProxyError(f"Invalid JSON or file not found: {payload_str}") from None


def output_result(result: dict, fmt: str = "json") -> None:
    """Print the envelope in the requested format.

    Args:
        result (dict): The `{"meta": …, "data": …}` envelope.
        fmt (str): `json` (default) or `table`.

    Returns:
        None

    Examples:
        >>> output_result({"meta": {"status": "ok"}, "data": {"id": "68f1"}})
        {"meta": {"status": "ok"}, "data": {"id": "68f1"}}
        >>> output_result({"meta": {"status": "ok"}, "data": []}, "table")
        (Meta table + Data table)
    """
    if fmt == "table":
        console.print("[bold blue]Meta:[/]")
        print_meta(result.get("meta", {}))
        console.print("[bold blue]Data:[/]")
        print_table(result.get("data") or {})
    else:
        print_json(data=result)


def output_rejection(comment: str, edited: bool, fmt: str = "json") -> None:
    """Print the canonical HITL rejection envelope and terminate with code one.

    Args:
        comment (str): Reviewer reason or timeout explanation.
        edited (bool): Whether the reviewer changed the reviewed payload.
        fmt (str): Requested output format; rejected output remains an envelope.

    Returns:
        None: Always raises `typer.Exit(1)` after printing.

    Examples:
        >>> output_rejection("not now", False)  # doctest: +SKIP
        {"meta":{"status":"rejected",...},"data":null}
        >>> output_rejection("timeout", False, "table")  # doctest: +SKIP
        {"meta":{"status":"rejected",...},"data":null}
    """
    output_result(rejected(comment, edited), fmt)
    raise typer.Exit(1)


def _autosave(action: str, result: dict) -> Path:
    """Write the envelope to /tmp/tick-proxy-autosave and return the path.

    Args:
        action (str): The action name, used in the file name.
        result (dict): The envelope to persist.

    Returns:
        Path: The file that was written.

    Examples:
        >>> _autosave("task-create", {"meta": {}, "data": {}})
        PosixPath('/tmp/tick-proxy-autosave/task-create_20260809_112403.json')
        >>> _autosave("view-today", {"meta": {}, "data": []})
        PosixPath('/tmp/tick-proxy-autosave/view-today_20260809_112500.json')
    """
    AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    path = AUTOSAVE_DIR / f"{action}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    return path


def _execute(
    action: ActionDef, payload_raw: str | None, output_file: str | None, fmt: str
) -> None:
    """Run one action end-to-end: validate → HITL → call → verify → print.

    Args:
        action (ActionDef): The registry entry to run.
        payload_raw (str | None): Inline JSON or a file path.
        output_file (str | None): Where to also write the envelope.
        fmt (str): Display format.

    Returns:
        None: Exits 1 on error, rejection or failed verification.

    Examples:
        >>> _execute(REGISTRY["view-today"], None, None, "json")
        {"meta": {...}, "data": {"date": "2026-08-09", "count": 4, ...}}
        >>> _execute(REGISTRY["task-delete"], '{"task_id":"x","project_id":"y"}', None, "json")
        (opens the HITL form, then prints the envelope)
    """
    try:
        ensure_env(require_v2=action.v2)
    except TickProxyError as exc:
        print_error(str(exc))
        sys.exit(1)
    params = parse_payload(payload_raw)

    meta_status, comment, edited = "ok", "", False
    required_checks = tuple(getattr(action.handler, "__verification_checks__", ()))
    task_context: dict[str, dict] | None = None
    client: TickClient | None = None
    preflight_payload: BaseModel | None = None
    preflight = getattr(action.handler, "__preflight_check__", None)
    preflight_identity_fields = tuple(
        getattr(action.handler, "__preflight_identity_fields__", ())
    )
    if preflight is not None:
        try:
            preflight_payload = action.payload(**params) if action.payload else None
        except ValidationError as exc:
            print_error(f"Validation error: {exc}")
            sys.exit(1)
        client = TickClient()
        try:
            preflight(client, preflight_payload)
        except TickProxyError as exc:
            client.close()
            print_error(str(exc))
            sys.exit(1)
    if action.hitl:
        if action.review_mode == "task":
            direct_document_fields = sorted(
                field for field in DOCUMENT_FIELDS if field in params
            )
            if direct_document_fields:
                print_error(
                    "Task document fields must be expressed through operations, not raw "
                    "values: "
                    f"{', '.join(f'{field}_ops' for field in direct_document_fields)} required."
                )
                sys.exit(1)
        prevalidated = None
        if action.payload:
            try:
                prevalidated = action.payload(**params)
            except ValidationError:
                prevalidated = None
        if action.review_mode == "task" and prevalidated is not None:
            original_task: dict = {}
            if action.name == "task-update":
                client = TickClient()
                try:
                    original_task = client.v1_get(
                        f"/project/{prevalidated.project_id}/task/{prevalidated.task_id}"
                    )
                except TickProxyError as exc:
                    client.close()
                    print_error(str(exc))
                    sys.exit(1)
            try:
                document = prepare_document_review(
                    original_task,
                    DocumentOperations.model_validate(prevalidated.model_dump()),
                    require_title=action.name in {"task-create", "subtask-create"},
                )
                materialized = {**prevalidated.model_dump(), **document["proposed"]}
            except ValueError as exc:
                if client is not None:
                    client.close()
                print_error(str(exc))
                sys.exit(1)
            task_context = {
                "original": document["original"],
                "proposed": {
                    field: str(materialized[field] or "") for field in DOCUMENT_FIELDS
                },
                "payload": params,
            }
            response = request_approval(action.name, params, task_context=task_context)
        else:
            response = request_approval(action.name, params)
        if response.status == "rejected":
            if client is not None:
                client.close()
            output_rejection(response.comment, response.edited, fmt)
        if isinstance(response.payload, dict):
            if task_context is not None:
                params = {
                    **params,
                    **response.payload,
                    **task_context["proposed"],
                    **{field: response.payload[field] for field in DOCUMENT_FIELDS},
                }
            else:
                params = response.payload
        if preflight_payload is not None and any(
            params.get(field) != preflight_payload.model_dump().get(field)
            for field in preflight_identity_fields
        ):
            if client is not None:
                client.close()
            print_error(
                "Reviewed payload changed the preflighted target identity: "
                f"{', '.join(preflight_identity_fields)}."
            )
            sys.exit(1)
        meta_status, comment, edited = "approved", response.comment, response.edited

    try:
        validated = action.payload(**params) if action.payload else None
    except ValidationError as exc:
        if client is not None:
            client.close()
        print_error(f"Validation error: {exc}")
        sys.exit(1)

    if client is None:
        client = TickClient()
    try:
        outcome = action.handler(client, validated)
        verification: Verification | None = None
        if isinstance(outcome, tuple):
            data, verification = outcome
        else:
            data = outcome
        if task_context is not None and isinstance(data, dict):
            task_id = str(data.get("id") or params.get("task_id") or "")
            project_id = str(data.get("projectId") or params.get("project_id") or "")
            persisted = client.v1_get(f"/project/{project_id}/task/{task_id}")
            final_document = {
                field: str(persisted.get(field) or "") for field in DOCUMENT_FIELDS
            }
            expected_document: dict[str, str | None] = {
                field: str(params.get(field) or "") for field in DOCUMENT_FIELDS
            }
            final_values: dict[str, str | None] = dict(final_document)
            if "parentId" in required_checks:
                expected_document["parentId"] = params.get("parent_id")
                final_values["parentId"] = persisted.get("parentId")
            verification = compare(
                f"GET /open/v1/project/{project_id}/task/{task_id}",
                expected_document,
                final_values,
            )
            data = {
                **data,
                **final_document,
                "diff": field_diffs(
                    task_context["original"],
                    final_document,
                ),
            }
        if required_checks and verification is None:
            raise TickProxyError(
                f"{action.name} declares @require_verification but returned no proof."
            )
        if verification is not None and set(verification.checked) != set(
            required_checks
        ):
            raise TickProxyError(
                f"{action.name} verification checks do not match its declared policy."
            )
    except TickProxyError as exc:
        print_error(str(exc))
        sys.exit(1)
    finally:
        client.close()

    if verification is not None and isinstance(data, dict):
        data = {**data, "verification": verification.model_dump()}
    result = ok(data, edited=edited, comment=comment, status=meta_status)

    autosave_path = _autosave(action.name, result)
    if output_file:
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"📄 Written to: {out}", file=sys.stderr)
    else:
        print(f"💾 Autosave: {autosave_path}", file=sys.stderr)

    output_result(result, fmt)
    if verification is not None and not verification.ok:
        sys.exit(1)


# ─── Callbacks ───


def _version_callback(value: bool) -> None:
    """Print the version and exit.

    Args:
        value (bool): True when `--version` was passed.

    Returns:
        None

    Examples:
        >>> _version_callback(True)
        tick-proxy v1.0.0
        >>> _version_callback(False)     # no-op
    """
    if value:
        console.print(f"tick-proxy v{__version__}")
        raise typer.Exit()


def _do_help_callback(value: bool = True) -> None:
    """Print the compact catalog of all 52 actions, grouped.

    Args:
        value (bool): True when help was requested.

    Returns:
        None

    Examples:
        >>> _do_help_callback(True)
        (Tasks / Batch / Projects … with one compact docstring per action)
        >>> _do_help_callback(False)     # no-op
    """
    if not value:
        return
    console.print(
        "[bold yellow]For detailed information and examples on a specific"
        " action, run:[/bold yellow]"
    )
    console.print("  [bold]tick-proxy do <action> --help[/bold]\n")
    for group, actions in by_group().items():
        console.print(f"[bold magenta]── {group} ──[/bold magenta]")
        for action in actions:
            console.print(f"[bold cyan]{action.name}[/bold cyan]")
            console.print(get_compact_help(action.handler))
            console.print()
    raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Root callback — configures stderr logging.

    Args:
        version (bool | None): Handled by the eager `--version` callback.

    Returns:
        None

    Examples:
        >>> main(None)
        >>> main(True)      # prints the version and exits
    """
    setup_logging()


# ─── Admin ───


def _run_admin(command: Callable[[], tuple[dict | None, Status, bool, str]]) -> None:
    """Run one admin manager without applying the `do` configuration gate.

    Args:
        command (Callable[[], tuple[dict | None, Status, bool, str]]): Zero-argument
            admin callable returning normalized HITL metadata.

    Returns:
        None: Prints an approved envelope or the shared rejection envelope.

    Examples:
        >>> _run_admin(lambda: ({"status": "ok"}, "approved", False, ""))  # doctest: +SKIP
        {"meta":{"status":"approved",...},"data":{"status":"ok"}}
        >>> _run_admin(lambda: (None, "rejected", False, "not now"))  # doctest: +SKIP
        {"meta":{"status":"rejected",...},"data":null}
    """
    try:
        data, status, edited, comment = command()
    except TickProxyError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from exc
    if status == "rejected":
        output_rejection(comment, edited)
    print_json(data=ok(data, status=status, edited=edited, comment=comment))


@app_admin.command("setup")
def admin_setup() -> None:
    """Configure credentials via the HITL web form (ALWAYS JSON)."""
    _run_admin(admin.setup)


@app_admin.command("status")
def admin_status() -> None:
    """Auth state: masked tokens, expiry and live V1/V2 probes (ALWAYS JSON)."""
    print_json(data=ok(admin.status()))


@app_admin.command("reset")
def admin_reset() -> None:
    """Empty the configuration file (ALWAYS JSON)."""
    _run_admin(admin.reset)


@app_admin.command("purge")
def admin_purge() -> None:
    """Delete the configuration directory and uninstall the CLI (ALWAYS JSON)."""
    _run_admin(admin.purge)


@app_admin.command("session-refresh")
def admin_session_refresh() -> None:
    """Get a fresh V2 session token — password collected transiently (ALWAYS JSON)."""
    _run_admin(admin.session_refresh)


# ─── do ───

OUTPUT_FILE_OPT = typer.Option(
    None, "--output-file", "-o", help="Write the envelope to a file."
)
FORMAT_OPT = typer.Option(
    "json", "--format", "-f", help="Output format: json (default) or table."
)


@app_do.callback(invoke_without_command=True)
def do_main(
    ctx: typer.Context,
    show_help: bool = typer.Option(
        False, "--help", "-h", help="Show help.", hidden=True
    ),
) -> None:
    """`do` callback — prints the catalog when no action is given.

    Args:
        ctx (typer.Context): Typer context.
        show_help (bool): True when `-h/--help` was passed.

    Returns:
        None

    Examples:
        >>> # tick-proxy do            → prints the 52-action catalog
        >>> # tick-proxy do task-create → runs the action
    """
    if show_help or ctx.invoked_subcommand is None:
        _do_help_callback(True)


def _register(action: ActionDef) -> None:
    """Attach one registry entry as a Typer command under `do`.

    Args:
        action (ActionDef): The action to expose.

    Returns:
        None

    Examples:
        >>> _register(REGISTRY["task-create"])    # adds `tick-proxy do task-create`
        >>> _register(REGISTRY["raw"])            # adds `tick-proxy do raw`
    """

    @app_do.command(action.name, help=get_full_help(action.handler))
    def _command(
        payload: str | None = typer.Argument(None, help="JSON payload or file path."),
        output_file: str | None = OUTPUT_FILE_OPT,
        fmt: str = FORMAT_OPT,
    ) -> None:
        try:
            _execute(action, payload, output_file, fmt)
        except TickProxyError as exc:
            print_error(str(exc))
            sys.exit(1)


for _action in REGISTRY.values():
    _register(_action)
