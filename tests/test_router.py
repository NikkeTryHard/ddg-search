import asyncio
from dataclasses import dataclass

import pytest

from ddg_search.config import BACKENDS
from ddg_search.models import BackendConfig
from ddg_search.remote_mcp import RemoteRpcError, RemoteToolError, RemoteTransportError
from ddg_search.router import (
    EMPTY_RESULTS_PREFIX,
    SearchRouter,
    compact_remote_text,
    format_results_compact,
)


def make_backend(backend_id: str, ip: str = "0.0.0.0", kind: str = "remote") -> BackendConfig:
    return BackendConfig(id=backend_id, name=backend_id, label=backend_id, ip=ip, kind=kind)


@dataclass
class Row:
    title: str
    link: str
    snippet: str
    position: int


def _async_returning(value):
    async def inner(*args, **kwargs):
        return value

    return inner


def _async_raising(exc):
    async def inner(*args, **kwargs):
        raise exc

    return inner


# --- backend resolution -------------------------------------------------


def test_resolve_backend_by_id_name_alias_and_ip():
    router = SearchRouter()
    relay = next(b for b in BACKENDS if b.id == "relay-b")
    for token in ("relay-b", "oc-relay-b", "167.71.126.78"):
        assert router.resolve_backend(token) == relay


def test_resolve_backend_rejects_unknown():
    assert SearchRouter().resolve_backend("nope") is None


def test_resolve_targets_dedup_and_order():
    targets = SearchRouter().resolve_targets("relay-d", ["relay-d", "local"])
    assert [b.id for b in targets] == ["relay-d", "local"]


def test_resolve_targets_unknown_raises_value_error():
    with pytest.raises(ValueError, match="Unknown backend target"):
        SearchRouter().resolve_targets("ghost", None)


# --- ordering ------------------------------------------------------------


def test_auto_order_prefers_healthy_then_least_used():
    router = SearchRouter()
    healthy = next(b for b in BACKENDS if b.id == "local")
    cooling = next(b for b in BACKENDS if b.id == "relay-b")
    router.state.entry(cooling).cooldown_until = "9999-01-01T00:00:00+00:00"
    ordered = router.auto_order([cooling, healthy], "search")
    assert ordered[0] is healthy and ordered[1] is cooling


# --- result classification ----------------------------------------------


def test_result_failure_kinds():
    router = SearchRouter()
    empty = router._result_failure(f"{EMPTY_RESULTS_PREFIX}. details")
    soft = router._result_failure("An error occurred while searching: boom")
    unknown = router._result_failure("Unknown tool: x")
    ok = router._result_failure("via local\n\n3 results:")
    assert empty is not None and empty[0] == "empty"
    assert soft is not None and soft[0] == "remote-tool-error"
    assert unknown is not None and unknown[0] == "remote-tool-error"
    assert ok is None


def test_classify_exception_mapping():
    cases = [
        (RemoteToolError("x"), ("error", "remote-tool-error")),
        (RemoteTransportError("stage", "x"), ("error", "local-transport")),
        (RemoteRpcError({"message": "m", "code": 1}), ("error", "remote-rpc")),
        (TimeoutError(), ("timeout", "timeout")),
        (RuntimeError("connection timed out"), ("timeout", "timeout")),
        (RuntimeError("weird"), ("error", "local")),
    ]
    for exc, expected in cases:
        status, kind, _, _ = SearchRouter()._classify_exception(exc)
        assert (status, kind) == expected


# --- formatting ----------------------------------------------------------


def test_format_results_compact_rows():
    rows = [
        Row("Alpha", "https://a", "first snippet", 1),
        Row("Beta", "https://b", "", 2),
    ]
    out = format_results_compact(rows)
    assert out.splitlines() == [
        "2 results:",
        "1. Alpha",
        "https://a",
        "first snippet",
        "2. Beta",
        "https://b",
    ]


def test_format_results_compact_empty_keeps_classifier_prefix():
    assert format_results_compact([]).startswith(EMPTY_RESULTS_PREFIX)


def test_compact_remote_text_transforms_upstream_shape():
    upstream = (
        "Found 3 search results:\n\n1. Alpha\n   URL: https://a\n   Summary: one\n"
        "\n2. Beta\n   URL: https://b\n   Summary: two\n"
    )
    assert compact_remote_text(upstream).splitlines() == [
        "3 results:",
        "1. Alpha",
        "https://a",
        "one",
        "2. Beta",
        "https://b",
        "two",
    ]


def test_compact_remote_text_passthrough_non_standard_shapes():
    for text in (
        f"{EMPTY_RESULTS_PREFIX}. could be bot detection",
        "An error occurred while searching: boom",
        "some new format\nbody",
    ):
        assert compact_remote_text(text) == text


# --- failure banners -----------------------------------------------------


def test_failure_banner_variants():
    banner = SearchRouter()._failure_banner
    assert "No backends were attempted" in banner([], False)
    assert "empty DuckDuckGo results" in banner(["empty"], False)
    assert "this machine" in banner(["local"], False)
    assert "budget exhausted" in banner(["timeout"], True)
    assert "No backend returned usable" in banner(["timeout"], False)


# --- search flow with stubbed searchers ----------------------------------


def test_search_success_via_first_backend(monkeypatch):
    router = SearchRouter()
    monkeypatch.setattr(router, "_search_local", _async_returning("2 results:\n1. T\nhttps://t"))
    monkeypatch.setattr(router, "_search_remote", _async_raising(AssertionError("should not be called")))
    out = asyncio.run(router.search("q", 3, "", "manual", "local", None, None))
    assert out.startswith("via local")


def test_search_fails_over_to_next_backend(monkeypatch):
    router = SearchRouter()
    calls: list[str] = []

    async def failing(query, max_results, region, ctx):
        calls.append("local")
        raise RuntimeError("kaboom")

    async def succeeding(url, query, max_results, region):
        calls.append(url)
        return "1 result:\n1. T\nhttps://t"

    monkeypatch.setattr(router, "_search_local", failing)
    monkeypatch.setattr(router, "_search_remote", succeeding)
    out = asyncio.run(router.search("q", 3, "", "manual", "local", ["relay-b"], None))
    assert out.startswith("via relay-b")
    assert calls[0] == "local"


def test_search_manual_mode_reports_soft_failure(monkeypatch):
    router = SearchRouter()
    monkeypatch.setattr(router, "_search_local", _async_returning(f"{EMPTY_RESULTS_PREFIX}. nothing"))
    out = asyncio.run(router.search("q", 3, "", "manual", "local", None, None))
    assert out.startswith("No usable result from requested backend.")
