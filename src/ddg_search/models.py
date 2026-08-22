from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BackendStatus = Literal["unknown", "ok", "timeout", "error", "probe-ok", "probe-failed"]


@dataclass(frozen=True)
class BackendConfig:
    id: str
    name: str
    label: str
    ip: str
    kind: Literal["local", "remote"]
    url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class BackendState:
    last_used_at: str | None = None
    last_result_at: str | None = None
    last_status: BackendStatus = "unknown"
    last_error: str | None = None
    cooldown_until: str | None = None
    search_timestamps: list[float] = field(default_factory=list)
    last_search_started_at: str | None = None
    last_search_finished_at: str | None = None
    last_probe_at: str | None = None
    last_probe_ok: bool | None = None
