# TODO

## 🔴 GATE — validation required before any code

The 11 decisions in `CONTRACT.md` → *Decisions requiring KπX validation* must be answered first.
Summary of what is being asked:

- [ ] **D1** — Action naming flipped to domain-first kebab (`task-create`, not `create-task`)
- [x] **D2** — `verified_*` tools folded into `@require_verification` (no CLI flag); proof appears only in `data.verification`, including task create/update document fields.
- [ ] **D3** — `ticktick_guide` dropped in favour of docstring-driven `do --help`
- [ ] **D4** — `check_v2_availability` folded into `admin status`
- [ ] **D5** — 8 `tick-admin` credential subcommands folded into ONE `admin setup` HITL form
- [ ] **D6** — `config.yaml` dropped (documented defaults in `config.py`, overridable via `.env`)
- [ ] **D7** — Env prefix `TICK_*` (harmonizes with `TG_*`)
- [ ] **D8** — HTTP transport + Telegram admin bot + PID daemon dropped
- [x] **D9** — HITL now covers deletions + `tag-merge` + `raw` + admin secrets, mandatory
       `task-create` / `task-update` / `subtask-create`, and simple full-JSON batch task writes;
       every individual task kind uses full editable JSON plus three inline
      title/content/desc patches, returning `data.diff.{title_diff,content_diff,desc_diff}` and
      no `meta.review` wrapper.
- [ ] **D10** — `actions/` registry layout instead of a monolithic `client.py`
- [ ] **D11** — `~/Work/AI/MCPs/tick_mcp/` kept as reference until parity, then archived

## Done (design phase)

- [x] Scoped V2 authentication repair (2026-08-12): canonical login headers, reference-compatible
       MFA request shape, email device-approval retry, and safe `access_forbidden` reporting covered
       by mocked tests; no live login performed.

- [x] Task-write HITL v2 architecture (2026-08-12): `task-create` / `task-update` / `subtask-create` always open
      one shared full-JSON review for every task kind. `title_ops`, `content_ops`, `desc_ops`
      are exact preflighted `replace`/`insert` lists; the page adds three editable inline Monaco
      patches. Final output is read back from TickTick and returns title/content/desc plus the
      three field-local diffs; no `meta.review` exists. Mocked/local HTTP plus real remote update
       and read-back cover operation preflight, editable JSON metadata, inline edits, and persistence.

- [x] Subtask + batch + outcome harmonization (2026-08-12): `subtask-create` now receives the
       operation-first task review, then creates, links to `parentId`, and verifies all four final
       fields. Batch create/update retain native V2 free-form payloads but now have one full-JSON
       HITL review. Rejections from `do`, task review, and admin all emit the same envelope and exit
       status, while admin does not use the `do` configuration gate. Real E2E created, reviewed,
       linked, verified, batch-created/updated, and removed isolated TickTick data; V2
       `/batch/project` replaced a V1 project deletion path that did not remove live projects.

- [x] Exhaustive analysis of `tg-proxy` ADN — `cli.py` (Typer `do`/`admin`, `_make_rpc` factory,
      autosave, meta options), `client.py` (docstring format), `models.py` (Pydantic payloads),
      `config.py` (`.env` loader), `doc.py` (`get_full_help` / `get_compact_help`),
      `display.py`, `logger.py`, `exceptions.py`, `hitl.py` (`require_approval`, free port,
      HTML template), `Makefile`, `pyproject.toml`, `.gitlab-ci.yml`, `scripts/`, `.gitignore`
- [x] Exhaustive analysis of `tick-mcp` — 71 tools across 13 `mcp_api/` modules with category and
      auth level, `client_api/` transport (V1/V2, 401 re-login, session cache),
      `tick_mcp/services/query.py` filter engine (→ ported flat as `src/tick_proxy/query.py`),
      `admin/` (cli + service + telegram), `config.py`,
      `config.yaml`, `.env.example`, `http_app.py`, `daemon.py`, tests (unit + 12 live scripts)
- [x] Complete 71 → 52 action mapping with coverage proof (zero gaps)
- [x] `CONTRACT.md` — architecture contract
- [x] `AGENTS.md` — agent working context
- [x] `README.md` — user-facing documentation skeleton
- [x] `CHANGELOG.md` — 0.1.0 design entry
- [x] `TODO.md` — this file

## Implementation phases (after the gate)

### P0 — Skeleton
- [ ] `pyproject.toml` — `tick-proxy = "tick_proxy.cli:app"`, `uv_build` backend, deps
      (`typer`, `httpx`, `pydantic`, `rich`, `python-dotenv`, `pyyaml` only if still needed)
- [ ] `Makefile` — `tg-proxy` targets **minus** all `docker-*`
- [ ] `.gitignore`, `.env.example` (the fully commented block from `CONTRACT.md`), `.gitlab-ci.yml`
      (validate → build → publish, no docker stage)
- [ ] Package directory tree (`src/tick_proxy/{api,actions,services,admin,templates}/`)
- [ ] `git init` + remotes `github: git@github.com:kpihx-labs/tick-proxy.git`,
      `gitlab: git@gitlab.com:kpihx-labs/proxies/tick-proxy.git` + repo creation (`gh`, `glab`)

