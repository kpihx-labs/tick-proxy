# tick-proxy — Agent Context

## Project

Non-MCP CLI proxy for TickTick. The full `tick-mcp` catalog (71 MCP tools) refactored into
**52 flat `do` actions**, built on the exact ADN of `tg-proxy` (`$HOME/KpihX-Labs/tg_proxy`):
single binary, `do` + `admin` namespaces, `meta`+`data` envelope, docstring-driven `--help`,
HITL web UI, autosave. **No Docker, no MCP transport, no daemon.**

**Design reference: `CONTRACT.md` — read it before touching anything.**

> **Status:** 🟡 DESIGN. Only the Markdown skeleton exists. No source code, no `pyproject.toml`,
> no git repo yet — the 11 decisions in `CONTRACT.md` → *Decisions requiring KπX validation*
> must be validated first.

## Overview

```bash
tick-proxy do <action> [payload|file] [-o path] [-f json|table]   # 52 RPC actions
tick-proxy admin setup|status|session-refresh                     # ALWAYS JSON
```

## Key Files (once implemented)

| File | Role |
|------|------|
| `src/tick_proxy/cli.py` | ONE Typer app: `do` + `admin` sub-typers, built **from the registry** |
| `src/tick_proxy/client.py` | `TickClient` — auth state + `v1_*` / `v2_*` request verbs |
| `src/tick_proxy/config.py` | `~/.config/tick-proxy/.env` loader + documented endpoint defaults |
| `src/tick_proxy/models.py` | SHARED types only: `Output`, `OutputMeta`, `Priority`, `TickTickAPIError` |
| `src/tick_proxy/doc.py` | Dynamic `--help` injection from docstrings |
| `src/tick_proxy/display.py` | Rich output helpers (`print_json`, `print_table`) |
| `src/tick_proxy/logger.py` | stderr logger — systemd/journald captures (tg-proxy ADN, no file) |
| `src/tick_proxy/exceptions.py` | `TickProxyError` |
| `src/tick_proxy/hitl.py` | HITL web UI (free port, browser auto-open) |
| `src/tick_proxy/api/` | Low-level V1/V2 HTTP transport + endpoint wrappers |
| `src/tick_proxy/actions/` | The 52 actions: `ActionDef` + colocated Pydantic payload + handler |
| `src/tick_proxy/actions/registry.py` | `name → ActionDef` map; duplicates raise at import |
| `src/tick_proxy/query.py` | The filter engine (flat — no `services/` package) |
| `src/tick_proxy/admin.py` | Single source of truth for admin logic (setup, status, session-refresh) |
| `CONTRACT.md` | Architecture contract + full 52-action catalog |

## Key Rules

- **stdout is pure JSON.** Logs, HITL prompts, progress → **stderr**. `tick-proxy do … | jq` must never break.
- **Never write secrets into the repo.** The only secret location is `~/.config/tick-proxy/.env` (chmod 600).
  `tick-mcp`'s in-package `src/tick_mcp/.env` is an anti-pattern that must not be reproduced.
- **Adding an action = adding ONE `ActionDef`.** Never register a command directly in `cli.py`.
- **The docstring IS the documentation.** Mandatory sections: description, `Parameters:`, `Examples:`
  with real `→` outputs. `doc.py` renders them into `--help`; there is no second doc surface.
- **Envelope always.** `{"meta":{"status","comment","edited","verification"},"data":…}` — errors exit 1,
  admin misuse (`--format`/`-o`) exits 2.
- **Verification is not optional for the 4 silent-failure ops** — `task-parent-set`,
  `project-create`/`project-update` with `group_id`, `habit-update`, `task-move`. It is enforced by
  the **`@always_verify` decorator** on the handler — no flag, no bypass. There is **no `verified`
  field in `meta`**: the presence of a non-empty `meta.verification` means verified, `{}`/absent
  means not verified.
- **No Docker, ever** in this repo (explicit KπX decision — the `tg-proxy` Docker layer is untested).

## TickTick API gotchas (silent failures — no error, data simply not saved)

| Operation | Gotcha | Correct approach |
|-----------|--------|------------------|
| Create subtask | `parentId` silently ignored at creation (V1 **and** V2 batch) | `task-create` → `task-parent-set` (or `subtask-create`) |
| Assign project folder | `groupId` silently ignored by V1; V1 reads always return `null` | V2 `batch/project` follow-up, verify via `sync-full` |
| Update habit | V2 `/habits/batch` is a **full replacement** | read-modify-write, never a partial payload |
| Move tasks with children | `move_tasks` does **not** cascade | fetch `childIds`, move parent + children in one batch |
| Reminders on V2-created tasks | V1 needs `dueDate` as the trigger anchor; without it `reminder_minutes` is dropped | always pass `due_date` + `time_zone` alongside `reminder_minutes` |

## Commands (once implemented)

```bash
make check        # smoke + ruff check --fix + ruff format + py_compile + pyright + pytest
make smoke        # tick-proxy do --help + registry integrity (52 actions, 0 duplicates)
make uv-link      # editable install (dev)
make uv-install   # uv tool install . --force
make git-push     # push to github + gitlab
make release      # check → git-push → uv-publish
```

## Reference implementations

| Repo | Role |
|------|------|
| `$HOME/KpihX-Labs/tg_proxy/` | **ADN source** — CLI shape, `doc.py`, `hitl.py`, envelope, autosave, Makefile, CI |
| `$HOME/Work/AI/MCPs/tick_mcp/` | **Content source** — 71 tools, query engine, V1/V2 transport, gotcha handling. Keep as reference until parity, then archive. |

## Evolution Rules

- New feature → update `TODO.md` first, propose before acting.
- Significant change → update `CONTRACT.md` + `AGENTS.md` + `README.md` + `CHANGELOG.md`.
- Breaking change → bump version in `pyproject.toml` + `CHANGELOG.md` entry.
- Destructive / architectural → **stop and confirm with KπX first**.
- `sudo` required → tmux ops pane, never a raw `sudo` in an agent shell.
- **Makefile is the standard task runner** — `make check`, `make push`, `make release`.
