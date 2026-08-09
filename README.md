# tick-proxy

TickTick administrative proxy — RPC CLI for tasks, projects, habits, tags, focus and query views.

> **Status:** 🟡 **DESIGN — not implemented yet.** This repository currently holds only the
> architecture contract and the agent context. See `CONTRACT.md` for the full design and the
> list of decisions awaiting validation.

Refonte of [`tick-mcp`](https://github.com/kpihx/tick-mcp) (MCP server, 71 tools) into a non-MCP
CLI built on the exact model of [`tg-proxy`](https://github.com/KpihX/tg-proxy).

---

## Architecture

Single binary with two namespaces:

```bash
tick-proxy admin setup|status|session-refresh   # Admin operations (always JSON)
tick-proxy do <action> [payload|file]           # 52 RPC actions (JSON default)
```

### `tick-proxy admin`

| Command | Description |
|---------|-------------|
| `setup` | Credential setup via HITL web form — writes `~/.config/tick-proxy/.env` |
| `status` | Auth state (masked tokens), expiry, V1/V2 availability, config path |
| `session-refresh` | V2 re-login when the session token is invalid: HITL collects username + password **transiently** (never stored), saves the new token, discards the password |

### `tick-proxy do` — 52 actions

| Domain | Actions |
|--------|---------|
| **Tasks** | `task-create` · `task-update` · `task-complete` · `task-reopen` · `task-delete` · `task-info` · `task-list` · `project-tasks` · `inbox-list` |
| **Batch** | `task-batch-create` · `task-batch-update` · `task-batch-delete` · `task-move` · `task-parent-set` · `subtask-create` |
| **Projects** | `project-list` · `project-info` · `project-create` · `project-update` · `project-delete` |
| **Folders / Columns** | `folder-list` · `folder-manage` · `column-list` · `column-manage` |
| **Tags** | `tag-list` · `tag-create` · `tag-update` · `tag-merge` · `tag-delete` |
| **Habits** | `habit-list` · `habit-section-list` · `habit-create` · `habit-update` · `habit-delete` · `habit-checkin` · `habit-records` |
| **Query** | `workspace-map` · `query-projects` · `query-folders` · `query-tasks` · `query-agenda` |
| **Views** | `view-today` · `view-week` · `view-week-overview` · `view-upcoming` · `view-overdue` |
| **History** | `history-query` |
| **Stats** | `focus-stats` · `user-status` · `user-stats` |
| **Sync** | `sync-full` |
| **Escape hatch** | `raw` |

Full catalog with source-tool mapping, auth level (V1/V2) and HITL requirement: **`CONTRACT.md`**.

---

## Usage

```bash
# Discover everything — the docstrings ARE the documentation
tick-proxy do --help                 # compact overview of all 52 actions
tick-proxy do task-create --help     # full docstring + exact payload schema

# Payload: inline JSON or a file path
tick-proxy do task-create '{"title":"Buy bread","priority":3}'
tick-proxy do task-create ./payload.json

# Meta options
tick-proxy do view-today -f table            # table instead of JSON
tick-proxy do query-tasks ./filter.json -o /tmp/result.json
# (verification is NOT a CLI option — it runs automatically via the @always_verify decorator)
```

### Meta options (`do` only)

| Option | Role |
|--------|------|
| `--output-file <path>` / `-o` | Write the full envelope to a file |
| `--format json\|table` / `-f` | Display format (default: `json`) |
| `--help` / `-h` | Full docstring + payload schema |

> **No `--verify/-V` flag.** Verification is structural — the `@always_verify` decorator on the
> handler runs it automatically. See `CONTRACT.md` → **Verification model**.

---

## Output format

Every response carries a `meta` section:

```json
{
  "meta": {
    "status": "ok",
    "comment": "",
    "edited": false,
    "verification": null
  },
  "data": { }
}
```

`stdout` is **pure JSON** — logs and HITL prompts go to `stderr`, so `tick-proxy do … | jq` always works.

Every execution also autosaves to `/tmp/tick-proxy-autosave/{action}_{timestamp}.json`.

---

## Config

Single `.env` at `~/.config/tick-proxy/.env`, created by `tick-proxy admin setup`:

```env
TICK_API_TOKEN=6f8a1c2e-4b7d-4e9f-8a1b-2c3d4e5f6a7b   # V1 Open API (required)
TICK_SESSION_TOKEN=a1b2c3d4e5f60718293a4b5c6d7e8f90   # V2 web API cookie (optional)
TICK_USERNAME=kapoivha@gmail.com                      # account e-mail (optional) — pre-fills the session-refresh form
```

- **V1** (`TICK_API_TOKEN`) — official Open API: tasks, projects.
- **V2** (`TICK_SESSION_TOKEN`; refreshed via `tick-proxy admin session-refresh` when invalid) —
  unlocks tags, habits, folders, columns, batch operations, focus stats, history and sync.

**The TickTick password is never stored.** It is collected transiently by
`tick-proxy admin session-refresh` (HITL), exchanged for a new session token, then discarded —
exactly like `tg-proxy` never persists credentials. Only the e-mail (`TICK_USERNAME`) may be
kept, and only to pre-fill the refresh form.

Every endpoint, header and timeout has a documented default in `config.py` and is overridable from
the same `.env`. See `.env.example` for the fully commented template.

### Security

```bash
chmod 700 ~/.config/tick-proxy
chmod 600 ~/.config/tick-proxy/.env
```

---

## HITL

Human-in-the-Loop via a local web UI. Destructive and secret-touching operations open a browser
page showing the payload for review, editing, and approval/rejection.

**HITL-required:** `task-delete` · `task-batch-delete` · `project-delete` · `tag-delete` ·
`tag-merge` · `habit-delete` · `folder-manage`/`column-manage` (when the payload
contains `delete`) · `raw` · `admin setup` · `admin session-refresh`.

Everything else (creates, updates, reads, views) runs without prompting.

---

## Install

```bash
uv tool install .              # production
uv tool install --editable .   # development
```

**No Docker.** This project is a local CLI by design.

---

## Development

```bash
make check   # smoke + ruff + py_compile + pyright + pytest
make smoke   # CLI + registry integrity (52 actions, 0 duplicates)
make uv-link # editable install
```

See `Makefile` for the full target list, `AGENTS.md` for the agent working context, and
`CONTRACT.md` for the architecture contract.
