from __future__ import annotations

import os
from pathlib import Path

from .models import BackendConfig


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


# All runtime state lives under the install tree default:
# ~/.local/share/mcp/ddg-search/state
# Override only with DDG_SEARCH_STATE_DIR (no separate config/ path).
SEARCH_TIMEOUT_MS = int(_env("DDG_SEARCH_TIMEOUT_MS", "25000"))
PROBE_TIMEOUT_MS = int(_env("DDG_SEARCH_PROBE_TIMEOUT_MS", "3000"))
TIMEOUT_COOLDOWN_MS = int(_env("DDG_SEARCH_TIMEOUT_COOLDOWN_MS", "90000"))
ERROR_COOLDOWN_MS = int(_env("DDG_SEARCH_ERROR_COOLDOWN_MS", "30000"))
SEARCH_LIMIT_PER_MIN = 30

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STATE = PACKAGE_ROOT / "state"
STATE_DIR = Path(_env("DDG_SEARCH_STATE_DIR", str(_DEFAULT_STATE))).expanduser()
STATE_FILE = STATE_DIR / "router-state.json"

BACKENDS: tuple[BackendConfig, ...] = (
    BackendConfig(
        id="local",
        name="local",
        label="this-machine",
        ip="127.0.0.1",
        kind="local",
        aliases=("local", "this-machine", "127.0.0.1", "127.0.1.1", "localhost", "current"),
    ),
    # Remotes: stock duckduckgo-mcp-server 0.5.0[browser], search backend=auto
    BackendConfig(
        id="relay-b",
        name="relay-b",
        label="oc-relay-b",
        ip="167.71.126.78",
        kind="remote",
        url="http://167.71.126.78/ddg-mcp",
        aliases=("relay-b", "oc-relay-b", "167.71.126.78"),
    ),
    BackendConfig(
        id="relay-d",
        name="relay-d",
        label="oc-relay-d",
        ip="157.230.147.4",
        kind="remote",
        url="http://157.230.147.4/ddg-mcp",
        aliases=("relay-d", "oc-relay-d", "157.230.147.4"),
    ),
)
