"""
tick-proxy: TickTick administrative proxy — RPC CLI for tasks, projects, habits and queries.

Config: ~/.config/tick-proxy/.env (TICK_API_TOKEN, TICK_SESSION_TOKEN, TICK_USERNAME).
The TickTick password is NEVER stored — see `admin session-refresh`.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tick-proxy")
except PackageNotFoundError:  # pragma: no cover - only when running from source tree
    __version__ = "0.0.0"
