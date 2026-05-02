# tplink-archer-mcp

Model Context Protocol (MCP) server for TP-Link Archer routers (tested on AX53 / AX3000). Talks to the router from Claude / Cursor / any MCP client — natural-language access to every WiFi, LAN, NAT, security, and system setting.

**67 tools across 2 backends:**

| Backend | Tools | Speed | Coverage |
|---|---|---|---|
| Direct (`tplinkrouterc6u`) | 17 | ~100 ms/call | Wireless 2g/5g/iot/guest, status, internet, DHCP leases/reservations, basic VPN |
| Playwright (drives the actual web UI) | 50 (24 read + 26 write) | ~1-3 s/call | Everything else — full UI coverage including endpoints the firmware refuses via raw API |

Tool naming:
- Plain names (e.g. `get_wifi_config`) → fast direct path
- `pw_*` prefix (e.g. `pw_get_lan_settings`) → Playwright path

## Why hybrid?

The AX53's firmware encrypts every API request and registers callbacks per-endpoint. The library handles the encryption, but only ~10-15 endpoints respond to its calls — the rest return `"no such callback"` for reasons not fully reverse-engineered. The browser session can call all of them.

The Playwright backend bypasses this by:
1. Logging in via the actual web UI (encrypted)
2. Hooking `JSON.parse` to capture every decrypted response the JS layer receives
3. Optionally driving the form UI for writes

This gives 100% functional coverage today. As more of the protocol gets reverse-engineered, individual tools can be promoted to the fast direct path with no changes to the tool name or interface.

## Install

```bash
git clone https://github.com/sharozdawa/tplink-archer-mcp
cd tplink-archer-mcp
uv sync
uv run playwright install chromium
```

## Configure

Store router admin password in macOS Keychain (recommended):

```bash
security add-generic-password -s "tplink-archer-mcp" -a "192.168.0.1" -w "<ADMIN_PASSWORD>" -U
```

Or via env var:

```bash
export TPLINK_ARCHER_PASSWORD="..."
export TPLINK_ARCHER_HOST="192.168.0.1"        # optional, default 192.168.0.1
export TPLINK_ARCHER_USERNAME="admin"           # optional, default admin
```

## Register globally in Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "tplink-archer": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--python", "3.12", "--directory",
               "/path/to/tplink-archer-mcp", "tplink-archer-mcp"]
    }
  }
}
```

Restart Claude Code; the 67 tools appear under the `tplink-archer` namespace.

## Tool catalog

### Reads (no router state changes — safe to call freely)

**Direct fast path:**
- `get_status`, `get_clients`, `get_dhcp_leases`, `get_dhcp_reservations`
- `get_internet_status`, `get_wifi_config(band)`, `get_wireguard_status`
- `list_endpoints`

**Playwright (full coverage):**
- `pw_get_internet_full`, `pw_get_internet_protos`
- `pw_get_lan_settings`, `pw_get_dhcp_server_config`, `pw_get_dhcp_reservations_full`
- `pw_get_port_forwards`, `pw_get_port_triggers`, `pw_get_dmz`, `pw_get_upnp_full`, `pw_get_alg`
- `pw_get_firewall`, `pw_get_access_control`, `pw_get_ip_mac_bindings`
- `pw_get_vpn_client_full`, `pw_get_ipv6`
- `pw_get_easymesh`, `pw_get_time_settings`, `pw_get_scheduled_reboot`
- `pw_get_network_map`, `pw_get_device_info`, `pw_get_diagnostic_state`
- `pw_get_qos_state`, `pw_get_backup_state`
- `pw_read_page_raw(route)` — escape hatch for any UI page

### Writes (each invocation makes a real change to the router)

**Direct fast path:**
- `set_wifi_password(band, password)`, `set_wifi_ssid(band, ssid)`,
  `set_wifi_security(band, mode)`, `set_wifi_channel(band, ch, htmode)`,
  `set_wifi_hidden(band, hidden)`
- `set_dns(primary, secondary)`, `revert_dns_to_isp`
- `reboot_router`
- `router_api_call(path, body)` — escape hatch for any direct API call

**Playwright write tools (UI-driven):**
- DMZ: `pw_set_dmz`
- Firewall: `pw_set_firewall`
- ALG: `pw_set_alg`
- UPnP: `pw_set_upnp`
- Port forwarding: `pw_add_port_forward`, `pw_remove_port_forward`
- DHCP: `pw_add_dhcp_reservation`, `pw_remove_dhcp_reservation`, `pw_set_dhcp_range`
- LAN: `pw_set_lan_ip`
- WAN: `pw_set_wan_dns`, `pw_revert_wan_dns_to_isp`
- WiFi: `pw_set_wifi_password`, `pw_set_wifi_ssid`, `pw_toggle_wifi`, `pw_set_wifi_security`
- IPv6: `pw_set_ipv6`
- Smart Connect: `pw_set_smart_connect`
- Time: `pw_set_ntp_server`
- Scheduled reboot: `pw_set_scheduled_reboot`
- Access control: `pw_set_access_control`
- VPN client: `pw_set_vpn_client`
- Destructive: `pw_reboot_router_now`, `pw_factory_reset`, `pw_change_admin_password`
- `pw_drive_form_raw(route, actions)` — escape hatch for any form on any page

## Architecture

```
src/tplink_archer_mcp/
├── server.py                  # FastMCP server, tool registration
├── auth.py                    # tplinkrouterc6u session wrapper
├── config.py                  # Keychain + env var resolution
├── serialize.py               # dataclass → JSON helpers
├── playwright_backend.py      # Long-lived browser, JSON.parse hook
├── tools_playwright.py        # 24 read tools, async
├── tools_playwright_write.py  # 26 write tools, async, UI form drivers
├── endpoints_catalog.json     # 40 forms from UI walk
├── endpoints_from_js.json     # 317 forms + 81 ops + 30 admin paths
└── full_endpoint_map.json     # 63 verified read/write JS-confirmed endpoints

scripts/
├── discover_endpoints.py      # Playwright UI walker (regenerate catalog)
├── deep_probe2.py             # JSON.parse-hook deep discovery
├── capture_request_bodies.py  # Captures plaintext bodies for protocol RE
└── router-js/                 # Dumped 403 JS files (4 MB) for analysis
```

## Discovery / extending

If your firmware version exposes endpoints we haven't wrapped:

```bash
uv run python scripts/discover_endpoints.py        # → endpoints_catalog.json
uv run python scripts/deep_probe2.py               # → deep_probe_v2_results.json
```

Then call `pw_read_page_raw("/yourPage")` from MCP and inspect the captured objects. Add a typed wrapper in `tools_playwright.py`.

## Known limitations

- The AX53's `1.11.0_2026-01-05` firmware refuses many endpoints when called via the library (returns `"no such callback"`). Playwright tools sidestep this. Reverse-engineering the exact protocol is a future task.
- Playwright tools are slow (1-3s per call) — keeps a Chromium process running idle ~150 MB.
- One MCP server instance = one browser session = serialized requests. Concurrent calls queue.
- Form selectors in `tools_playwright_write.py` are based on TP-Link Vue conventions and may need fine-tuning per firmware version. Use `pw_drive_form_raw` to override.

## License

MIT.
