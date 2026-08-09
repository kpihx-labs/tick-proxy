# Changelog

## Unreleased — 0.1.0 (design)

### Architecture contract drafted — refonte of `tick-mcp` into `tick-proxy`

- **`CONTRACT.md` created** — complete architecture contract for the refonte of `tick-mcp` v0.2.0
  (MCP server, 71 tools) into `tick-proxy` (non-MCP RPC CLI, 65 flat actions), modelled on
  `tg-proxy` v1.1.0 (`$HOME/KpihX-Labs/tg_proxy`).
- **Interface ADN adopted from `tg-proxy`:** single binary, `do` (RPC) + `admin` (always JSON)
  namespaces, flat kebab-case actions, payload as inline JSON or file path,
  `meta`+`data` envelope, `--output-file/-o` + `--format/-f` meta options, docstring-driven
  `--help` via `doc.py`, HITL web UI, autosave to `/tmp/tick-proxy-autosave/`.
- **Full 71 → 65 action mapping** established with a coverage proof (zero gaps):
  64 tools renamed 1:1 to domain-first kebab actions, 5 `verified_*` tools folded into a
  `--verify/-V` meta option, `ticktick_guide` folded into `do --help`, `check_v2_availability`
  folded into `admin status`, plus one new `raw` escape-hatch action.
- **Naming convention flipped** to `<domain>-<verb>` (`task-create`, `project-list`,
  `habit-checkin`) to match `tg-proxy` (`bot-list`, `chat-read`, `folder-set`).
- **Verification model defined:** `--verify/-V` performs post-write read-back comparison and
  reports through `meta.verified` + `data.verification`; verification is **always on** for the four
  documented silent-failure operations (`task-parent-set`, `project-create`/`project-update` with
  `group_id`, `habit-update`, `task-move`).
- **Admin surface simplified:** the 8 `tick-admin` credential subcommands
  (`api|session|user|pass` × `set|unset`) collapse into ONE `admin setup` HITL web form with four
  fields and explicit clear semantics; `admin` gains `session-refresh` and `logs`.
