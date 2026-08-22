# ddg-search

A DuckDuckGo search MCP server that refuses to have a single point of failure.
One process, many backends, automatic failover, honest error messages.

## The idea

Web search is load-bearing infrastructure for coding agents, and it fails in
boring ways: rate limits, bot detection, your VPS provider having a moment.
Most servers give you one HTTP client and hope. This one routes each query
across several backends — a local searcher on this machine plus any number of
remote [duckduckgo-mcp-server](https://pypi.org/project/duckduckgo-mcp-server/)
instances you happen to run — and keeps trying until something answers or the
budget runs out.

Backends that fail get put in timeout. Backends that behave get more traffic.
You get the results, one compact block, with a one-line note of who served it.

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone <this repo> ~/.local/share/mcp/ddg-search   # or anywhere you like
cd ~/.local/share/mcp/ddg-search
uv sync
```

That is the whole ceremony. `uv sync` creates `.venv`, locks dependencies, and
installs the package editable, so edits to `src/` apply on restart.

## Wire it into your agent

Any MCP client that speaks stdio works. For an `mcp.json`-style config:

```json
{
  "mcpServers": {
    "ddg-search": {
      "type": "stdio",
      "command": "/path/to/ddg-search/.venv/bin/python",
      "args": ["-m", "ddg_search.server"],
      "env": {
        "DDG_SAFE_SEARCH": "OFF",
        "DDG_SEARCH_BACKEND": "auto"
      },
      "timeout": 60000
    }
  }
}
```

`DDG_SAFE_SEARCH` is content filtering only — it does nothing against bot
detection, and it is off by default because agents doing research want recall,
not a chaperone.

## Tools

### `search`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `query` | string | required | Exact nouns beat vague one-word vibes |
| `max_results` | int | 10 | Upstream caps around 10–11 regardless |
| `region` | string | `""` | DuckDuckGo region code |
| `route_mode` | `"auto"` \| `"manual"` | `"auto"` | Manual skips health sorting |
| `target` | string | `""` | One backend name/alias/IP (manual mode) |
| `targets` | list | `null` | Ordered fallback chain (manual mode) |

Results come back compact on purpose:

```
via relay-b

3 results:
1. Some Page Title
https://example.com/page
The snippet text, labels stripped, no blank lines eating your tokens.
2. ...
```

Every response states which backend served it. Failed attempts are listed
under `Attempts:` with a tag telling you *where* it broke:

| Tag | Meaning |
|---|---|
| `[empty]` | DuckDuckGo returned zero matches — genuine no-results or bot-empty, indistinguishable from here |
| `[local]` / `[local-transport]` | This machine's client failed. Do not blame the remote hosts |
| `[remote-tool-error]` / `[remote-rpc]` | A remote answered badly |
| `[timeout]` | The 60s budget ran out while waiting |

### `status`

Backend table: online flag, observed attempts this minute, last status,
cooldown expiry. Pass `probe: true` to actually ping remote backends instead
of trusting cached state.

## Configuration

Environment variables, all optional:

| Variable | Default | Purpose |
|---|---|---|
| `DDG_SAFE_SEARCH` | `OFF` | `STRICT` / `MODERATE` / `OFF` |
| `DDG_SEARCH_BACKEND` | `auto` | Local transport: `httpx`, `curl`, or `auto` (curl_cffi Chrome TLS fallback) |
| `DDG_SEARCH_TIMEOUT_MS` | `60000` | Total budget across all backends per query |
| `DDG_SEARCH_TIMEOUT_COOLDOWN_MS` | `90000` | Timeout penalty per backend |
| `DDG_SEARCH_ERROR_COOLDOWN_MS` | `30000` | Error penalty per backend |
| `DDG_SEARCH_STATE_DIR` | `<repo>/state` | Router state directory |

Backends live in [`src/ddg_search/config.py`](src/ddg_search/config.py). The
default fleet is `local` (this machine) plus two remote relays; edit the tuple
to match your own infrastructure.

## Behavior worth knowing

- **Failover order** prefers healthy backends with the fewest recent attempts,
  so traffic spreads instead of hammering one poor box.
- **Cooldowns are per-backend and time-boxed.** A backend that timed out sits
  out for 90s; a soft-failed one for 30s. Success clears the slate instantly.
- **State survives restarts** in `state/router-state.json`. Delete it if you
  want amnesia; the server recreates it.
- **Empty is ambiguous.** DuckDuckGo serves empty pages to clients it does not
  trust, so "no results" can mean either no matches or quiet bot-flagging.
  The router treats it as failure and tries the next backend anyway.
- **Rate limits are upstream.** Each duckduckgo-mcp-server instance enforces
  its own 30 requests/minute; the router spreads load but will not lie about
  capacity it does not have.

## Running your own relays

Any machine that can run the stock server works as a backend:

```sh
pip install 'duckduckgo-mcp-server[browser]'
python -m duckduckgo_mcp_server.main --transport streamable-http --host 0.0.0.0 --port 18082
```

Point a `BackendConfig(url="http://that-host/ddg-mcp")` at it. The
[`realip/`](realip/) directory contains a launcher used by a systemd unit to
run one such exit behind `mullvad-exclude` on residential IP — useful if your
datacenter egress gets worse captcha treatment than your home connection.

## Development

```sh
uv sync                          # install everything including dev tools
uv run pytest                    # 26 tests, no network needed except one optional live check
uv run ruff check src tests      # lint
uv run ruff format src tests     # format
uv run pyrefly check             # static types
```

A quick manual smoke test through the full router:

```sh
uv run python -c "import asyncio; from ddg_search.router import SearchRouter; \
print(asyncio.run(SearchRouter().search('crawl4ai', 3, '', 'auto', None, None, None)))"
```

## See also

- [fast-webfetch-mcp](../../fast-webfetch-mcp) — the other half: opens the URLs
  these searches find, through a local Crawl4AI browser
- [Model Context Protocol](https://modelcontextprotocol.io) — what "MCP" means
- [duckduckgo-mcp-server](https://pypi.org/project/duckduckgo-mcp-server/) —
  the search library doing the actual scraping underneath

## License

MIT.
