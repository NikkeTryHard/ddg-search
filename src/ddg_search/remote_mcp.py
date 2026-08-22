from __future__ import annotations

from dataclasses import dataclass
import json
import time

import httpx


@dataclass
class RemoteToolResult:
    text: str


class RemoteTransportError(RuntimeError):
    """Client-side or transport failure talking to a remote backend."""

    def __init__(self, stage: str, detail: str, *, http_completed: bool = False) -> None:
        self.stage = stage
        self.http_completed = http_completed
        self.detail = detail
        where = "after HTTP" if http_completed else "no completed remote tool HTTP"
        super().__init__(f"local/client {stage} ({where}): {detail}")


class RemoteToolError(RuntimeError):
    """Remote MCP tool completed and returned isError."""


class RemoteRpcError(RuntimeError):
    """Remote returned a JSON-RPC error object."""

    def __init__(self, error: object) -> None:
        self.error = error
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if message is not None and code is not None:
                text = f"remote RPC error code={code}: {message}"
            elif message is not None:
                text = f"remote RPC error: {message}"
            else:
                text = f"remote RPC error: {error}"
        else:
            text = f"remote RPC error: {error}"
        super().__init__(text)


def _parse_sse_messages(text: str) -> list[dict]:
    messages: list[dict] = []
    data_lines: list[str] = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    messages.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
                data_lines = []
    if data_lines:
        payload = "\n".join(data_lines)
        try:
            messages.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return messages


async def _post(url: str, session_id: str | None, payload: dict, timeout_s: float) -> httpx.Response:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        return await client.post(url, headers=headers, json=payload)


def _transport_from_exc(stage: str, exc: Exception, *, http_completed: bool = False) -> RemoteTransportError:
    if isinstance(exc, RemoteTransportError):
        return exc
    detail = f"{type(exc).__name__}: {exc}"
    return RemoteTransportError(stage, detail, http_completed=http_completed)


async def call_remote_tool(url: str, tool_name: str, arguments: dict, timeout_ms: int) -> RemoteToolResult:
    if not url:
        raise RemoteTransportError("config", "remote backend URL is empty", http_completed=False)

    started = time.monotonic()
    init_timeout = max(1.0, min(timeout_ms / 1000.0, 5.0))

    try:
        init_response = await _post(
            url,
            None,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ddg-search", "version": "0.2.0"},
                },
            },
            init_timeout,
        )
    except Exception as exc:
        raise _transport_from_exc("initialize", exc, http_completed=False) from exc

    try:
        init_response.raise_for_status()
    except Exception as exc:
        raise _transport_from_exc(
            "initialize",
            exc,
            http_completed=True,
        ) from exc

    session_id = init_response.headers.get("mcp-session-id")
    if not session_id:
        raise RemoteTransportError("initialize", "remote returned no mcp-session-id", http_completed=True)

    _parse_sse_messages(init_response.text)
    remaining = max(1.0, (timeout_ms / 1000.0) - (time.monotonic() - started))

    try:
        notify_response = await _post(
            url,
            session_id,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            min(remaining, 5.0),
        )
    except Exception as exc:
        raise _transport_from_exc("initialized-notify", exc, http_completed=False) from exc

    if notify_response.status_code not in (200, 202):
        try:
            notify_response.raise_for_status()
        except Exception as exc:
            raise _transport_from_exc("initialized-notify", exc, http_completed=True) from exc

    remaining = max(1.0, (timeout_ms / 1000.0) - (time.monotonic() - started))
    try:
        response = await _post(
            url,
            session_id,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            remaining,
        )
    except Exception as exc:
        raise _transport_from_exc("tools/call", exc, http_completed=False) from exc

    try:
        response.raise_for_status()
    except Exception as exc:
        raise _transport_from_exc("tools/call", exc, http_completed=True) from exc

    messages = _parse_sse_messages(response.text)
    for message in messages:
        if message.get("id") == 2 and "result" in message:
            content = message["result"].get("content", [])
            text = "\n\n".join(item.get("text", "") for item in content if item.get("type") == "text").strip()
            is_error = bool(message["result"].get("isError"))
            if is_error and text:
                raise RemoteToolError(f"remote tool isError: {text}")
            return RemoteToolResult(text=text)
        if message.get("id") == 2 and "error" in message:
            raise RemoteRpcError(message["error"])
    raise RemoteTransportError("tools/call", "remote returned no tool result", http_completed=True)