- **Config unified:** `config.yaml` dropped, in-repo `src/tick_mcp/.env` dropped; single
  `~/.config/tick-proxy/.env` with documented defaults in `config.py` and every value overridable.
  Env prefix harmonized `TICKTICK_*` → `TICK_*` (mirrors `tg-proxy`'s `TG_*`).
- **Layers dropped** (~1550 lines of transport/deployment scaffolding): MCP server plumbing,
  FastAPI HTTP app, Telegram admin bot, PID daemon, `deploy/` + `Dockerfile`, `config.yaml`,
  `TOOL_CATALOG`/`COMMON_WORKFLOWS`/`INTENT_GUIDE`. Domain logic (query engine, V1/V2 endpoint
  wrappers, gotcha handling) is ported, not rewritten.
- **Docker explicitly excluded** per KπX decision — the `tg-proxy` Docker layer is untested and
  non-functional, so it is not reproduced here.
- **`actions/` registry layout chosen** over a monolithic `client.py`: 65 actions as `ActionDef`
  entries in domain modules with colocated Pydantic payload models; `registry.py` raises on
  duplicate names at import time; `cli.py` builds its Typer commands from the registry.
- **`AGENTS.md` created** — agent working context: key files, stdout-purity rule, secret hygiene,
  registry rule, docstring-as-documentation rule, the five TickTick silent-failure gotchas.
- **`README.md` created** — user-facing overview: namespaces, 65-action table by domain, usage,
  output format, config, HITL matrix, install, development.
- **`TODO.md` created** — 9-phase implementation plan (P0 skeleton → P8 ecosystem switch) with the
  11 open decisions gating P0.

### Not yet done

No source code, no `pyproject.toml`, no `Makefile`, no git repository — implementation is gated on
validation of the 11 decisions listed in `CONTRACT.md` → *Decisions requiring KπX validation*.

### Design refinement pass 2 (2026-08-09 — KπX directives)

Applied on top of the skeleton, patch-edit, following KπX's exact orders:

- **`admin logs` purged.** The `logs` admin command is gone entirely — like `tg-proxy`, there is
  **no log file managed by the tool** and no `tick-proxy.log`. Logging is **stderr-only**
  (`logger.py` → systemd/journald captures), no file, no rotation, no `TICK_LOG_LEVEL` env var.
  `admin` is now exactly: `setup`, `status`, `session-refresh`.
- **TickTick password NEVER stored.** `.env` holds at most `TICK_API_TOKEN`, `TICK_SESSION_TOKEN`
  and `TICK_USERNAME` (the account e-mail, optional, kept only to pre-fill the refresh form). The
  password exists only inside the `admin session-refresh` HITL form, is exchanged for a new session
  token via `POST /user/signon`, and is discarded immediately. Credentials (username + password)
  are requested **only when the session token is invalid** — never otherwise. The stored-password
  auto-login pattern of `tick-mcp` (`TICKTICK_PASSWORD` + `_v2_login`) is dropped.
- **Single `admin.py`** instead of `admin/service.py`. The admin logic (610 lines in `tick-mcp`,
  merged) lives in ONE `src/tick_proxy/admin.py` — setup, status, session-refresh.
- **Verification model detailed + plausibility confirmed.** Added the full `--verify/-V`
  walkthrough (write → read-back → compare → report) and a web-checked plausibility table
  (independent sources: `dev-mirzabicer/ticktick-sdk`, `jaeyeonling/ticktick-client`,
  `MHoroszowski/ticktick-client` — `parentId` ignored at creation, "200 but no actual change",
  `/habits/batch` full replacement, `complete_tasks` date wipe, `update_tasks` silent re-activation).
- **`presets.json` content + role illustrated** — full JSON schema (name, query_type, filters,
  description, created_at/updated_at) and its role as the persistence layer of the 4 preset
  actions. No credentials ever enter this file.
- **`services/` roles illustrated** — `query.py` = the filter engine (scope resolution, time
  windows, grep-like matching, shape filters, ordering); `presets.py` = `presets.json`
  persistence.
- **`D5` decision updated** — `admin setup` form now has **3 persisted fields** (API token,
  session token, username) + clear checkboxes; password never stored.
- **Docs updated across the board** — README (admin table, `.env` block, V2 bullet, password
  note), AGENTS.md (overview, key files `logger.py`/`admin.py`, rules), TODO.md (P1 logger, P2
  admin.py + three-field setup form).

### Design refinement pass 3 (2026-08-09 — KπX directives)

- **`verified` field purged from `meta`.** The envelope now carries only `verification` —
  a non-empty `meta.verification` object means verified, `{}`/absent means not verified. A separate
  `verified` boolean was redundant (KπX decision). Applied across CONTRACT (envelope, meta field
  table, verification model, scenario, error model), README, AGENTS.md, TODO.md.
- **`--verify/-V` flag removed entirely — verification is structural.** The read-back verification
  is now enforced by an **`@always_verify(...)` decorator** on the handler of the actions that need
  it (`task-parent-set`, `project-create`/`project-update` with `group_id`, `habit-update`,
  `task-move`). There is no CLI flag, no opt-in, no bypass: `cli.py` has no code path to skip it,
  and `make smoke` fails (AST check) if a `verify="always"` action lacks the decorator. The flag
  references were purged from CONTRACT (meta options, design tree, coverage proof, error model,
  D2, P5), README, AGENTS.md, TODO.md.
- **Presets purged — replaced by the payload file-path + `k-tick` skill assets.** There is **no
  `presets.json`, no `preset-*` actions, no `services/presets.py`** (KπX decision: it
  over-complicated the design for nothing). Recurring queries are plain JSON files owned by the
  future `k-tick` skill (`assets/`), invoked through the **existing payload file-path mechanism**:
  `tick-proxy do query-tasks /path/to/k-tick/assets/revision-week.json`. The code stays 100 %
  business. The 4 `tick-mcp` preset tools (`list_query_presets`, `save_query_preset`,
  `run_query_preset`, `delete_query_preset`) and `services/query_presets.py` are **dropped, not
  ported** (coverage proof + ported list updated).
- **Action count 65 → 61.** Catalog, coverage proof, README table, AGENTS overview, TODO gates
  and `make smoke` all updated to **61 `do` actions** (60 renamed + 1 `raw`). HITL-required goes
  from 10 to **9 `do` actions** + 2 `admin` commands (`preset-delete` removed).

### Design refinement pass 4 (2026-08-09 — KπX directive)

- **Structure flattened — `services/` removed.** The filter engine now lives directly at
  **`src/tick_proxy/query.py`** (ported from `tick_mcp/services/query.py`, 754 lines), no
  `services/` package. Rationale: after the presets purge, `services/` held exactly one module —
  a one-file folder was unnecessary weight. Architecture tree, `query.py` roles section, ported
  list, P4 plan, AGENTS.md Key Files and TODO.md updated accordingly.
