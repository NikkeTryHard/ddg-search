import json
from pathlib import Path

from ddg_search.diagnostics import SERIOUS_KINDS, write_failure_log


def test_serious_kinds_cover_tool_side_only():
    assert "local" in SERIOUS_KINDS
    assert "local-transport" in SERIOUS_KINDS
    assert "remote-tool-error" in SERIOUS_KINDS
    assert "remote-rpc" in SERIOUS_KINDS
    assert "timeout" not in SERIOUS_KINDS
    assert "empty" not in SERIOUS_KINDS


def test_write_failure_log_persists_json(isolated_state, monkeypatch):
    from ddg_search import diagnostics

    logs_dir = isolated_state.parent / "logs"
    monkeypatch.setattr(diagnostics, "LOGS_DIR", logs_dir)
    path = write_failure_log({"kind": "local", "query": "x", "attempts": ["- a [local]: nope"]})
    assert path is not None and str(logs_dir) in path
    record = json.loads(Path(path).read_text())
    assert record["query"] == "x"
    assert record["attempts"] == ["- a [local]: nope"]


def test_write_failure_log_survives_unwritable_dir(monkeypatch):
    from ddg_search import diagnostics

    monkeypatch.setattr(diagnostics, "LOGS_DIR", "/proc/definitely/not/writable")
    assert write_failure_log({"kind": "local"}) is None


def test_search_local_failure_writes_log_and_path_in_output(monkeypatch):
    """A broken local searcher is tool-side: log file + path in the response."""
    import asyncio

    from ddg_search.router import SearchRouter

    async def broken(query, max_results, region, ctx):
        raise RuntimeError("import chain exploded")

    router = SearchRouter()
    monkeypatch.setattr(router, "_search_local", broken)
    out = asyncio.run(router.search("q", 3, "", "manual", "local", None, None))
    assert "log: " in out
    log_path = next(line[5:] for line in out.splitlines() if line.startswith("log: "))
    assert json.loads(Path(log_path).read_text())["attempts"]


def test_search_empty_results_write_no_log(monkeypatch):
    """Empty results are the internet being the internet — tagged, not logged."""
    import asyncio

    from ddg_search.router import EMPTY_RESULTS_PREFIX, SearchRouter

    async def empty(query, max_results, region, ctx):
        return f"{EMPTY_RESULTS_PREFIX}. nothing"

    router = SearchRouter()
    monkeypatch.setattr(router, "_search_local", empty)
    out = asyncio.run(router.search("q", 3, "", "manual", "local", None, None))
    assert "log:" not in out
