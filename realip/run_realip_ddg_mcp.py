"""Real-IP (mullvad-exclude) exit for the ddg-search fleet.

Runs stock duckduckgo-mcp-server (streamable-http, 127.0.0.1:18082) so the
router treats it like any remote backend, but its egress bypasses Mullvad via
the mullvad-exclude fwmark and leaves on the real ISP IP (school network).
Launched by the user unit ddg-realip-mcp.service under /usr/bin/mullvad-exclude.
"""
import os
import sys

# Env must be set before importing the module-level searcher.
os.environ["DDG_SEARCH_BACKEND"] = os.environ.get("DDG_SEARCH_BACKEND", "auto")
os.environ["DDG_SAFE_SEARCH"] = os.environ.get("DDG_SAFE_SEARCH", "OFF")
# Drop mistaken old name if present
os.environ.pop("DDG_SAFESEARCH", None)

from duckduckgo_mcp_server.server import mcp

mcp.settings.host = "127.0.0.1"
mcp.settings.port = 18082

if __name__ == "__main__":
    sys.argv = [
        "duckduckgo-mcp-server",
        "--transport", "streamable-http",
        "--host", "127.0.0.1",
        "--port", "18082",
        "--search-backend", "auto",
    ]
    from duckduckgo_mcp_server.server import main
    main()
