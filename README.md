# 🌐 tplink-archer-mcp

> **Talk to your TP-Link router in plain English.**
> A Model Context Protocol (MCP) server that exposes 117 typed tools for every read/write operation on your home router — WiFi, LAN, NAT, security, VPN, the works.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/status-production-green.svg)]()

```
You: "Show me all the devices connected to my router right now"
Claude: [calls js_get_dhcp_clients] → 14 devices, here's the list with IPs and MACs

You: "Add a port forward for 8080 going to 192.168.0.50 TCP"
Claude: [calls js_add_port_forward(...)] → ✅ Rule added

You: "What's the current PPPoE config — is my username right?"
Claude: [calls js_get_pppoe_config] → username=sharoz_dawa, dial mode=auto, MTU=1480

You: "Reserve 192.168.0.41 for the device with MAC 34-2E-B7-9F-40-75"
Claude: [calls js_add_dhcp_reservation(...)] → ✅ Static lease created
```

No web UI clicks. No memorizing where each setting lives in the menu. Just ask.

---

## 🚀 Why this exists

Every modern TP-Link router runs a Vue.js single-page web UI that locks every API call behind:
1. AES-CBC encryption (per-session key)
2. HMAC-SHA256 signed requests
3. RSA password handshake at login
4. A callback registry that refuses many endpoints from third-party clients

The popular `tplinkrouterc6u` Python library handles auth and ~10 endpoints, but the AX53's firmware refuses 80% of the rest. So how do you read your DHCP reservations or change your DNS from a script?

**The breakthrough:** the same JavaScript that powers the web UI exports a `store` object with `read/write/insert/remove/update` methods. By dynamically importing the chunk via `page.evaluate` in a headless browser, you call the same APIs the UI uses — same encryption, same auth, same callbacks. **Every endpoint, decrypted, accessible from Python.**

This MCP wraps all of that into 117 typed tools any Claude/Cursor/MCP client can call.

---

## 📡 Supported routers

### ✅ Confirmed working

| Model | Firmware tested | Notes |
|---|---|---|
| **TP-Link Archer AX53** (AX3000) | `1.11.0_2026-01-05` | Primary development target |

### 🟡 Should work (same firmware family)

The JS-direct backend works against any TP-Link router shipping the **modern Vue web UI** with the `update-store` module. That includes:

- **Archer AX series:** AX10, AX20, AX23, AX55, AX73, AX75, AX90, AX95, AXE5400, AXE16000
- **Archer A series:** A6, A7, A8, A9, A10, A20
- **Archer C series:** C6, C20, C50, C80, C1200, C3200, C5400X
- **Archer GX series:** gaming-focused models with the same UI

The legacy `tplinkrouterc6u` library backend extends compatibility to:

- **Mercusys Router** family (rebranded TP-Link hardware)
- **TP-Link Deco** mesh systems
- **TP-Link MR series** (LTE/4G)
- **TP-Link VR series** (DSL)
- **TP-Link EX series** (older AX firmware)
- **TP-Link XDR series**
- **TP-Link RE series** (range extenders)
- **TP-Link CPE/EAP/SG** (business gear)

If your model isn't listed, the universal escape hatch tools (`js_read`, `js_write`) likely still work — just point them at the right endpoint.

### ❌ Not supported

- **Tapo** routers (different firmware lineage — they use the Tapo cloud API)
- Routers running custom firmware (OpenWrt, DD-WRT, etc. — they have their own APIs)

---

## ⚡ Quickstart

### 1. Install

```bash
git clone https://github.com/sharozdawa/tplink-archer-mcp
cd tplink-archer-mcp
uv sync
uv run playwright install chromium
```

Requirements:
- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package manager)
- A TP-Link Archer router on your network
- The router admin password

### 2. Store your router password

**macOS** (recommended — uses Keychain):

```bash
security add-generic-password -s "tplink-archer-mcp" -a "192.168.0.1" -w "<YOUR_ADMIN_PASSWORD>" -U
```

**Other platforms** (env vars):

```bash
export TPLINK_ARCHER_PASSWORD="..."
export TPLINK_ARCHER_HOST="192.168.0.1"   # default
export TPLINK_ARCHER_USERNAME="admin"      # default
```

### 3. Test it works

