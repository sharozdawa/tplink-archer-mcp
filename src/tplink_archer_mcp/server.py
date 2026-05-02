"""FastMCP server for TP-Link Archer routers (tested on AX53 / AX3000).

Two backends, hybrid:

    DIRECT (fast, ~100ms): tplinkrouterc6u library handles AES+HMAC and gives
    direct read/write access. Works for ~10 endpoints today; can grow as the
    AX53 protocol gets reverse-engineered further.

    PLAYWRIGHT (slow, ~1-3s): drives a headless Chromium session of the actual
    web UI, hooks JSON.parse to capture decrypted responses. Works for ~193
    endpoints — covers everything the UI does.

Tool naming:
    Plain names (e.g. `get_wifi_config`)   → direct/fast path
    `pw_*` prefix (e.g. `pw_get_lan_settings`) → playwright path
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .auth import router_session
from .serialize import to_jsonable
from . import tools_playwright as pw
from . import tools_playwright_write as pww


logging.basicConfig(level=logging.WARNING)
mcp: FastMCP = FastMCP("tplink-archer")

CATALOG_PATH = Path(__file__).resolve().parent / "endpoints_catalog.json"


# ---------------------------------------------------------------- helpers

def _kv(**kwargs: Any) -> str:
    """Build an `operation=...&key=value&...` body. Skips None."""
    parts = ["operation={}".format(kwargs.pop("operation", "read"))]
    for k, v in kwargs.items():
        if v is None:
            continue
        parts.append("{}={}".format(k, v))
    return "&".join(parts)


def _do(path: str, body: str) -> Any:
    with router_session() as r:
        return to_jsonable(r.request(path, body))


# ---------------------------------------------------------------- L2: reads

@mcp.tool
def get_status() -> dict:
    """Overall router status: WiFi states, model, mem/cpu (where exposed)."""
    with router_session() as r:
        return to_jsonable(r.get_status())


@mcp.tool
def get_clients() -> list[dict]:
    """Every device currently connected to the router (wired + wireless), with hostname, IP, MAC, type, signal."""
    with router_session() as r:
        s = r.get_status()
        clients = (
            getattr(s, "clients", None)
            or getattr(s, "devices", None)
            or []
        )
        return [to_jsonable(c) for c in clients]


@mcp.tool
def get_dhcp_leases() -> list[dict]:
    """Currently active DHCP leases — IPs the router has handed out."""
    with router_session() as r:
        return [to_jsonable(x) for x in r.get_ipv4_dhcp_leases()]


@mcp.tool
def get_dhcp_reservations() -> list[dict]:
    """Static IP reservations (MAC → IP pinning)."""
    with router_session() as r:
        return [to_jsonable(x) for x in r.get_ipv4_reservations()]


@mcp.tool
def get_internet_status() -> dict:
    """WAN IPv4 status — public IP, gateway, netmask, DNS."""
    with router_session() as r:
        return to_jsonable(r.get_ipv4_status())


@mcp.tool
def get_wifi_config(band: str) -> dict:
    """Get full WiFi configuration for a band.

    band: "2g" or "5g"

    Returns the raw config dict (ssid, psk_key, encryption, channel, htmode,
    txpower, mu_mimo, hidden, wep_*, etc.).
    """
    band = band.lower()
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    return _do(f"admin/wireless?form=wireless_{band}", "operation=read")


@mcp.tool
def get_wireguard_status() -> dict:
    """Built-in WireGuard server config (NOT our own relay — that's separate)."""
    return _do("admin/wireguard?form=config", "operation=read")


@mcp.tool
def list_endpoints() -> dict:
    """List every API endpoint discovered by walking the AX53 web UI.

    Returned shape: {form_name: {paths, methods, operations, seen_on_pages, first_payload_sample}}.

    Useful when you need an endpoint not covered by a typed tool — pick the
    form name from here, then call router_api_call.
    """
    if not CATALOG_PATH.exists():
        return {
            "error": "endpoints_catalog.json not found. Run: uv run python scripts/discover_endpoints.py",
        }
    return json.loads(CATALOG_PATH.read_text())


# ---------------------------------------------------------------- L3: writes

@mcp.tool
def set_wifi_password(band: str, password: str) -> dict:
    """Change WiFi password for a band ("2g" or "5g"). Min 8 chars."""
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    if len(password) < 8:
        raise ValueError("WiFi password must be at least 8 characters")
    body = _kv(operation="write", psk_key=password)
    return _do(f"admin/wireless?form=wireless_{band}", body)


@mcp.tool
def set_wifi_ssid(band: str, ssid: str) -> dict:
    """Change WiFi SSID (network name) for a band ("2g" or "5g")."""
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    body = _kv(operation="write", ssid=ssid)
    return _do(f"admin/wireless?form=wireless_{band}", body)


@mcp.tool
def set_wifi_security(band: str, mode: str) -> dict:
    """Set WiFi encryption mode for a band.

    band: "2g" or "5g"
    mode: "wpa2"  (PSK only)
          "wpa2_wpa3"  (mixed PSK+SAE — recommended)
          "wpa3"  (SAE only)
          "open"  (no password — DON'T use)
    """
    map_ = {
        "wpa2":      ("psk",     "rsn", "aes"),
        "wpa2_wpa3": ("psk_sae", "rsn", "aes"),
        "wpa3":      ("sae",     "rsn", "aes"),
        "open":      ("none",    "",    ""),
    }
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    if mode not in map_:
        raise ValueError("mode must be one of: " + ", ".join(map_))
    enc, ver, cipher = map_[mode]
    body = _kv(operation="write", encryption=enc, psk_version=ver, psk_cipher=cipher)
    return _do(f"admin/wireless?form=wireless_{band}", body)


@mcp.tool
def set_wifi_channel(band: str, channel: str = "auto", htmode: str | None = None) -> dict:
    """Set WiFi channel + width.

    band:    "2g" or "5g"
    channel: "auto" or specific number ("1"-"11" for 2g; "36","40","44","48","149","153","157","161","165" for 5g)
    htmode:  channel width — "20","40","80","160","auto" (5g supports up to "160")
    """
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    body = _kv(operation="write", channel=channel, htmode=htmode)
    return _do(f"admin/wireless?form=wireless_{band}", body)


@mcp.tool
def set_wifi_hidden(band: str, hidden: bool) -> dict:
    """Hide or broadcast WiFi SSID. band: "2g" or "5g"."""
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    body = _kv(operation="write", hidden="on" if hidden else "off")
    return _do(f"admin/wireless?form=wireless_{band}", body)


@mcp.tool
def set_dns(primary: str, secondary: str | None = None) -> dict:
    """Set static WAN DNS servers (overrides ISP-assigned DNS).

    primary:   primary DNS IPv4 (e.g., "1.1.1.1" for Cloudflare)
    secondary: secondary DNS IPv4 (e.g., "1.0.0.1")

    For PPPoE users (most fiber connections in India). For DHCP/dynamic IP, the
    router uses a different form — call list_endpoints if this fails.
    """
    body = _kv(
        operation="write",
        dns_mode="static",
        static_pridns=primary,
        static_snddns=secondary or primary,
    )
    return _do("admin/network?form=wan_ipv4_pppoe", body)


@mcp.tool
def revert_dns_to_isp() -> dict:
    """Switch DNS back to the ISP's automatically-assigned servers."""
    body = _kv(operation="write", dns_mode="dynamic")
    return _do("admin/network?form=wan_ipv4_pppoe", body)


# ---------------------------------------------------------------- L4: ops

@mcp.tool
def reboot_router() -> dict:
    """Reboot the router. Returns immediately; full reboot ~60-90s."""
    with router_session() as r:
        r.reboot()
    return {"status": "rebooting", "estimated_downtime_seconds": 90}


# ---------------------------------------------------------------- L1: escape hatch

@mcp.tool
def router_api_call(path: str, body: str = "operation=read") -> Any:
    """Send any raw request to the router for things not yet wrapped as typed tools.

    path: relative API path, e.g. "admin/wireless?form=wireless_5g"
    body: form-encoded body, e.g. "operation=read" or "operation=write&key=val&key2=val2"

    Use list_endpoints first to find the right form name. The library handles
    AES encryption + HMAC signing automatically.

    Returns the decrypted response body parsed as JSON when possible.
    """
    return _do(path, body)



# ──────────────────────────────────────────────────────────────────
#  Playwright-backed tools (~25 typed reads, ~193 endpoint coverage)
# ──────────────────────────────────────────────────────────────────

mcp.tool(pw.pw_get_internet_full)
mcp.tool(pw.pw_get_internet_protos)
mcp.tool(pw.pw_get_lan_settings)
mcp.tool(pw.pw_get_dhcp_server_config)
mcp.tool(pw.pw_get_dhcp_reservations_full)
mcp.tool(pw.pw_get_port_forwards)
mcp.tool(pw.pw_get_port_triggers)
mcp.tool(pw.pw_get_dmz)
mcp.tool(pw.pw_get_upnp_full)
mcp.tool(pw.pw_get_alg)
mcp.tool(pw.pw_get_firewall)
mcp.tool(pw.pw_get_access_control)
mcp.tool(pw.pw_get_ip_mac_bindings)
mcp.tool(pw.pw_get_vpn_client_full)
mcp.tool(pw.pw_get_ipv6)
mcp.tool(pw.pw_get_easymesh)
mcp.tool(pw.pw_get_time_settings)
mcp.tool(pw.pw_get_scheduled_reboot)
mcp.tool(pw.pw_get_backup_state)
mcp.tool(pw.pw_get_diagnostic_state)
mcp.tool(pw.pw_get_qos_state)
mcp.tool(pw.pw_get_network_map)
mcp.tool(pw.pw_get_device_info)
mcp.tool(pw.pw_read_page_raw)


# ──────────────────────────────────────────────────────────────────
#  Playwright-backed WRITE tools (drive UI forms)
#  IMPORTANT: invoking any of these makes a real change to the router.
# ──────────────────────────────────────────────────────────────────

mcp.tool(pww.pw_drive_form_raw)
# DMZ
mcp.tool(pww.pw_set_dmz)
# Firewall
mcp.tool(pww.pw_set_firewall)
# ALG
mcp.tool(pww.pw_set_alg)
# UPnP
mcp.tool(pww.pw_set_upnp)
# Port forwarding
mcp.tool(pww.pw_add_port_forward)
mcp.tool(pww.pw_remove_port_forward)
# DHCP
mcp.tool(pww.pw_add_dhcp_reservation)
mcp.tool(pww.pw_remove_dhcp_reservation)
mcp.tool(pww.pw_set_dhcp_range)
# LAN
mcp.tool(pww.pw_set_lan_ip)
# WAN / DNS
mcp.tool(pww.pw_set_wan_dns)
mcp.tool(pww.pw_revert_wan_dns_to_isp)
# Wireless
mcp.tool(pww.pw_set_wifi_password)
mcp.tool(pww.pw_set_wifi_ssid)
mcp.tool(pww.pw_toggle_wifi)
mcp.tool(pww.pw_set_wifi_security)
# IPv6
mcp.tool(pww.pw_set_ipv6)
# EasyMesh / Smart Connect
mcp.tool(pww.pw_set_smart_connect)
# Time / NTP
mcp.tool(pww.pw_set_ntp_server)
# Scheduled reboot
mcp.tool(pww.pw_set_scheduled_reboot)
# Access control
mcp.tool(pww.pw_set_access_control)
# VPN client
mcp.tool(pww.pw_set_vpn_client)
# Destructive
mcp.tool(pww.pw_reboot_router_now)
mcp.tool(pww.pw_factory_reset)
mcp.tool(pww.pw_change_admin_password)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
