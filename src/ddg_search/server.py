from __future__ import annotations

import argparse
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .router import SearchRouter

mcp = FastMCP("ddg-search")
router = SearchRouter()


@mcp.tool(
    description=(
        "Search DuckDuckGo via ddg-search (local + remote backends). Discovery only — titles/URLs/snippets, "
        "not page proof. Then open promising URLs with fast-webfetch. "
        "Routing: auto = healthy low-use backends; manual = target/targets. "
        "On failure, read Attempts tags: [empty] = DDG no matches; [local]/[local-transport] = this machine "
        "(do not assume remote VPS missing packages); [remote-rpc]/[remote-tool-error] = remote answered badly; "
        "[timeout] = real wait timeout."
    )
)
async def search(
    query: Annotated[
        str,
        Field(
            description=(
                "DuckDuckGo query. Prefer exact nouns, error strings, version, site/domain, or API names. "
                "Avoid vague one-word searches."
            )
        ),
    ],
    ctx: Context,
    max_results: Annotated[
        int,
        Field(
            description="Max results. Use 5-10 normally; raise when the first page looks noisy.",
            ge=1,
            le=50,
        ),
    ] = 10,
    region: Annotated[
        str,
        Field(description="DuckDuckGo region code. Empty = default/global."),
    ] = "",
    route_mode: Annotated[
        str,
        Field(description="'auto' for normal failover; 'manual' only when targeting specific backend(s)."),
    ] = "auto",
    target: Annotated[
        str,
        Field(description="Manual mode: single backend name, alias, or IP. Empty = all backends."),
    ] = "",
    targets: Annotated[
        list[str] | None,
        Field(description="Manual mode: ordered backend names/aliases/IPs. Combined with target; duplicates ignored."),
    ] = None,
) -> str:
    return await router.search(
        query=query,
        max_results=max_results,
        region=region,
        route_mode=route_mode,
        target=target or None,
        targets=targets,
        ctx=ctx,
    )


@mcp.tool(
    description=(
        "Show ddg-search backend router status (health, cooldowns, last errors). "
        "Optional probe pings backends. Use after search failures; if last_error is local/transport, "
        "fix the workstation ddg-search process before remote packages."
    )
)
async def status(
    ctx: Context,
    probe: Annotated[bool, Field(description="If true, probe backends before reporting.")] = False,
    target: Annotated[str, Field(description="Optional single backend name/alias/IP.")] = "",
    targets: Annotated[
        list[str] | None,
        Field(description="Optional ordered backend names/aliases/IPs."),
    ] = None,
) -> str:
    return await router.status_text(probe=probe, target=target or None, targets=targets)


def main() -> None:
    parser = argparse.ArgumentParser(description="ddg-search MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
