"""Serious-failure diagnostics: one JSON file per broken search, nothing else.

Timeouts and empty result sets are the internet being the internet — they get
a tag in the response, not a log file. Tool-side breakage gets a log, because
that is the stuff worth replaying."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import PACKAGE_ROOT

LOGS_DIR = Path(os.getenv("DDG_SEARCH_LOGS_DIR", str(PACKAGE_ROOT / "logs")))

# Attempt kinds that mean *we* broke (or a relay we own did), as opposed to
# the site/DDG being flaky ([timeout], [empty]).
SERIOUS_KINDS = {"local", "local-transport", "remote-tool-error", "remote-rpc"}


def write_failure_log(payload: dict) -> str | None:
    """Persist one failure record; returns its path or None if unwritable.

    Diagnostics must never break the search path, hence the bare except."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = LOGS_DIR / f"{stamp}-{payload.get('kind', 'error')}.json"
        path.write_text(json.dumps(payload, indent=2, default=str))
        return str(path)
    except Exception:
        return None