```bash
uv run python -c "
import asyncio
from tplink_archer_mcp import tools_js as js
from tplink_archer_mcp.js_backend import close_js_backend
async def go():
    print(await js.js_get_wan_status())
    await close_js_backend()
asyncio.run(go())
"
```

You should see your WAN IP, DNS, gateway, etc. printed out.

### 4. Register with Claude Code (or any MCP client)

Edit `~/.claude.json`:

```json
{
  "mcpServers": {
    "tplink-archer": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run", "--python", "3.12",
        "--directory", "/absolute/path/to/tplink-archer-mcp",
        "tplink-archer-mcp"
      ]
    }
  }
}
```

Restart Claude Code. The 117 tools appear under the `tplink-archer` namespace.

### 5. Talk to it

```
"What devices are on my network?"
"Show me my port forwarding rules"
"What's my current WiFi 5G config?"
"Add a DHCP reservation for MAC AA-BB-CC-DD-EE-FF at 192.168.0.50"
"Disable WAN ping responses"
"Enable MU-MIMO on 5GHz"
"What's my PPPoE username?"
```

---

## 🛠️ What you can do

### Read every aspect of your router's state

| Area | Tool | Returns |
|---|---|---|
| WAN status | `js_get_wan_status` | Public IP, gateway, netmask, DNS |
| PPPoE config | `js_get_pppoe_config` | Username, MTU, conn mode, IP mode |
| LAN settings | `js_get_lan_config` | LAN IP, netmask, MAC, DHCP DNS |
| DHCP server | `js_get_dhcp_server` | Range, lease time, gateway, DNS |
| DHCP leases | `js_get_dhcp_clients` | Currently leased IPs (active devices) |
| DHCP reservations | `js_get_dhcp_reservations` | Static IP pinnings |
| Port forwards | `js_get_port_forwards` | All virtual server rules |
| Port triggers | `js_get_port_triggers` | Port triggering rules |
| DMZ | `js_get_dmz` | DMZ enable + host |
| UPnP | `js_get_upnp` | UPnP enable + active mappings |
| ALG | `js_get_alg` | FTP, H323, IPSec, L2TP, PPTP, RTSP, SIP, TFTP toggles |
| Firewall | `js_get_firewall` | Enable + LAN/WAN ping responses |
| Access Control | `js_get_access_control` | Mode + blocked devices |
| WiFi (per band) | `js_get_wifi(band)` | SSID, password, channel, encryption, MU-MIMO, etc. |
| Smart Connect | `js_get_smart_connect` | Band steering enable |
| EasyMesh | `js_get_easymesh` | Mesh enable + connected nodes |
| WireGuard server | `js_get_wireguard_server` | Built-in WireGuard config |
| IPv6 | `js_get_ipv6` | IPv6 status + addresses |
| Firmware | `js_get_firmware` | Model, hardware version, firmware version |
| Scheduled reboot | `js_get_scheduled_reboot` | Auto-reboot schedule |

### Change anything you can change in the UI

| Area | Tool | What it does |
|---|---|---|
| WiFi password | `js_set_wifi_password(band, password)` | Change PSK |
| WiFi SSID | `js_set_wifi_ssid(band, ssid)` | Rename network |
| WiFi security | `js_set_wifi_security(band, mode)` | wpa2 / wpa2_wpa3 / wpa3 / open |
| WiFi channel | `js_set_wifi_channel(band, channel, htmode)` | Specific channel + width |
| WiFi features | `js_set_wifi_features(band, mu_mimo, airtime_fairness, hidden, enable)` | Toggles |
| OFDMA / TWT | `js_set_ofdma`, `js_set_twt` | Wi-Fi 6 features |
| Smart Connect | `js_set_smart_connect(enable)` | Band steering |
| DNS (LAN) | `js_set_lan_dns(pri_dns, snd_dns)` | Override DHCP-announced DNS |
| DHCP server | `js_set_dhcp_server(enable, leasetime_min, pri_dns, snd_dns)` | Server config |
| DHCP reservation | `js_add_dhcp_reservation(mac, ip, name)` | Pin device IP |
| Port forward | `js_add_port_forward(name, ext_port, int_port, ip, protocol)` | Open port |
| DMZ | `js_set_dmz(enable, ipaddr)` | DMZ host |
| UPnP | `js_set_upnp(enable)` | UPnP toggle |
| ALG | `js_set_alg(ftp=, h323=, ...)` | Per-protocol toggles |
| Firewall | `js_set_firewall(enable, lan_ping, wan_ping)` | Firewall + ping |
| EasyMesh | `js_set_easymesh(enable)` | Mesh on/off |
| Scheduled reboot | `js_set_scheduled_reboot(enable, cycle, day, time)` | Auto-reboot |
| Reboot now | `js_reboot_now()` | Restart router |
| PPPoE | `js_set_pppoe(username, password, pri_dns, snd_dns, dns_mode, mtu)` | WAN PPPoE config |

