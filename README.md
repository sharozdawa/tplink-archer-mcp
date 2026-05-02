# tplink-archer-mcp

A Model Context Protocol (MCP) server that exposes a TP-Link Archer router (AX53 / AX3000 / similar) for full automation from Claude / Cursor / any MCP-compatible client.

## What it does

Lets you read every router status surface and toggle most settings via natural-language tools, e.g.:

> "Show me all clients connected to my router right now."
> "Reserve 192.168.0.41 for the Mini PC's MAC address."
> "Disable WPS, UPnP, and WAN remote management."
> "Set the 5GHz channel to 36 with 80MHz width."
> "Reboot the router."

## Architecture

Three layers of tools, each adding more coverage:

| Layer | Tools | When to use |
|---|---|---|
| **L1** | `router_api_call(form, operation, payload)` | Universal escape hatch — calls any underlying form/op. 100% coverage. |
| **L2** | `get_status`, `get_clients`, `get_dhcp_leases`, `get_firmware`, `get_internet_status`, `get_dhcp_reservations`, `get_vpn_status`, `get_vpn_client_status` | Read every router surface. |
| **L3** | `set_wifi(band, enabled)`, `set_vpn_server`, `set_vpn_client`, `set_vpn_client_device`, `reboot_router` | Common writes as typed tools. |

For everything not yet wrapped at L2/L3, use `router_api_call`. Run `scripts/discover_endpoints.py` to generate a JSON catalog of every endpoint the web UI calls (used as a reference).

## Install

```bash
git clone https://github.com/sharozdawa/tplink-archer-mcp
cd tplink-archer-mcp
uv sync
uv run playwright install chromium  # only needed if you'll run discovery
```

## Configure

Store the router admin password in macOS Keychain (recommended):

```bash
security add-generic-password -s "tplink-archer-mcp" -a "192.168.0.1" -w "<YOUR_PASSWORD>" -U
```

Or set the env var (any platform):

```bash
export TPLINK_ARCHER_PASSWORD="your-pw"
export TPLINK_ARCHER_HOST="192.168.0.1"   # optional, default 192.168.0.1
export TPLINK_ARCHER_USERNAME="admin"     # optional, default admin
```

## Run as MCP server (Claude Code)

Register globally in `~/.claude.json`:

```json
{
  "mcpServers": {
    "tplink-archer": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/sharozdawa/development/tplink-archer-mcp", "python", "-m", "tplink_archer_mcp.server"]
    }
  }
}
```

Restart Claude Code; the tools appear under the `tplink-archer` namespace.

## Discovery walker

The router web UI is not openly documented. Run the bundled Playwright walker to catalog every API endpoint your specific firmware uses:

```bash
uv run python scripts/discover_endpoints.py
```

Output: `src/tplink_archer_mcp/endpoints_catalog.json` — keyed by form name with sample payloads, HTTP methods, operations, and pages where each was observed. Use this as a reference when calling `router_api_call`.

Re-run after any router firmware update.

## Supported routers

Auto-detection via [`tplinkrouterc6u`](https://github.com/AlexandrErohin/TP-Link-Archer-C6U). Confirmed:
- Archer AX53 (AX3000)
- Anything in the C6U / AX / XDR / VR / Deco / EX / MR families that `tplinkrouterc6u` supports.

## License

MIT.
