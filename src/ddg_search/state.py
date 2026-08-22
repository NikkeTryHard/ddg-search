from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from .config import BACKENDS, STATE_DIR, STATE_FILE
from .models import BackendConfig, BackendState


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def trim_timestamps(items: list[float], now_ts: float) -> list[float]:
    return [ts for ts in items if now_ts - ts < 60.0]


class SearchState:
    def __init__(self) -> None:
        self.backends: dict[str, BackendState] = {}
        self.load()

    def load(self) -> None:
        ensure_state_dir()
        if not STATE_FILE.exists():
            self.backends = {backend.id: BackendState() for backend in BACKENDS}
            self.save()
            return
        try:
            raw = json.loads(STATE_FILE.read_text())
            loaded: dict[str, BackendState] = {}
            for backend in BACKENDS:
                payload = dict(raw.get("backends", {}).get(backend.id, {}))
                payload.pop("fetch_timestamps", None)
                payload.pop("last_fetch_started_at", None)
                payload.pop("last_fetch_finished_at", None)
                loaded[backend.id] = BackendState(**payload)
            self.backends = loaded
        except Exception:
            self.backends = {backend.id: BackendState() for backend in BACKENDS}
            self.save()

    def save(self) -> None:
        ensure_state_dir()
        STATE_FILE.write_text(
            json.dumps({"backends": {key: asdict(value) for key, value in self.backends.items()}}, indent=2)
        )

    def refresh_usage(self) -> None:
        now_ts = datetime.now(timezone.utc).timestamp()
        for entry in self.backends.values():
            entry.search_timestamps = trim_timestamps(entry.search_timestamps, now_ts)

    def entry(self, backend: BackendConfig) -> BackendState:
        return self.backends[backend.id]