### Universal escape hatches

For anything not yet wrapped as a typed tool:

```python
js_read("/admin/network?form=wan_ipv4_pppoe")
js_write("/admin/nat?form=dmz", {"enable": "on", "ipaddr": "192.168.0.50"})
js_insert("/admin/dhcps?form=reservation", {"mac": "...", "ip": "..."})
js_remove("/admin/nat?form=vs", {"key": "WireGuard"})
js_update("/admin/dhcps?form=reservation", {"key": "...", "ip": "..."})
```

---

## 🏗️ Architecture

Three backends, hybrid:

```
┌───────────────────────────────────────────────────────────────────┐
│  MCP CLIENT (Claude Code, Cursor, etc.)                            │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ stdio
                                   ▼
┌───────────────────────────────────────────────────────────────────┐
│  tplink-archer-mcp (FastMCP server)                                │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ Direct      │  │ Playwright   │  │ JS-direct  ★ PRIMARY       │ │
│  │ (library)   │  │ (UI driver)  │  │                             │ │
│  │ ~100ms      │  │ ~1-3s        │  │ ~500ms                      │ │
│  │ 17 tools    │  │ 50 tools     │  │ 50 tools                    │ │
│  └─────┬───────┘  └──────┬───────┘  └─────────────┬──────────────┘ │
│        │                 │                          │                │
│        ▼                 ▼                          ▼                │
│   tplinkrouterc6u   Headless Chromium ──── store.read/write/...    │
│   (auth + crypto)   (Vue UI session)                                │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ AES-CBC + HMAC-SHA256 over HTTP
                                   ▼
                       ┌──────────────────────┐
                       │  TP-Link Archer AX53 │
                       │  (your router)       │
                       └──────────────────────┘
```

### Why three backends?

1. **Direct (library)** is fastest but limited — works for ~10 endpoints the AX53 firmware accepts via the library.
2. **Playwright UI** drives form clicks; it's the legacy approach. Selectors are fragile.
3. **JS-direct** dynamically imports the router's own JS module and calls its store methods — best of both worlds: full coverage, no fragile selectors.

The hybrid lets you pick speed-vs-coverage per call. New tools default to JS-direct.

### Critical gotchas (learned the hard way)

1. **Writes must be partial diffs**, not full configs. Reading a config and writing the entire object back fails with `errorCode: server` because some fields (`macaddr`, `extinfo`, `wps_state`) are read-only/computed. Only send the fields you want to change.

2. **Field names differ from what you'd expect.** DHCP reservations use `mac` and `ip`, not `macaddr`/`ipaddr`. Always read first to learn the schema.

3. **Endpoint paths split for reboot:**
   - `/admin/reboot?form=set` — scheduled-reboot config
   - `/admin/system?form=reboot` — reboot-now (with `operation: "reboot"`)

4. **Some endpoints use `load`, not `read`** — e.g. DHCP reservations: `store.load("/admin/dhcps?form=reservation")`.

5. **The chunk hash changes per firmware update.** `JsBackend._discover_update_store_url()` re-discovers it on startup by parsing the index.html.

---

## 📚 Discovery / extending

If your firmware exposes endpoints we haven't wrapped:

```bash
# Walk the UI, capture every API call
uv run python scripts/discover_endpoints.py
# → src/tplink_archer_mcp/endpoints_catalog.json

# Deep probe — capture decrypted responses
uv run python scripts/deep_probe2.py
# → scripts/deep_probe_v2_results.json

# Capture wire bodies for reverse engineering
uv run python scripts/capture_request_bodies.py
# → scripts/captured_request_bodies.json
```

Then call `js_read("/admin/yourArea?form=yourForm")` from MCP and inspect the result. Add a typed wrapper in `tools_js.py`.

---

