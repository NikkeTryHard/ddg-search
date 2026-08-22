from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import cast

from duckduckgo_mcp_server.server import DuckDuckGoSearcher, SafeSearchMode
from mcp.server.fastmcp import Context

from .config import (
    BACKENDS,
    ERROR_COOLDOWN_MS,
    PROBE_TIMEOUT_MS,
    SEARCH_LIMIT_PER_MIN,
    SEARCH_TIMEOUT_MS,
    TIMEOUT_COOLDOWN_MS,
)
from .models import BackendConfig
from .remote_mcp import RemoteRpcError, RemoteToolError, RemoteTransportError, call_remote_tool
from .state import SearchState, now_iso


class NullContext:
    async def info(self, _message: str) -> None:
        return None

    async def error(self, _message: str) -> None:
        return None


EMPTY_RESULTS_PREFIX = "No results were found for your search query"


def format_results_compact(results: list) -> str:
    """Token-lean rendering of SearchResult rows: number, title, url, snippet.

    Zero-result case must keep the upstream prefix verbatim — the router's
    failure classifier matches on it."""
    if not results:
        return (
            f"{EMPTY_RESULTS_PREFIX}. This could be due to DuckDuckGo's bot detection or the query returned no matches."
        )
    lines = [f"{len(results)} results:"]
    for result in results:
        lines.append(f"{result.position}. {result.title}")
        lines.append(result.link)
        if result.snippet:
            lines.append(result.snippet)
    return "\n".join(lines)


_REMOTE_HEADER_RE = re.compile(r"^Found \d+ search results:$")


def compact_remote_text(text: str) -> str:
    """Rewrite upstream 'N. title / URL: / Summary:' blocks into the compact
    shape. Unknown shapes pass through untouched so classifier prefixes and
    future upstream format changes stay safe."""
    lines = text.splitlines()
    if not lines or not _REMOTE_HEADER_RE.match(lines[0].strip()):
        return text
    out: list[str] = [f"{lines[0].strip()[len('Found ') : -len(' search results:')]} results:"]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("URL: ") or stripped.startswith("Summary: "):
            out.append(stripped.split(": ", 1)[1])
        else:
            out.append(stripped)
    return "\n".join(out)


def make_local_searcher() -> DuckDuckGoSearcher:
    # 0.5.0 best search path: auto = httpx first, curl_cffi Chrome TLS on 202/403.
    backend = (os.getenv("DDG_SEARCH_BACKEND", "auto") or "auto").lower()
    if backend not in {"httpx", "curl", "auto"}:
        backend = "auto"
    # Content filter only (not bot defense). Default OFF for agent research recall.
    safe_name = (os.getenv("DDG_SAFE_SEARCH", "OFF") or "OFF").upper()
    try:
        safe_search = SafeSearchMode[safe_name]
    except KeyError:
        safe_search = SafeSearchMode.OFF
    return DuckDuckGoSearcher(safe_search=safe_search, default_region="", backend=backend)


