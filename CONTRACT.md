# tick-proxy — Architecture Contract

> **Status:** 🟢 **IMPLEMENTED — 52 actions.** This document is the authoritative architecture
> contract for `tick-proxy`, the non-MCP TickTick CLI built on the ADN of `tg-proxy`
> (`$HOME/KpihX-Labs/tg_proxy`).

---

## Mission

Total refonte of the MCP `tick-mcp` (`$HOME/Work/AI/MCPs/tick_mcp`) into a non-MCP CLI proxy that
follows **exactly** the `tg-proxy` model (`$HOME/KpihX-Labs/tg_proxy`):

- **Single binary, two namespaces** — `tick-proxy do <action>` (RPC) + `tick-proxy admin <action>` (always JSON)
- **Flat kebab-case actions** — ONE level after `do`, pure JSON-RPC, payload inline or file
- **`meta` + `data` envelope** — every response, always
- **Docstring-driven `--help`** — the docstring IS the documentation (single source of truth)
- **HITL web UI** — destructive and secret-touching operations require human approval
- **Autosave** — every `do` execution snapshots to `/tmp/tick-proxy-autosave/`
- **Python + uv + Typer + Pydantic + Rich + httpx** — same stack as `tg-proxy`
- **NO Docker** — explicitly excluded (the `tg-proxy` Docker layer is untested/non-functional)

**Location:** `$HOME/KpihX-Labs/tick_proxy/` — sibling of `tg_proxy/`.

---

## Mantras

- **0 Hardcoding · 100% Flexibility** — no hardcoded API URLs in logic, no in-repo `.env`,
  every endpoint/header/timeout overridable from `~/.config/tick-proxy/.env`.
- **0 Magic · 100% Transparency** — every HTTP call is explicit; V1 vs V2 is stated per action;
   read-back verification is a structural decorator (`@require_verification`), never a hidden retry loop.
- **0 Trust · 100% Control** — secrets live only in `~/.config/tick-proxy/.env` (never in the repo);
  destructive actions preflight then pass through HITL with their identities locked; `raw` gives full
  escape-hatch access with approval.
- **Preflighted destructive review** — deletions and destructive merges read their declared targets
  before HITL and lock their identity fields. The approval payload cannot redirect the write to a
  different resource; absent targets fail without opening a review page.

---

## Design — Single Binary, Namespaced CLI

```
tick-proxy
   │
   ├── admin <action>                       # ALWAYS JSON — auth + config lifecycle
   │   ├── setup                            # HITL web form → writes ~/.config/tick-proxy/.env
   │   ├── status                           # auth state, masked tokens, expiry, V1/V2 availability
   │   ├── session-refresh                  # V2 re-login (device/MFA code via HITL) → new session token
   │
   └── do <action> [payload|file] [--output-file/-o] [--format/-f] [--help/-h]
                                            # RPC — 52 flat actions, JSON payload (inline or file)
```

### `tick-proxy admin` — Admin (ALWAYS JSON to stdout — hardcoded, no `--format`)

| Command | Role | Output | HITL | Backend |
|---------|------|--------|:----:|---------|
| `tick-proxy admin setup` | First-time / any-time credential setup — one web form for the **three persisted fields** (API token, session token, username) | JSON (final) | ✅ | local file write |
| `tick-proxy admin status` | Auth state: which secrets are present (masked), token expiry, V1 reachable?, V2 reachable?, config path | JSON | ❌ | V1 + V2 probe |
| `tick-proxy admin session-refresh` | V2 re-login when the session token is invalid: HITL collects **username + password transiently** (never stored), `POST /user/signon` → new session token saved, password discarded | JSON | ✅ | V2 |

**`admin setup` replaces 8 `tick-admin` subcommands.** The old surface
(`api set|unset`, `session set|unset`, `user set|unset`, `pass set|unset`) collapses into ONE
HITL web form with **three persisted fields**: `TICK_API_TOKEN`, `TICK_SESSION_TOKEN`,
`TICK_EMAIL`. Semantics are explicit, not magic:

| Form state | Effect on `.env` |
|------------|------------------|
| Field left **unchanged** | key preserved as-is |
| Field **filled** | key overwritten with the new value |
| Field **emptied + `clear` checkbox ticked** | key removed from `.env` |

**Auth & password policy — the TickTick password is NEVER stored.** `.env` holds at most
`TICK_API_TOKEN` (required), `TICK_SESSION_TOKEN` (optional) and `TICK_EMAIL` (optional — the
account e-mail, kept only to pre-fill the refresh form). The password exists **only** inside the
`admin session-refresh` HITL form, is sent to TickTick for exactly one `POST /user/signon`, and is
discarded immediately after — never written to disk, never echoed, never autosaved. Credentials
(username + password) are requested **only when the session token is no longer valid**; if a valid
session token exists, nothing is asked.

**Admin never accepts `--format` or `--output-file`** — passing either exits **2** with an error envelope.

### `admin` walkthroughs — what really happens

**`tick-proxy admin setup`**
1. HITL web server starts on an OS-assigned free port; the browser opens the form.
2. The form shows the three fields above. `TICK_API_TOKEN` is required; the other two are optional
   and pre-filled from the current `.env` when present.
3. On submit, `config.py` writes each filled field to `~/.config/tick-proxy/.env` (`chmod 600`);
   untouched fields keep their existing value; `clear`-checked fields are removed.
4. Exit 0 with `{"meta":{"status":"ok"},"data":{"config":"~/.config/tick-proxy/.env","fields":["TICK_API_TOKEN","TICK_SESSION_TOKEN","TICK_EMAIL"]}}`.
   No password field exists in this form — `setup` never touches credentials.

**`tick-proxy admin status`** (read-only, no HITL, always JSON)
1. Reads `.env` and reports which keys are present — **masked** (`TICK_API_TOKEN=6f8a…a7b`), never
   the full value.
2. Reports the recorded `TICK_SESSION_TOKEN_OBTAINED_AT` / `_EXPIRES_AT` timestamps.
3. Probes V1 (`GET /open/v1/project`) → `v1_reachable: true|false`; probes V2 (session token) →
   `v2_reachable: true|false` and `v2_token_valid: true|false`.
4. Prints the config path. Exit 0. Never asks for anything.

