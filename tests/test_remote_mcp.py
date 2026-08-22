import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ddg_search.remote_mcp import (
    RemoteRpcError,
    RemoteToolError,
    RemoteTransportError,
    _parse_sse_messages,
    call_remote_tool,
)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def make_server(paths: dict[str, str]):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            self.rfile.read(length)
            body = paths.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            if not self.path.endswith("/no-session"):
                self.send_header("mcp-session-id", "sess-1")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


INIT_RESULT = sse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}})


def test_parse_sse_messages_collects_frames_and_ignores_junk():
    # SSE frames newline-join multiple data: lines; only complete JSON frames parse.
    text = 'data: {"a": 1}\n\nevent: noise\ndata: not-json\n\ndata: {"b": 2}\ndata: {"c": 3}\n\n'
    assert _parse_sse_messages(text) == [{"a": 1}]


def test_call_remote_tool_rejects_empty_url():
    with pytest.raises(RemoteTransportError, match="URL is empty"):
        import asyncio

        asyncio.run(call_remote_tool("", "search", {}, 1000))


def test_call_remote_tool_returns_text():
    tool = sse({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "5 results:\n1. T"}]}})
    server, url = make_server({"/": INIT_RESULT + tool})
    try:
        result = __import__("asyncio").run(call_remote_tool(url, "search", {"query": "q"}, 5000))
        assert result.text == "5 results:\n1. T"
    finally:
        server.shutdown()


def test_call_remote_tool_raises_on_is_error():
    tool = sse({"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": [{"type": "text", "text": "bad"}]}})
    server, url = make_server({"/err": INIT_RESULT + tool})
    try:
        with pytest.raises(RemoteToolError, match="remote tool isError: bad"):
            __import__("asyncio").run(call_remote_tool(f"{url}/err", "search", {}, 5000))
    finally:
        server.shutdown()


def test_call_remote_tool_raises_rpc_error():
    body = sse({"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "no such tool"}})
    server, url = make_server({"/rpcerr": INIT_RESULT + body})
    try:
        with pytest.raises(RemoteRpcError, match="no such tool"):
            __import__("asyncio").run(call_remote_tool(f"{url}/rpcerr", "search", {}, 5000))
    finally:
        server.shutdown()


def test_call_remote_tool_requires_session_id():
    server, url = make_server({"/no-session": INIT_RESULT})
    try:
        with pytest.raises(RemoteTransportError, match="mcp-session-id"):
            __import__("asyncio").run(call_remote_tool(f"{url}/no-session", "search", {}, 5000))
    finally:
        server.shutdown()