class SearchRouter:
    def __init__(self) -> None:
        self.state = SearchState()
        self.local_searcher = make_local_searcher()

    def resolve_backend(self, token: str | None) -> BackendConfig | None:
        if not token:
            return None
        normalized = token.strip().lower()
        for backend in BACKENDS:
            if normalized in {backend.id, backend.name, backend.ip, *(alias.lower() for alias in backend.aliases)}:
                return backend
        return None

    def resolve_targets(self, target: str | None, targets: list[str] | None) -> list[BackendConfig]:
        requested: list[str] = []
        if target and target.strip():
            requested.append(target.strip())
        if targets:
            requested.extend(value.strip() for value in targets if isinstance(value, str) and value.strip())
        if not requested:
            return list(BACKENDS)
        resolved: list[BackendConfig] = []
        seen: set[str] = set()
        for token in requested:
            backend = self.resolve_backend(token)
            if not backend:
                raise ValueError(f"Unknown backend target: {token}")
            if backend.id not in seen:
                seen.add(backend.id)
                resolved.append(backend)
        return resolved

    def _now_ts(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def _is_cooling(self, backend: BackendConfig) -> bool:
        cooldown_until = self.state.entry(backend).cooldown_until
        return bool(cooldown_until and datetime.fromisoformat(cooldown_until).timestamp() > self._now_ts())

    def auto_order(self, backends: list[BackendConfig], kind: str) -> list[BackendConfig]:
        self.state.refresh_usage()
        return sorted(
            backends,
            key=lambda backend: (
                1 if self._is_cooling(backend) else 0,
                len(self.state.entry(backend).search_timestamps),
                self.state.entry(backend).last_used_at or "",
            ),
        )

    def available_backends(self, backends: list[BackendConfig], route_mode: str, kind: str) -> list[BackendConfig]:
        ordered = backends if route_mode == "manual" else self.auto_order(backends, kind)
        if route_mode == "manual":
            return ordered
        return [backend for backend in ordered if not self._is_cooling(backend)] + [
            backend for backend in ordered if self._is_cooling(backend)
        ]

    def mark_attempt(self, backend: BackendConfig, kind: str) -> None:
        entry = self.state.entry(backend)
        current_iso = now_iso()
        current_ts = self._now_ts()
        entry.last_used_at = current_iso
        entry.last_search_started_at = current_iso
        entry.search_timestamps.append(current_ts)
        entry.search_timestamps = [ts for ts in entry.search_timestamps if current_ts - ts < 60.0]
        self.state.save()

    def mark_success(self, backend: BackendConfig, kind: str) -> None:
        entry = self.state.entry(backend)
        current_iso = now_iso()
        entry.last_result_at = current_iso
        entry.last_status = "ok"
        entry.last_error = None
        entry.cooldown_until = None
        entry.last_search_finished_at = current_iso
        self.state.save()

    def mark_failure(self, backend: BackendConfig, status: str, error: str, cooldown_ms: int) -> None:
        entry = self.state.entry(backend)
        current_iso = now_iso()
        entry.last_result_at = current_iso
        entry.last_status = status  # type: ignore[assignment]
        entry.last_error = error
        entry.cooldown_until = datetime.fromtimestamp(
            self._now_ts() + (cooldown_ms / 1000.0), tz=timezone.utc
        ).isoformat()
        entry.last_search_finished_at = current_iso
        self.state.save()

    async def _search_local(self, query: str, max_results: int, region: str, ctx: Context | None) -> str:
        effective_ctx = ctx or NullContext()
        results = await asyncio.wait_for(
            # The upstream searcher only calls ctx.info/ctx.error for logging;
            # NullContext satisfies that duck-type but not the concrete class.
            self.local_searcher.search(query, cast("Context", effective_ctx), max_results, region),
            timeout=SEARCH_TIMEOUT_MS / 1000.0,
        )
        return format_results_compact(results)

    async def _search_remote(self, url: str, query: str, max_results: int, region: str) -> str:
        result = await call_remote_tool(
            url, "search", {"query": query, "max_results": max_results, "region": region}, SEARCH_TIMEOUT_MS
        )
        return compact_remote_text(result.text)

    def _result_failure(self, text: str) -> tuple[str, str] | None:
        """Return (kind, detail) for non-success tool bodies, else None."""
        stripped = (text or "").strip()
        if stripped.startswith("No results were found for your search query"):
            return (
                "empty",
                "DuckDuckGo returned no matches (empty page or bot-empty); not a remote package error",
            )
        if stripped.startswith("An error occurred while searching:"):
            detail = stripped.removeprefix("An error occurred while searching:").strip()
            return ("remote-tool-error", f"remote search tool soft-failed: {detail}")
        if stripped.startswith("Unknown tool:"):
            return ("remote-tool-error", stripped)
        return None

    def _classify_exception(self, exc: Exception) -> tuple[str, str, str, int]:
        """Return (status, kind, detail, cooldown_ms)."""
        if isinstance(exc, RemoteToolError):
            return ("error", "remote-tool-error", str(exc), ERROR_COOLDOWN_MS)
        if isinstance(exc, RemoteTransportError):
            return ("error", "local-transport", str(exc), ERROR_COOLDOWN_MS)
        if isinstance(exc, RemoteRpcError):
            return ("error", "remote-rpc", str(exc), ERROR_COOLDOWN_MS)
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return ("timeout", "timeout", f"timeout waiting for backend: {exc}", TIMEOUT_COOLDOWN_MS)
        message = str(exc)
        lowered = message.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return ("timeout", "timeout", f"timeout: {type(exc).__name__}: {exc}", TIMEOUT_COOLDOWN_MS)
        return (
            "error",
            "local",
            f"local {type(exc).__name__} while targeting this backend "
            f"(do not assume the remote host lacks packages): {exc}",
            ERROR_COOLDOWN_MS,
        )

    def _format_attempt(self, backend: BackendConfig, kind: str, detail: str) -> str:
        return f"- {backend.name} ({backend.ip}) [{kind}]: {detail}"

    def _failure_banner(self, kinds: list[str], deadline_hit: bool) -> str:
        if not kinds:
            return "No backends were attempted."
        unique = set(kinds)
        if unique == {"empty"}:
            return (
                "All backends returned empty DuckDuckGo results. "
                "This is usually no-matches/bot-empty, not a 60s network hang."
            )
        if unique <= {"local", "local-transport"}:
            return (
                "All backends failed in the local ddg-search client/transport path. "
                "Debug this machine's ddg-search process first; remote VPS package installs are unlikely."
            )
        if "timeout" in unique and deadline_hit:
            return f"Search budget exhausted ({SEARCH_TIMEOUT_MS}ms) with timeouts before a usable result."
        if deadline_hit:
            return f"Search budget exhausted ({SEARCH_TIMEOUT_MS}ms) before a usable result."
        return "No backend returned usable search results."

    async def search(
        self,
        query: str,
        max_results: int,
        region: str,
        route_mode: str,
        target: str | None,
        targets: list[str] | None,
        ctx: Context | None,
    ) -> str:
        candidates = self.resolve_targets(target, targets)
        ordered = self.available_backends(candidates, route_mode, "search")
        attempt_lines: list[str] = []
        attempt_kinds: list[str] = []
        deadline = time.monotonic() + (SEARCH_TIMEOUT_MS / 1000.0)
        deadline_hit = False

        for backend in ordered:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                deadline_hit = True
                break
            self.mark_attempt(backend, "search")
            try:
                if backend.kind == "local":
                    result = await asyncio.wait_for(
                        self._search_local(query, max_results, region, ctx),
                        timeout=remaining,
                    )
                else:
                    result = await asyncio.wait_for(
                        self._search_remote(backend.url or "", query, max_results, region),
                        timeout=remaining,
                    )

                bad = self._result_failure(result)
                if bad:
                    kind, detail = bad
                    self.mark_failure(backend, "error", detail, ERROR_COOLDOWN_MS)
                    attempt_kinds.append(kind)
                    attempt_lines.append(self._format_attempt(backend, kind, detail))
                    if route_mode == "manual":
                        return f"No usable result from requested backend.\nAttempts:\n{attempt_lines[-1]}"
                    continue

                self.mark_success(backend, "search")
                trail = "\n".join(attempt_lines)
                prefix = f"via {backend.name}"
                if trail:
                    prefix += f"\nPrior attempts:\n{trail}"
                return f"{prefix}\n\n{result}".strip()
            except Exception as exc:
                status, kind, detail, cooldown = self._classify_exception(exc)
                self.mark_failure(backend, status, detail, cooldown)
                attempt_kinds.append(kind)
                attempt_lines.append(self._format_attempt(backend, kind, detail))
                continue

        if time.monotonic() >= deadline:
            deadline_hit = True

        banner = self._failure_banner(attempt_kinds, deadline_hit)
        body = "\n".join(attempt_lines) if attempt_lines else "- none"
        return f"{banner}\nAttempts:\n{body}"

    async def probe(self, backend: BackendConfig) -> None:
        entry = self.state.entry(backend)
        entry.last_probe_at = now_iso()
        try:
            if backend.kind == "local":
                entry.last_probe_ok = True
                entry.last_status = "probe-ok"
            else:
                result = await call_remote_tool(
                    backend.url or "",
                    "search",
                    {
                        "query": "Tjong transformer Mahjong AI hierarchical decision-making fan backward IET 2024",
                        "max_results": 1,
                        "region": "",
                    },
                    PROBE_TIMEOUT_MS,
                )
                bad = self._result_failure(result.text)
                if bad:
                    raise RuntimeError(bad[1])
                entry.last_probe_ok = True
                entry.last_status = "probe-ok"
        except Exception as exc:
            entry.last_probe_ok = False
            entry.last_status = "probe-failed"
            if isinstance(exc, (RemoteTransportError, RemoteRpcError)):
                entry.last_error = str(exc)
            else:
                entry.last_error = f"local {type(exc).__name__}: {exc}"
        self.state.save()

    async def status_text(self, probe: bool, target: str | None, targets: list[str] | None) -> str:
        backends = self.resolve_targets(target, targets)
        if probe:
            for backend in backends:
                await self.probe(backend)
        self.state.refresh_usage()
        lines = [
            "ddg-search router: auto tries backends sequentially, preferring non-cooling backends "
            "with lowest recent attempt count; manual uses target order.",
            f"Search budget: {SEARCH_TIMEOUT_MS}ms total across backends. "
            "State: package-local under ~/.local/share/mcp/ddg-search/state.",
            f"Observed search attempts in this process only. Each backend package enforces {SEARCH_LIMIT_PER_MIN}/min.",
            "[empty] is ambiguous upstream output: genuine zero matches, bot-empty, parse-empty, "
            "and backend-swallowed request errors are indistinguishable.",
            "",
            "| name | ip | online | search_obs | last_used | last_result | status | cooldown_until | route |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for backend in backends:
            entry = self.state.entry(backend)
            online = "unknown" if entry.last_probe_ok is None else ("yes" if entry.last_probe_ok else "no")
            lines.append(
                f"| {backend.name} | {backend.ip} | {online} | {len(entry.search_timestamps)}/{SEARCH_LIMIT_PER_MIN} | "
                f"{entry.last_used_at or '-'} | {entry.last_result_at or '-'} | {entry.last_status} | "
                f"{entry.cooldown_until or '-'} | {backend.kind} |"
            )
            if entry.last_error and entry.last_status in {"error", "timeout", "probe-failed"}:
                lines.append(f"|  |  |  | last_error | {entry.last_error} |  |  |  |  |")
        return "\n".join(lines)