## 📂 Project layout

```
src/tplink_archer_mcp/
├── server.py                  # FastMCP entry point
├── auth.py                    # Library auth wrapper
├── config.py                  # Keychain + env var resolution
├── serialize.py               # JSON helpers
├── playwright_backend.py      # Headless browser session + JSON.parse hook
├── js_backend.py              # ★ Dynamic import → store.read/write
├── tools_playwright.py        # 24 read tools (legacy)
├── tools_playwright_write.py  # 26 write tools (legacy)
├── tools_js.py                # ★ 50 read+write tools (PRIMARY)
├── endpoints_catalog.json     # 40 forms from UI walk
├── endpoints_from_js.json     # 317 forms + 81 ops from JS source
└── full_endpoint_map.json     # 63 verified read/write JS endpoints

scripts/
├── discover_endpoints.py      # Playwright UI walker
├── deep_probe2.py             # JSON.parse hook deep discovery
├── capture_request_bodies.py  # XHR interception for protocol RE
├── test_writes_safe.py        # 3-tool round-trip test
├── test_all_js_tools.py       # Comprehensive js_* tools test
└── router-js/                 # 403 dumped JS files (4 MB) for analysis

SESSION_REPORT.md              # Detailed build journal
LICENSE                        # MIT
README.md                      # This file
```

---

## 🧪 Testing

The included test suite uses a round-trip pattern (read → flip → verify → restore → verify) so the router state is always returned to its starting state, even if a test fails:

```bash
uv run python scripts/test_all_js_tools.py
```

Output:
```
PHASE A: read all js_get_* tools
  ✓ js_get_wan_status                 keys=['lan_ipv4_dhcp_enable', 'lan_macaddr', ...]
  ✓ js_get_pppoe_config               keys=['static_ip', 'access', 'mtu', ...]
  ... (24 more)

PHASE B: round-trip test write tools
  ✓ js_set_alg(ftp) (flip)            from_to=True→False
  ✓ js_set_alg(ftp) (restore)         restored_to=True
  ... (more)

SUMMARY: 37 pass / 3 fail / 1 manual fix
```

---

## 💡 Real-world uses

- **Network monitoring**: "Tell me when a new device joins my network" (with a cron-driven Claude session)
- **Bandwidth allocation**: "Cap my brother's PC to 10 Mbps" (combined with `pw_set_dhcp_reservation` + QoS)
- **Security audit**: "Show me everything currently open from the WAN" (port forwards + DMZ + UPnP services)
- **Backup config**: Read every setting once a week and dump to JSON for version-controlled backups
- **Smart home automation**: "Disable IoT WiFi at 2am, re-enable at 7am" via a scheduled MCP invocation
- **Troubleshooting**: "Why can't my laptop connect?" → check DHCP leases, access control, IP-MAC binding

---

## 🤝 Contributing

Pull requests welcome. To add support for a new router model:

1. Test with the `js_*` tools first — most likely they Just Work
2. Run `scripts/discover_endpoints.py` against your router
3. Compare the new `endpoints_catalog.json` to `full_endpoint_map.json` — note differences
4. Add typed tools for your router's unique endpoints in `tools_js.py`
5. Update the **Supported routers** table above
6. Open a PR

For protocol reverse-engineering, see `SESSION_REPORT.md` for the methodology used to crack the AX53.

---

## ⚖️ License

[MIT](LICENSE) — do whatever you want with it. Attribution appreciated but not required.

---

## 🙏 Credits

- **[`tplinkrouterc6u`](https://github.com/AlexandrErohin/TP-Link-Archer-C6U)** by Alexandr Erohin — the original Python library that handles auth + crypto. The "direct" backend wraps this.
- **[`tplink-archer-ax53-guide`](https://github.com/Mahir-Isikli/tplink-archer-ax53-guide)** — comprehensive AX53 setup guide that hinted at the JS API approach.
- **[FastMCP](https://github.com/jlowin/fastmcp)** — the MCP server framework.
- **[Playwright](https://playwright.dev/)** — for the headless browser session driving the JS backend.

Built by [@sharozdawa](https://github.com/sharozdawa) — inspired by the frustration of clicking through router admin pages one too many times.

---

## 🌟 Star this repo

If this saved you time or made you smile, give it a star. And tell other folks running TP-Link routers — they probably want this too.