**`tick-proxy admin session-refresh`** (HITL — the ONLY place the password is seen)
1. Triggered when the session token is missing, expired (401) or manually.
2. HITL form asks for username (pre-filled from `TICK_EMAIL`) and password (`type=password`).
3. `POST /api/v2/user/signon?wc=true&remember=true` with `{"username","password"}`.
4. On success → the `token` is written to `.env` as `TICK_SESSION_TOKEN` (+ timestamps) and the
   password object is **deleted from memory** before any further work.
5. If the response contains `authId` (device check / 2FA) → a second HITL step collects the
    verification code, calls `/api/v2/user/sign/mfa/code/verify` with `x-verify-id`, then stores
    the final token. A long-lived challenge requests confirmation that the operator clicked the
    TickTick email device-approval link before one sign-on retry; neither `authId` nor credentials
    are persisted or shown in the HITL payload.
6. Exit 0 with the masked new token. Under no circumstance is the password persisted.

### `tick-proxy do` — RPC Actions (JSON default, table via `--format/-f`)

**Meta options (ONLY for `do`, every `--` has its `-`):**

| Option | Role |
|--------|------|
| `--output-file <path>` / `-o <path>` | Write the full envelope to a file (path required) |
| `--format json\|table` / `-f json\|table` | Display format (default: `json`) |
| `--help` / `-h` | Full docstring + Pydantic payload schema for that action |
| *(positional)* `payload` | Inline JSON `'{"k":"v"}'` **or** a file path `./payload.json` |

> **No `--verify/-V` flag.** Verification is NOT a CLI option — it is a **structural decorator**
> (`@require_verification`) baked into the handler of the actions that need it. It cannot be forgotten or
> bypassed: `cli.py` has no way to skip it. See **Verification model**.

**Output envelope — EVERY response (verification appears only in `data` when required):**

```json
{
  "meta": {
    "status": "ok",
    "comment": "",
    "edited": false
  },
  "data": { }
}
```

| `meta` field | Values | Meaning |
|--------------|--------|---------|
| `status` | `ok` · `approved` · `rejected` · `error` | `approved`/`rejected` only when HITL was involved |
| `comment` | free text | the HITL reviewer's comment (empty if none) |
| `edited` | `true` · `false` | the HITL reviewer modified the payload before approving |
| `verification` | — | never present in `meta` |

> **No `verified` boolean and no `meta.verification`.** An action that declares
> `@require_verification(...)` adds one `data.verification` comparison block; actions without the
> decorator have no verification field at all.

**Pre-check (ALL `do` commands):** `~/.config/tick-proxy/.env` must exist and expose at least
`TICK_API_TOKEN` (V1). V2-only actions additionally require V2 auth and fail with an explicit hint
to run `tick-proxy admin setup`. Checked **once** at the start of any `do` command.

**Autosave:** every `do` execution writes `/tmp/tick-proxy-autosave/{action}_{YYYYmmdd_HHMMSS}.json`.
When `-o` is given, the file path is printed instead of the autosave path (both are always written).

---

## Actions — FLAT, ONE level after `do` (52 actions)

Naming convention (inherited from `tg-proxy`: `bot-list`, `chat-read`, `folder-set`):
**`<domain>-<verb>`, kebab-case, domain FIRST.** All `tick-mcp` `verb_noun` names are flipped.

### Tasks — write (5)

| Action | Source tool (`tick-mcp`) | Auth | HITL | Notes |
|--------|--------------------------|:----:|:----:|-------|
| `task-create` | `create_task` | V1 | ✅ | mandatory approval; all kinds use full JSON plus three document patches |
| `task-update` | `update_task` | V1 | ✅ | mandatory approval; all kinds use the same document review; **always pass `due_date` + `time_zone` with `reminder_minutes`** (anchor gotcha) |
| `task-complete` | `complete_task` | V1 | ❌ | `{"project_id":"...","task_id":"..."}` |
| `task-reopen` | `reopen_task` | V1 | ❌ | status → 0 |
| `task-delete` | `delete_task` | V1 | ✅ | irreversible |

### Tasks — read (3)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `task-info` | `get_task_detail` | V1 | ❌ | full detail: checklist, reminders, recurrence, tags |
| `project-tasks` | `get_project_tasks` | V1 | ❌ | tasks of one project (+ kanban columns) |
| `inbox-list` | `get_inbox` | V1 | ❌ | Inbox tasks |