### P1 — Core
- [ ] `config.py` — `.env` loader, documented endpoint defaults, all overridable
- [ ] `exceptions.py` — `TickProxyError`
- [ ] `logger.py` — stderr logger only (systemd/journald captures — like `tg-proxy`, **no file**, no rotation)
- [ ] `display.py` — `print_json`, `print_table`, `print_meta`, `print_error`
- [ ] `doc.py` — `get_full_help` / `get_compact_help` (ported verbatim from `tg-proxy`)
- [ ] `models.py` — `Output`, `OutputMeta`, `Priority`, `TickTickAPIError`
- [ ] `client.py` — `TickClient` (auth state, `v1_get/post/delete`, `v2_get/post/put/delete`)
- [ ] `api/transport.py` — ported from `tick_mcp/client_api/transport.py` (V1/V2, 401 re-login)
- [ ] **Gate:** `admin status` returns real data from the live API

### P2 — HITL + admin
- [ ] `hitl.py` + `templates/hitl.html` (ported from `tg-proxy`)
- [ ] `templates/setup.html` — the three-field credential form (API token, session token, username) with clear checkboxes; **no password field, ever**
- [ ] `admin.py` — single source of truth (status payload, env read/write, transient credential collection for session-refresh)
- [ ] `admin.py` — `setup`, `status`, `session-refresh`
- [ ] **Gate:** full auth lifecycle proven live, including MFA/device code path, with the password never touching disk

### P3 — Action framework
- [ ] `actions/base.py` — `ActionDef`, payload validation, `verify()` helper
- [ ] `actions/registry.py` — aggregation + duplicate detection at import
- [ ] `cli.py` — ONE Typer app building `do` commands from the registry, `admin` sub-typer,
      meta options, autosave, envelope, exit codes
- [ ] `actions/tasks.py` — first 5 write actions as the reference implementation
- [ ] **Gate:** `do --help`, `do task-create --help`, `do task-create` all work live

### P4 — Port all actions
- [ ] `api/{projects,tasks,habits,stats}.py` — ported endpoint wrappers
- [ ] `query.py` — the filter engine (ported from `tick_mcp/services/query.py`, flat at `src/tick_proxy/`)
- [ ] `actions/{tasks_batch,projects,tags,habits,query,views,history,stats,sync,builders}.py`
- [ ] **Gate:** registry = 52 actions, `make smoke` green, every action has a full docstring with
      `Parameters:` and at least 2 `Examples:` showing real `→` output

### P5 — Verification engine
- [x] `@require_verification` decorator in `actions/base.py`; no `ActionDef.verify` field
- [ ] Always-on verification for `task-parent-set`, `project-create`/`project-update` (`group_id`),
      `habit-update`, `task-move`
- [x] `data.verification` block only for declared verification writes; no meta verification field
- [ ] **Gate:** verified writes proven live, including a deliberately failing verification

### P6 — `raw` gateway
- [ ] `actions/raw.py` — `{"api":"v1|v2","method":…,"endpoint":…,"params":…,"payload":…}` + HITL
- [ ] **Gate:** live V1 and V2 raw calls proven

### P7 — Tests
- [ ] Port unit tests from `tick_mcp/tests/` (models, query engine, config, admin service)
- [ ] Add registry test (52 actions, no duplicates, every action has a docstring with `Examples:`)
- [ ] Adapt the 12 `tests/live/` scripts to drive the CLI instead of MCP tools
- [ ] `scripts/smoke.sh` — end-to-end against an isolated config dir
- [ ] **Gate:** `make check` fully green

### P8 — Docs + ecosystem switch
- [ ] `README.md` finalized, `CHANGELOG.md` 1.0.0, `CONTRACT.md` status → ✅ STABLE
- [ ] Remove `mcp.tick_fallback` from `~/.config/opencode/opencode.jsonc`
- [ ] Rewrite `k-ticktick` skill: `allowed-tools` → `Bash(tick-proxy *)`, re-point
      `references/mcp.md` + `references/tool-keep-matrix.md` to the 52 `do` actions
- [ ] Confirm nothing else consumes `https://tick.kpihx-labs.com/mcp` before dropping it
- [ ] Archive `~/Work/AI/MCPs/tick_mcp/` once parity is proven
- [ ] `make release`

## Open questions (non-blocking)

- [ ] Recurring queries = JSON files in the future `k-tick` skill `assets/`, invoked via the
      existing payload file-path (`tick-proxy do query-tasks /path/to/k-tick/assets/q.json`)
      — no preset machinery, code stays 100 % business (KπX decision 2026-08-09)
- [ ] Should `raw` autosave include the request as well as the response? *(useful for auditing)*
- [ ] Shell completions — `tg-proxy` deliberately disables them (`add_completion=False` on both
       Typer apps, no `completions/` directory). Keep that stance for ADN fidelity, or enable
       Typer's `--install-completion` so the 52 action names become tab-completable?
- [ ] **Deferred backlog — native Zsh completions (do not implement yet).** Current unsupported
      invocation: `tick-proxy --show-completion zsh` → `No such option: --show-completion`.
      When the CLI implementation and completion policy are validated, expose a native
      Typer/Click-generated Zsh completion script. If compatible with the chosen mechanism, keep
      its canonical source in the future `k-tick` skill `assets/` and install it through a local
      Zsh completion-path symlink; do not create a second maintained copy. Smoke expectation:
      generation/install is non-interactive, the generated script loads in Zsh, and completion
      resolves `tick-proxy` namespaces and registered `do` action names without real TickTick
      authentication.