> The cross-project read (`task-list`, the counterpart of `tg-proxy`'s `bot-list`) is backed by the
> V2 sync endpoint and is therefore listed once, under **Sync**.

### Tasks — batch (5)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `task-batch-create` | `batch_create_tasks` | V2 | ✅ | one full-JSON review; `parentId` silently ignored → use `task-parent-set` |
| `task-batch-update` | `batch_update_tasks` | V2 | ✅ | one full-JSON review; ⚠️ cannot set reminders reliably — use `task-update` (V1) |
| `task-batch-delete` | `batch_delete_tasks` | V2 | ✅ | irreversible |
| `task-move` | `move_tasks` + `verified_move_tasks` + `verified_batch_move` | V2 | ❌ | **always cascades to children** (orphan trap) |
| `task-parent-set` | `set_subtask_parent` + `verified_set_subtask_parent` | V2 | ❌ | **verification always on** (silent-failure op) |

### Subtasks (1)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `subtask-create` | `create_subtask` | V1 + V2 | ❌ | genuine composite: `task-create` → `task-parent-set` → verify |

### Sync (2)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `task-list` | `get_all_tasks` | V2 | ❌ | flat list of all active tasks |
| `sync-full` | `full_sync` | V2 | ❌ | projects + tasks + tags + folders in ONE call |

### Projects (5)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `project-list` | `list_projects` | V1 | ❌ | ⚠️ V1 always returns `groupId: null` — use `sync-full` |
| `project-info` | `get_project_detail` | V1 | ❌ | |
| `project-create` | `create_project` + `verified_create_project` | V1 (+V2 if `group_id`) | ❌ | `groupId` needs a V2 follow-up (handled internally) |
| `project-update` | `update_project` + `verified_assign_project_folder` | V1 (+V2 if `group_id`) | ❌ | **verification always on** when `group_id` is set |
| `project-delete` | `delete_project` | V1 | ✅ | pre-read → one delete → bounded absence poll (`404` or `{}`); deletes the project **and all its tasks** |

### Folders (2)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `folder-list` | `list_project_folders` | V2 | ❌ | |
| `folder-manage` | `manage_project_folders` | V2 | ⚠️ | batch add/update/delete — HITL **only when `delete` is present** |

### Kanban columns (2)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `column-list` | `list_columns` | V2 | ❌ | |
| `column-manage` | `manage_columns` | V2 | ⚠️ | HITL **only when `delete` is present** |

### Tags (5)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `tag-list` | `list_tags` | V2 | ❌ | |
| `tag-create` | `create_tag` | V2 | ❌ | |
| `tag-update` | `update_tag` + `rename_tag` | V2 | ❌ | color / parent / sort **and `name`** — passing `name` renames the tag and cascades to all tasks (merged: no separate `tag-rename`) |
| `tag-merge` | `merge_tags` | V2 | ✅ | **source tag is deleted** |
| `tag-delete` | `delete_tag` | V2 | ✅ | removed from all tasks |

### Habits (7)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `habit-list` | `list_habits` | V2 | ❌ | streaks + totals |
| `habit-section-list` | `list_habit_sections` | V2 | ❌ | Morning / Afternoon / Evening |
| `habit-create` | `create_habit` | V2 | ❌ | Boolean or Real (goal/step/unit) |
| `habit-update` | `update_habit` | V2 | ❌ | ⚠️ V2 batch = **full replacement** → read-modify-write always |
| `habit-delete` | `delete_habit` | V2 | ✅ | irreversible |
| `habit-checkin` | `habit_checkin` | V2 | ❌ | `{"habit_id":"...","checkin_stamp":20260809}` |
| `habit-records` | `get_habit_records` | V2 | ❌ | check-in history |

### History (1)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `history-query` | `query_task_history` + `get_completed_tasks` + `get_deleted_tasks` | V2 | ❌ | full filter engine over completed / abandoned / deleted — `history_source` selects the bucket (merged: no separate `history-completed` / `history-deleted`) |

### Stats (3)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `focus-stats` | `get_focus_stats` | V2 | ❌ | heatmap \| distribution |
| `user-status` | `get_user_status` | V2 | ❌ | inbox id, Pro state, team |
| `user-stats` | `get_productivity_stats` | V2 | ❌ | score, level, streaks |

### Query (5)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `workspace-map` | `workspace_map` | V1 (+V2 counts) | ❌ | folder/project tree |
| `query-projects` | `query_projects` | V1 + V2 | ❌ | |
| `query-folders` | `query_folders` | V1 + V2 | ❌ | |
| `query-tasks` | `query_tasks` + `query_notes` + `priority_dashboard` + `stale_tasks` | V1 + V2 | ❌ | the full filter engine (dates, tags, regex, priorities…). Merged: `kinds:["NOTE"]` replaces `query-notes`, `priorities` + `group_by:"priority"` replaces `view-priority`, `modified_to` replaces `view-stale` |
| `query-agenda` | `query_agenda` | V1 + V2 | ❌ | date/time window |

> The history filter engine (`history-query`) is listed once, under **History**.

### Views (5)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `view-today` | `tasks_of_today` + `events_of_today` | V1 + V2 | ❌ | today's scheduled items; `timed_only:true` returns events only (merged: no separate `view-events`) |
| `view-week` | `week_agenda` | V1 + V2 | ❌ | |
| `view-week-overview` | `week_overview` | V1 + V2 | ❌ | events / due / overdue split |
| `view-upcoming` | `upcoming_tasks` | V1 + V2 | ❌ | |
| `view-overdue` | `overdue_tasks` | V1 + V2 | ❌ | |

> Views are **thin shortcuts over `query.py`** — `view-priority` and `view-stale` were merged into
> `query-tasks` (priority grouping / `modified_to` window), `view-events` into `view-today`.

### Escape hatch (1)

| Action | Source tool | Auth | HITL | Notes |
|--------|-------------|:----:|:----:|-------|
| `raw` | *(new — `tg-proxy do raw` equivalent)* | V1 or V2 | ✅ | any TickTick endpoint, any method |

```bash
tick-proxy do raw '{"api":"v2","method":"post","endpoint":"/batch/task","payload":{"update":[]}}'
tick-proxy do raw '{"api":"v1","method":"get","endpoint":"/project/6xxx/data"}'
```

### Action count

| Group | Count |
|-------|------:|
| Tasks — write | 5 |
| Tasks — read | 3 |
| Tasks — batch | 5 |
| Subtasks | 1 |
| Sync | 2 |
| Projects | 5 |
| Folders | 2 |
| Kanban columns | 2 |
| Tags | 5 |
| Habits | 7 |
| History | 1 |
| Stats | 3 |
| Query | 5 |
| Views | 5 |
| Escape hatch | 1 |
| **TOTAL `do` actions** | **52** |

**Coverage proof — all 71 `tick-mcp` tools accounted for:**

| Fate | Count | Detail |
|------|------:|--------|
| Renamed 1:1 → `do` action | 51 | domain-first kebab rename |
| Folded into `@require_verification` decorator | 5 | `verified_create_project` → `project-create` · `verified_assign_project_folder` → `project-update` · `verified_set_subtask_parent` → `task-parent-set` · `verified_move_tasks` **and** `verified_batch_move` → `task-move` |
| **Merged into an existing action** | 7 | `rename_tag` → `tag-update` · `query_notes` **and** `priority_dashboard` **and** `stale_tasks` → `query-tasks` · `get_completed_tasks` **and** `get_deleted_tasks` → `history-query` · `events_of_today` → `view-today` |
| Folded into `do --help` | 1 | `ticktick_guide` — docstrings are the single source of truth |
| Folded into `admin status` | 1 | `check_v2_availability` |
| **Dropped — presets** | 4 | `list_query_presets` · `save_query_preset` · `run_query_preset` · `delete_query_preset` — **replaced by the payload `file-path` mechanism + JSON assets in the `k-tick` skill** (code stays 100 % business) |
| **Dropped — local builders** | 2 | `build_recurrence_rule` · `build_reminder` — **not TickTick operations**; the RRULE / TRIGGER formats are documented in the docstrings of the actions that consume them (`task-create`, `task-update`, `habit-create`) |
| **Total consumed** | **71** | ✅ zero gaps |
| **New** | +1 | `raw` |
| **Result** | **52** | `51 + 1` |
| **Total consumed** | **71** | ✅ zero gaps |
| **New** | +1 | `raw` |
| **Result** | **52** | `51 + 1` |

---

## Verification model — `@require_verification` decorator

The TickTick API fails **silently**: the HTTP call returns 200, the data is not saved.
`tick-mcp` handled this by shipping 5 duplicated `verified_*` tools. `tick-proxy` centralizes it.

**No `--verify/-V` flag.** Verification is NOT a CLI option — it is a **structural decorator**
(`@require_verification`) applied directly on the handler of the actions that need it. `cli.py` has no
code path to skip it: if the action is decorated, the read-back ALWAYS runs; if it is not, no flag
can force it. The decorator is the single source of truth — non-bypassable by construction.

```python
# actions/base.py — the decorator (twin of hitl.require_approval)
def require_verification(*checks: str):
    """Mandatory post-write read-back verification, baked into the handler."""
    def decorator(func):
        @wraps(func)
        async def wrapper(client, payload):
            result = await func(client, payload)                    # the write
            result["data"]["verification"] = await verify_write(client, result, checks)
            return result
        return wrapper
    return decorator

# actions/tasks.py — the action definition carries the decorator, no flag anywhere
@require_verification("parentId", "childIds")
async def task_parent_set(client: TickClient, payload: TaskParentSetPayload) -> dict:
    ...
```

**Verification lands in `data`, never in `meta`** so the returned business object contains the
persisted state and the exact proof that produced it:

```json
{
  "meta": {
    "status": "ok",
    "comment": "",
    "edited": false
  },
  "data": {
    "id": "68f1...",
    "verification": {"method":"GET /open/v1/project/6xxx/task/68f1...","checked":["parentId","childIds"],"expected":{"parentId":"68e0..."},"actual":{"parentId":"68e0..."},"ok":true}
  }
}
```

**Always-on verification (NOT optional — enforced by the decorator):** the four documented
silent-failure operations carry `@require_verification`, because a non-verified result is meaningless.

| Action | Decorator checks | Why always verified |
|--------|------------------|---------------------|
| `task-parent-set` | `parentId`, `childIds` | `parentId` is silently ignored by V1 creation and unreliable via V2 batch |
| `project-create` / `project-update` *(with `group_id`)* | `groupId` (via `sync-full`) | `groupId` silently ignored by V1; V1 reads always return `null` |
| `habit-update` | `name`, `color`, `reminders`, … | V2 `/habits/batch` is a full replacement — a partial payload wipes fields |
| `task-move` | presence of moved tasks + `childIds` | V2 never cascades to children — orphaned subtasks |

**Anti-bypass guard:** `make smoke` (registry integrity) checks — at import time, via AST — that
every action declaring required verification carries the `@require_verification` decorator. A
missing decorator is a **hard error**, not a warning. Removing it is impossible
without breaking `make check`.

### `@require_verification` — detailed scenario (always-on case: `task-parent-set`)

```
1. Intent
   tick-proxy do task-parent-set '{"task_id":"68f1…c","project_id":"6xxx","parent_id":"68e0…a"}'

2. Write  → POST /api/v2/batch/task        (the parent relationship)
   Server → 200 OK                          ← TickTick V2 "acknowledged" (NOT "persisted")

3. Re-read → GET /open/v1/project/6xxx/task/68f1…c   (V1 read-back)
             + sync-full                            (V2 cross-check)

4. Compare → checked:  ["parentId", "childIds"]
             expected: {"parentId": "68e0…a"}
             actual:   {"parentId": <what the server REALLY holds>}

5. Report (in data)
   match     → data.verification.ok=true,   exit 0
   mismatch  → data.verification.ok=false,  exit 1
```

The same sequence runs automatically on every decorated action: write → read back → compare →
report. Without it, a `200 OK` that silently dropped the write is indistinguishable from a
successful write.

### Plausibility — confirmed by independent sources (web check, 2026-08-09)

The "200 OK but not saved" premise is **not a hypothesis** — it is documented by multiple
independent TickTick integrations:

| Source | Finding |
|--------|---------|
| `dev-mirzabicer/ticktick-sdk` (docs) | "Setting `parent_id` during task creation is **ignored** by the API" → separate `make_subtask()` call required |
| `jaeyeonling/ticktick-client` (Playwright-captured, 2026-04-07) | `POST /api/v2/task/{id}` with new `projectId` → **200 but no actual change**; task move has no native endpoint (copy+delete, ID changes) |
| `MHoroszowski/ticktick-client` | V2 `POST /api/v2/column` **silently drops** the update (200 with empty `id2etag`) when `projectId` is omitted; `groupId` clear wants literal `"NONE"` not JSON `null` |
| `dev-mirzabicer/ticktick-sdk` issues #41, #43 | `complete_tasks` **wipes** `start_date`/`due_date`; `update_tasks` **silently re-activates** completed tasks; all-day updates may clear dates |
| `tick-mcp` live-test suite (`tests/live/`, 12 scripts) | Reminders silently dropped without a `dueDate` anchor; V2 `/habits/batch` = full replacement |

Conclusion: read-back verification is the only reliable way to know whether a TickTick write
actually landed. The `@require_verification` decorator is not paranoia — it is the documented industry
workaround, made structural.

---

## Config — one `.env` at `~/.config/tick-proxy/.env`

**No `config.yaml`. No in-repo `.env`. No cache. No magic.**
`tick-mcp` stored its `.env` inside the package (`src/tick_mcp/.env`) — that is dropped.
Endpoint defaults live as documented constants in `config.py`; **every one is overridable** from
this single file.

```env
# ── tick-proxy configuration ──────────────────────────────────────────────────
# Location : ~/.config/tick-proxy/.env      (chmod 600 — contains secrets)
# Created  : tick-proxy admin setup
# Every line below is documented with a real, valid example value.

# [REQUIRED] V1 Open API personal access token.
# Where  : TickTick → Settings → Integrations → API (copy the displayed token).
# Example: TICK_API_TOKEN=6f8a1c2e-4b7d-4e9f-8a1b-2c3d4e5f6a7b
TICK_API_TOKEN=

# [OPTIONAL] V2 web-API session cookie (the `t` cookie on ticktick.com).
# Unlocks : tags, habits, folders, columns, batch ops, focus, history, sync.
# Validity: ~30 days. Refresh with: tick-proxy admin session-refresh
# Example : TICK_SESSION_TOKEN=a1b2c3d4e5f60718293a4b5c6d7e8f90
TICK_SESSION_TOKEN=

# [OPTIONAL] TickTick account e-mail — pre-fills the `admin session-refresh` HITL form.
# The password is NEVER stored: it is requested transiently via HITL only when the
# session token is invalid, exchanged for a new token, then discarded.
# Example: TICK_EMAIL=user@example.com
TICK_EMAIL=

# [AUTO] Timestamps written by `admin setup` / `admin session-refresh` for visibility only.
# Example: TICK_SESSION_TOKEN_OBTAINED_AT=2026-08-09T11:24:03Z
TICK_SESSION_TOKEN_OBTAINED_AT=
# Example: TICK_SESSION_TOKEN_EXPIRES_AT=2026-09-08T11:24:03Z
TICK_SESSION_TOKEN_EXPIRES_AT=

# [OVERRIDE] V1 Open API base URL — change only if TickTick moves the endpoint.
# Example: TICK_API_V1_BASE_URL=https://api.ticktick.com/open/v1
TICK_API_V1_BASE_URL=
# [OVERRIDE] V2 web API base URL.
# Example: TICK_API_V2_BASE_URL=https://api.ticktick.com/api/v2
TICK_API_V2_BASE_URL=
# [OVERRIDE] Web origin used for Origin/Referer headers on V2 calls (CORS check).
# Example: TICK_WEB_ORIGIN=https://ticktick.com
TICK_WEB_ORIGIN=
# [OVERRIDE] HTTP timeout in seconds for every API call.
# Example: TICK_API_TIMEOUT=15
TICK_API_TIMEOUT=
# Logging: NO file, NO level env var — exactly like `tg-proxy`. All logs go to stderr
# (systemd/journald captures them). Level is fixed in code: `setup_logging(level="WARNING")`.
```

**Config directory layout:**

```
~/.config/tick-proxy/
└── .env               # the file above (chmod 600)
```

**No log file — like `tg-proxy`, logs go to stderr only** (systemd/journald captures them;
`logger.py` is a pure stderr logger, no file, no rotation — see "What is dropped").

### Recurring queries — payload file-path + `k-tick` skill assets (NO preset machinery)

**There is no `presets.json`, no `preset-*` actions, no presets module — purged by KπX decision
(2026-08-09): it over-complicated the design for nothing.**

Recurring queries are plain JSON files, owned by the **future `k-tick` skill** (created when
`tick-proxy` is installed). The agent drops the query JSON into the skill's `assets/` and invokes
it through the **existing payload file-path mechanism** — the code stays 100 % business:

```bash
# assets/ in the k-tick skill holds the query JSON files, e.g. k-tick/assets/revision-week.json
# {"project_names":["🎓 X/Revision"],"priorities":[5,3],"due_from":"2026-08-10",
#  "due_to":"2026-08-16T23:59:59","tags":["revision","exam"],"has_reminders":true,
#  "sort_by":"dueDate"}

# run it — same call as any other payload file, no preset concept
tick-proxy do query-tasks /path/to/k-tick/assets/revision-week.json

# update the query = edit the JSON file (git-tracked in the skill), nothing to migrate
```

- No storage format to design, no CRUD to maintain, no duplication with the skill's docs.
- The 4 `tick-mcp` preset tools (`list_query_presets`, `save_query_preset`, `run_query_preset`,
  `delete_query_preset`) are **dropped** (see coverage proof) — their value is fully covered by
  file-path payloads + skill assets.

**Env-var prefix:** `TICK_*`, harmonizing with `tg-proxy`'s `TG_*`.

---

## Architecture

```
tick-proxy
   │
   ├── admin setup|status|session-refresh            # ALWAYS JSON
   └── do <action> [payload|file] [-o] [-f]    # 52 flat RPC actions
       │
       ▼
┌────────────────────────────────────────────────────────────────┐
│  src/tick_proxy/                                               │
│  ├── cli.py            ONE Typer app: `do` + `admin` sub-typers │
│  │                     (thin — parse, dispatch, envelope, exit) │
│  ├── client.py         TickClient — auth state + v1_*/v2_* verbs │
│  ├── models.py         SHARED types only: Output, OutputMeta,   │
│  │                     Priority, TickTickAPIError               │
│  ├── config.py         ~/.config/tick-proxy/.env loader +        │
│  │                     documented endpoint defaults             │
│  ├── display.py        Rich helpers: print_json / print_table    │
│  ├── doc.py            dynamic --help injection from docstrings  │
│  ├── logger.py         stderr logger — systemd/journald (no file) │
│  ├── exceptions.py     TickProxyError                            │
│  ├── hitl.py           HITL web UI (free port, browser auto-open)│
│  ├── templates/        hitl.html · setup.html                    │
│  │                                                              │
│  ├── api/              LOW-LEVEL HTTP (from tick_mcp/client_api) │
│  │   ├── transport.py    V1/V2 request + auth + 401 re-login     │
│  │   ├── projects.py     project/folder/column endpoints         │
│  │   ├── tasks.py        task/batch endpoints                    │
│  │   ├── habits.py       habit endpoints                         │
│  │   └── stats.py        focus/user/productivity endpoints       │
│  │                                                              │
│  ├── actions/          THE 52 ACTIONS (from tick_mcp/mcp_api)    │
│  │   ├── base.py         ActionDef, verify(), payload validation │
│  │   ├── registry.py     name → ActionDef map, duplicate = error │
│  │   ├── tasks.py        task-create/update/complete/reopen/     │
│  │   │                   delete/info + project-tasks/inbox-list  │
│  │   ├── tasks_batch.py  task-batch-* · task-move · task-parent- │
│  │   │                   set · subtask-create                    │
│  │   ├── projects.py     project-* · folder-* · column-*         │
│  │   ├── tags.py         tag-*                                   │
│  │   ├── habits.py       habit-*                                 │
│  │   ├── query.py        query-* · workspace-map                 │
│  │   ├── views.py        view-*                                  │
│  │   ├── history.py      history-*                               │
│  │   ├── stats.py        focus-stats · user-status · user-stats   │
│  │   ├── sync.py         task-list · sync-full                    │
│  │   ├── builders.py     rrule-build · reminder-build             │
│  │   └── raw.py          raw                                      │
│  │                                                              │
│  ├── query.py          the filter engine (dates/tags/regex/…)     │
│  │                     (from tick_mcp/services/query.py — flat,   │
│  │                      no services/ package)                     │
│  │                                                              │
│  └── admin.py          setup · status · session-refresh —          │
│                        SINGLE SOURCE OF TRUTH for admin logic      │
└────────────────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────────────┐
│  ~/.config/tick-proxy/.env                                      │
│  /tmp/tick-proxy-autosave/{action}_{timestamp}.json             │
└────────────────────────────────────────────────────────────────┘
```

### Why a registry instead of `tg-proxy`'s single `client.py`

`tg-proxy` holds its **24** actions as methods on one `TgClient` (1796 lines — measured), and
`cli.py` (1000 lines) repeats every action name twice: once in a `@app_do.command(...)` decorator and
once in the hand-maintained `commands = {…}` dict inside `_do_help_callback` (`cli.py:152-177`).
That hand-maintained dict **is already a registry** — just an implicit, duplicated one.

At 52 actions the same shape would exceed 5000 lines in a single `client.py`, and the name would have
to be written in three places. That is a structural burden and against *"if you code, think tree"*.
`tick-proxy` therefore keeps the **exact same interface and doc mechanism** as `tg-proxy` but makes
the registry **explicit and single-sourced**, storing actions as **`ActionDef` entries in domain
modules**:

```python
# actions/tasks.py — payload model and handler colocated, docstring = documentation
class TaskCreatePayload(BaseModel):
    title: str = Field(..., description="Task title")
    project_id: str | None = Field(None, description="Target project ID; omit → Inbox")
    priority: int = Field(0, description="0=none, 1=low, 3=medium, 5=high")

async def task_create(client: TickClient, payload: TaskCreatePayload) -> dict:
    """
    Create a new TickTick task.

    Creates the task through the V1 Open API. `parentId` is NOT accepted here —
    the V1 endpoint silently ignores it; use `task-parent-set` afterwards.

    Parameters:
        - title (str): Task title.
        - project_id (str | None): Target project ID. Omit → Inbox.
        - priority (int): 0=none, 1=low, 3=medium, 5=high.

    Examples:
        - Simple task in the Inbox:
            `tick-proxy do task-create '{"title_ops":[{"op":"insert","insert_lines":[0],"insert_text":"Buy bread"}]}'`
            → {"id":"68f1a2b3c4d5e6f708192a3b","title":"Buy bread","projectId":"inbox127..."}
        - High-priority task in a project:
            `tick-proxy do task-create '{"title":"Ship v1","project_id":"6xxx","priority":5}'`
            → {"id":"68f1a2b3c4d5e6f708192a3c","title":"Ship v1","priority":5}
    """

TASKS = [ActionDef("task-create", TaskCreatePayload, task_create, hitl=False)]
```

`registry.py` aggregates every domain list into one `name → ActionDef` map and raises on
duplicates at import time. `cli.py` builds its Typer commands **from the registry** — adding an
action means adding one `ActionDef`, nothing else.

### Doc system (`tg-proxy` `doc.py`, unchanged)

Every handler carries a structured docstring with the mandatory sections
`Description` / `Parameters:` / `Examples:` where each example shows the command **and** its real
output after `→`. `doc.py` extracts it and injects it into Typer `help` (`get_full_help`) and into
the `do --help` overview (`get_compact_help`), wrapping `→` outputs in the `meta`+`data` envelope.

**Result:** `tick-proxy do task-create --help` shows the full docstring **plus** the exact Pydantic
payload schema — human-readable and agent-parseable. This replaces `ticktick_guide` entirely.

### `query.py` — the filter engine (roles)

```
src/tick_proxy/
└── query.py      → THE FILTER ENGINE  (ported from tick_mcp/services/query.py, 754 lines — flat,
                   no services/ package)
```

**`src/tick_proxy/query.py` — the filter engine. Role:** turn a declarative filter description into the
right V1/V2 calls + client-side post-filtering. Every `query-*` and `view-*` action is a **thin
wrapper** over this engine; the engine itself never talks to the CLI. It owns:

- **Scope resolution** — folders, projects, tags, notes-only projects…
- **Time windows** — due/start/created ranges, HH:MM time-of-day windows (agenda views)
- **Grep-like matching** — substring, `any`/`all`/`phrase` keyword modes, regex + exclusion regex
- **Shape filters** — has_reminders, is_recurring, has_checklist, parent/subtask shape, priorities
- **Ordering + limiting** — sort_by (dueDate, priority, completedTime…), descending, limit

Example — `query-tasks` maps
`{"project_names":["🛠️ Tech & Science"],"priorities":[5],"due_from":"2026-08-09","due_to":"2026-08-09T23:59:59"}`
into the appropriate reads and returns only the matching tasks. `view-overdue`, `view-stale`,
`priority_dashboard` are the same engine with a fixed filter shape.

```
┌─────────────────────────────────────────────────────────────┐
│  query.py  ◀── query-* / view-* actions  (the engine)        │
│  raw.py    ◀── do raw                    (escape hatch)      │
└─────────────────────────────────────────────────────────────┘
```

No password ever flows through `query.py` — credentials live only in `~/.config/tick-proxy/.env`
and in the transient `admin session-refresh` HITL exchange.

---

## HITL — 100% Web UI

Same mechanism as `tg-proxy` (`hitl.py`): a local HTTP server on an OS-assigned free port, the
browser auto-opens, the payload is editable, the reviewer approves or rejects, and the outcome is
reported in `meta`. Timeout 600 s → automatic `rejected`. If no browser is available the URL is
printed with an `ssh -L` hint.

**HITL-required — 13 `do` actions + 2 `admin` commands:**

| Reason | Actions |
|--------|---------|
| Mandatory human review | `task-create` · `task-update` · `subtask-create` — every kind uses one full-JSON task review followed by title/content/desc inline patch frames |
| Batch write review | `task-batch-create` · `task-batch-update` — one editable complete V2 payload, deliberately without document diff frames |
| Irreversible deletion | `task-delete` · `task-batch-delete` · `project-delete` · `tag-delete` · `habit-delete` |
| Destructive side effect | `tag-merge` *(source tag destroyed)* |
| Folder / column mutation | `folder-manage` · `column-manage` — explicit `@require_approval` full-JSON review |
| Arbitrary API access | `raw` |
| Secrets | `admin setup` · `admin session-refresh` |

No new `note-create` / `note-update` actions exist: task kind does not decide review quality. Every
task create/update carries full normal task JSON plus three explicit operation lists: `title_ops`,
`content_ops`, and `desc_ops`. An operation is `replace` with `old_str`, mandatory contiguous
`old_lines`, and `new_str`, or `insert` with `insert_lines` and one `insert_text`. All operations
are replayed and checked against fresh task text before HITL; stale text/ranges reject the command.
The unified task page then shows one fully editable complete JSON payload and three vertically
stacked editable inline Monaco patches. On approval, the complete edited JSON is parsed and sent,
with the three inline editors taking final precedence only for `title`, `content`, and `desc`. The
approved response returns exact final `title`, `content`, `desc`, and
`data.diff.title_diff`, `data.diff.content_diff`, `data.diff.desc_diff`. `meta` remains exactly
status/comment/edited/verification: there is no `meta.review` block.

---

## Error model & exit codes

| Case | Behavior | Exit |
|------|----------|-----:|
| Success | `{"meta":{"status":"ok",…},"data":…}` | 0 |
| HITL approved | `meta.status = "approved"` | 0 |
| HITL rejected / timeout | `meta.status = "rejected"`, `data = null` | 1 |
| Missing `.env` / missing `TICK_API_TOKEN` | error envelope + hint `tick-proxy admin setup` | 1 |
| V2 action without V2 auth | error envelope naming the missing keys | 1 |
| V1 token expired (401) | error envelope + hint `tick-proxy admin setup` | 1 |
| V2 session expired (401) | error envelope + hint `tick-proxy admin session-refresh` — transient HITL credentials (username + password collected, new token saved, password discarded) | 1 |
| Invalid JSON / file not found | `TickProxyError` envelope | 1 |
| Pydantic validation error | error envelope listing offending fields | 1 |
| Verification failed (`@require_verification`) | `data.verification.ok = false` (block present) | 1 |
| Rate limit (429) | error envelope, no auto-retry (explicit, no magic) | 1 |
| `admin` + `--format`/`-o` | misuse error envelope | 2 |

**stdout is pure JSON.** All logs, HITL prompts and progress go to **stderr** — a piped
`tick-proxy do … \| jq` must never break.

---

## What is dropped from `tick-mcp` (and why)

All figures below are **exact** line counts measured on `tick-mcp` v0.2.0 — whole files via
`wc -l`, individual functions and module-level constants via Python `ast` (`end_lineno - lineno + 1`).

| Dropped | Lines | Rationale |
|---------|------:|-----------|
| `server.py` (137) + `mcp_api/core.py` (357) | 494 | MCP plumbing — no MCP transport any more, the CLI *is* the interface. Includes the `TOOL_CATALOG` (89), `COMMON_WORKFLOWS` (61) and `INTENT_GUIDE` (36) constants that live inside `core.py` |
| `http_app.py` (FastAPI `/mcp`, `/admin/*`, `/health`) | 235 | existed only to serve the remote MCP container |
| `admin/telegram.py` (Telegram admin bot) | 218 | remote-operation bridge for the container; a local CLI needs none |
| `mcp_api/verified.py` — 5 × `verified_*` + `_project_child_index` helper | 214 | replaced by the `@require_verification` decorator. `create_subtask` (71 lines, same file) is **kept** as `subtask-create` |
| `ticktick_guide` (in `mcp_api/utilities.py`) | 102 | replaced by docstring-driven `do --help` — single source of truth |
| `config.yaml` | 57 | folded into documented `config.py` defaults + `.env` overrides |
| `daemon.py` (PID file, serve/stop/status) | 48 | TickTick is a stateless REST API — no persistent session to own |
| `check_v2_availability` (in `mcp_api/utilities.py`) | 17 | folded into `admin status` |
| `deploy/` + `Dockerfile` + `.dockerignore` | — | **Docker explicitly excluded by KπX** |
| `src/tick_mcp/.env` (in-repo secrets) | — | moved to `~/.config/tick-proxy/.env` |
| stored-password V2 auto-login (`TICKTICK_PASSWORD` + `_v2_login` in `client_api/transport.py`) | — | dropped — the password is **never stored**; `admin session-refresh` collects it transiently via HITL, saves the token, discards the password |
| admin file logging (`logs/ticktick_admin_debug.log` + `_setup_logger` file handler) | — | dropped — like `tg-proxy`, everything logs to **stderr**; systemd/journald captures it (no `.log` file, no rotation) |
| **TOTAL removed** | **1385** | transport + deployment + duplicated-guide scaffolding |

**No double counting:** the three guide constants are counted once, inside the `core.py` figure.
The `mcp_api/verified.py` row counts only the 5 folded functions plus their private helper — the
retained `create_subtask` is excluded.

The ~4000 lines of real TickTick domain logic are **ported, not rewritten**:
`mcp_api/read.py` (871 — the query/views surface), `client_api/` (776 — V1/V2 transport and endpoint
wrappers), `query.py` (754 — the filter engine, flat at `src/tick_proxy/`), `models.py` (578), the
admin logic (610 — merged into a single `admin.py`), plus the remaining domain modules (`tasks_write`,
`habits`, `projects`, `tasks_batch`, `tags`, `folders`, `stats`, `sync_api`, `history`,
`tasks_read`). The `services/query_presets.py` module (presets) is **NOT ported** — dropped per KπX
decision (see coverage proof).

---

## Ecosystem impact — must be handled at implementation time

| Item | Action required |
|------|-----------------|
| `~/.config/opencode/opencode.jsonc` → `mcp.tick_fallback` | remove the MCP entry; agents call the CLI through `bash` (exactly as `tg-proxy` replaced the `tg` MCP) |
| `k-ticktick` skill (`allowed-tools: mcp__tick-mcp__*`) | rewrite to `Bash(tick-proxy *)`; the Project Map, gotchas and *What's up* slice stay valid (transport-agnostic) |
| `k-ticktick/references/mcp.md` + `tool-keep-matrix.md` | re-point to the 52 `do` actions |
| `https://tick.kpihx-labs.com/mcp` deployment | goes away with the Docker/HTTP layer — confirm nothing else consumes it |
| `~/Work/AI/MCPs/tick_mcp/` | keep untouched as the reference implementation until `tick-proxy` reaches parity, then archive |
| PyPI `tick-mcp` | untouched; `tick-proxy` publishes as a new package |

---

## Infrastructure files (Docker excluded)

| File | Source | Note |
|------|--------|------|
| `pyproject.toml` | `tg-proxy` | single entry point `tick-proxy = "tick_proxy.cli:app"`, `uv_build` backend |
| `Makefile` | `tg-proxy` | **minus** all `docker-*` targets |
| `.gitlab-ci.yml` | `tg-proxy` | `validate` → `build` → `publish`; **no docker stage** |
| `.gitignore` | `tg-proxy` | `.env` ignored, `.env.example` kept |
| `.env.example` | this document | the fully-commented block above |
| *(none)* | — | Install, uninstall and smoke are concise Makefile targets; duplicate shell wrappers are intentionally absent |
| `tests/` | `tick-mcp` | port the unit tests (models, query engine, config, admin service) |
| `tests/live/` | `tick-mcp` | the 12 numbered live scripts, re-pointed at the CLI |
| `README.md` · `AGENTS.md` · `CHANGELOG.md` · `TODO.md` | standard | |

### Makefile targets

| Target | Action |
|--------|--------|
| `check` | `smoke` → ruff check --fix → ruff format → py_compile → pyright → pytest |
| `smoke` | `tick-proxy do --help` + registry integrity (52 actions, zero duplicates) |
| `uv-install` / `uv-link` / `uv-uninstall` / `uv-purge` | `uv tool` lifecycle |
| `uv-build` / `uv-publish` | sdist + wheel → PyPI |
| `git-push` / `push` | push to `github` **and** `gitlab` |
| `git-install-hooks` | pre-commit → `make check` |
| `release` | `check` → `git-push` → `uv-publish` |

**Remotes:** `github: git@github.com:KpihX/tick-proxy.git` · `gitlab: git@gitlab.com:kpihx/tick-proxy.git`

---

## Decisions requiring KπX validation

| # | Decision | Proposal | Impact if refused |
|---|----------|----------|-------------------|
| **D1** | Action naming | flip to **domain-first kebab** (`task-create`, not `create-task`) — matches `tg-proxy` (`bot-list`, `chat-read`) | keep `create-task` style; `tg-proxy` ADN broken |
| **D2** | 5 × `verified_*` tools | fold into the **`@require_verification` decorator** on the silent-failure ops | keep 5 duplicate actions → 70 actions |
| **D3** | `ticktick_guide` | drop — `do --help` / `do <action> --help` generated from docstrings is the guide | keep a `guide` action duplicating docstrings |
| **D4** | `check_v2_availability` | fold into `admin status` | keep as a 66th action |
| **D5** | 8 × `tick-admin` credential commands | fold into ONE `admin setup` HITL form (3 persisted fields + clear checkboxes; **password never stored** — transient HITL only, see *Auth & password policy*) | keep 8 flat `admin` actions |
| **D6** | `config.yaml` | drop — documented defaults in `config.py`, all overridable via `.env` | keep a second config file |
| **D7** | Env prefix | **`TICK_*`** (harmonizes with `TG_*`) | any other prefix |
| **D8** | HTTP transport + Telegram admin bot + daemon | **drop** (they served the Docker deployment only) | must keep FastAPI + bot + PID lifecycle |
| **D9** | HITL scope | deletions + `tag-merge` + `raw` + admin secrets, plus **mandatory `task-create` / `task-update`**; NOTE uses locked document-diff mode | narrower policy would violate the approved task-write review requirement |
| **D10** | Actions layout | `actions/` registry with colocated Pydantic payloads | monolithic `client.py` (~5000 lines) |
| **D11** | Old repo | keep `~/Work/AI/MCPs/tick_mcp/` as reference until parity, then archive | delete immediately |

---

## Implementation plan (after validation)

| Phase | Content | Gate |
|-------|---------|------|
| **P0** | Skeleton: `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`, `.gitlab-ci.yml`, `scripts/`, package dirs, `git init` + remotes | `make check` runs (empty) |
| **P1** | Core: `config.py`, `exceptions.py`, `logger.py`, `display.py`, `doc.py`, `models.py`, `client.py`, `api/transport.py` | `admin status` works against the real API |
| **P2** | `hitl.py` + `templates/` + `admin.py` (setup, status, session-refresh) | full auth lifecycle proven live |
| **P3** | `actions/base.py` + `registry.py` + `cli.py` wired to the registry; first 5 task actions | `do --help` + `do task-create` live |
| **P4** | Port all remaining actions domain by domain (`api/` + `query.py` ported alongside) | registry = 52, `make smoke` green |
| **P5** | Verification engine (`@require_verification` on declared writes) | verified writes proven live |
| **P6** | `raw` gateway | live V1 + V2 raw calls proven |
| **P7** | Tests: port unit tests, adapt `tests/live/` (12 scripts) to the CLI | `make check` fully green |
| **P8** | Docs: `README.md`, `CHANGELOG.md` 1.0.0; ecosystem switch (`opencode.jsonc`, `k-ticktick` skill) | agents use `tick-proxy` end-to-end |

---

## Status

- See `AGENTS.md` for the agent working context.
- See `TODO.md` for the live task list.
- See `CHANGELOG.md` for version history.
- See `README.md` for user-facing documentation.

*Architecture contract drafted 2026-08-09 — refonte of `tick-mcp` v0.2.0 (71 MCP tools) into
`tick-proxy` (52 RPC actions), modelled on `tg-proxy` v1.1.0.*
