"""Playwright-driven WRITE tools for the AX53 web UI.

Each tool drives the actual UI in headless Chromium: navigate to the page,
fill the right inputs, click Save, capture the success/failure response.

Selectors are based on the TP-Link Vue component conventions (su-input,
su-checkbox, su-button, su-form-item with `name=` attributes). Some may
need fine-tuning per firmware version — the reusable
`pw_drive_form_raw` escape hatch lets the user run an arbitrary action list.

NONE of these tools execute writes against the router during build/test.
The user must explicitly invoke each one through the MCP and approve the
specific change. (See feedback_ask_before_changes.md.)
"""
from __future__ import annotations

from typing import Any

from .playwright_backend import get_backend


# ────────────────────────────── escape hatch


async def pw_drive_form_raw(route: str, actions: list[dict]) -> dict:
    """Universal write tool — drive ANY form on ANY page.

    route: hash route, e.g. "/dmz", "/lan", "/portForwarding"
    actions: ordered list of UI operations:
        {"type": "fill",   "selector": "input[name=ipaddr]",  "value": "192.168.0.50"}
        {"type": "check",  "selector": "input[type=checkbox]","checked": True}
        {"type": "click",  "selector": "button:has-text(\"Add\")"}
        {"type": "select", "selector": "select[name=protocol]","option": "TCP"}
        {"type": "wait",   "ms": 500}

    After the actions, automatically clicks "Save" and captures the response.
    Use when no typed tool covers your operation.
    """
    pb = await get_backend()
    return await pb.drive_form(route, actions)


# ────────────────────────────── DMZ


async def pw_set_dmz(enabled: bool, ipaddr: str = "") -> dict:
    """Enable/disable DMZ host. When enabled, all unsolicited inbound traffic
    forwards to `ipaddr`. Set ipaddr="" when disabling.
    """
    actions: list[dict] = [
        {"type": "check", "selector": 'input[type="checkbox"]', "checked": enabled},
    ]
    if enabled and ipaddr:
        actions.append({"type": "fill", "selector": 'input[type="text"]:visible', "value": ipaddr})
    pb = await get_backend()
    return await pb.drive_form("/dmz", actions)


# ────────────────────────────── FIREWALL


async def pw_set_firewall(
    enabled: bool | None = None,
    lan_ping: bool | None = None,
    wan_ping: bool | None = None,
) -> dict:
    """Toggle firewall + LAN/WAN ping responses. Pass None to leave a setting unchanged.

    wan_ping=False is recommended for security.
    """
    actions: list[dict] = []
    # Selector hints — actual page may show 1-3 toggles
    if enabled is not None:
        actions.append({"type": "check", "selector": 'input[type="checkbox"]:nth-of-type(1)', "checked": enabled})
    if lan_ping is not None:
        actions.append({"type": "check", "selector": 'input[type="checkbox"]:nth-of-type(2)', "checked": lan_ping})
    if wan_ping is not None:
        actions.append({"type": "check", "selector": 'input[type="checkbox"]:nth-of-type(3)', "checked": wan_ping})
    pb = await get_backend()
    return await pb.drive_form("/firewall", actions)


# ────────────────────────────── ALG


async def pw_set_alg(
    ftp: bool | None = None, h323: bool | None = None, ipsec: bool | None = None,
    l2tp: bool | None = None, pptp: bool | None = None, rtsp: bool | None = None,
    sip: bool | None = None, tftp: bool | None = None,
) -> dict:
    """Toggle ALG (Application Layer Gateway) protocols. Pass None to leave unchanged."""
    pb = await get_backend()
    actions: list[dict] = []
    # Each ALG is a checkbox under a labelled section
    for label, val in [("FTP", ftp), ("H323", h323), ("IPSec", ipsec),
                        ("L2TP", l2tp), ("PPTP", pptp), ("RTSP", rtsp),
                        ("SIP", sip), ("TFTP", tftp)]:
        if val is None: continue
        actions.append({
            "type": "check",
            "selector": f'su-form-item:has-text("{label}") input[type="checkbox"]',
            "checked": val,
        })
    return await pb.drive_form("/alg", actions)


# ────────────────────────────── UPnP


async def pw_set_upnp(enabled: bool) -> dict:
    """Enable/disable UPnP. Disabling is more secure; enabling lets apps auto-create port forwards."""
    pb = await get_backend()
    return await pb.drive_form("/upnp", [
        {"type": "check", "selector": 'input[type="checkbox"]', "checked": enabled},
    ])


# ────────────────────────────── PORT FORWARDING


async def pw_add_port_forward(
    name: str,
    external_port: int,
    internal_port: int,
    internal_ip: str,
    protocol: str = "TCP",
) -> dict:
    """Add a new port-forward rule (Virtual Server entry).

    protocol: "TCP", "UDP", or "ALL"
    """
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "click",  "selector": 'button:has-text("Add")'},
        {"type": "wait",   "ms": 500},
        {"type": "fill",   "selector": 'input[name="name"], input[placeholder*="Service"]', "value": name},
        {"type": "fill",   "selector": 'input[name="externalPort"], input[placeholder*="External"]', "value": str(external_port)},
        {"type": "fill",   "selector": 'input[name="internalPort"], input[placeholder*="Internal"]', "value": str(internal_port)},
        {"type": "fill",   "selector": 'input[name="ipaddr"], input[placeholder*="Device IP"]', "value": internal_ip},
        {"type": "select", "selector": 'select[name="protocol"]', "option": protocol},
    ]
    return await pb.drive_form("/portForwarding", actions)


async def pw_remove_port_forward(name: str) -> dict:
    """Remove an existing port-forward rule by its Service Name."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "click", "selector": f'tr:has-text("{name}") button:has-text("Delete")'},
        {"type": "wait",  "ms": 500},
    ]
    return await pb.drive_form("/portForwarding", actions, save_selectors=(
        'button:has-text("Yes")',
        'button:has-text("Confirm")',
    ))


# ────────────────────────────── DHCP


async def pw_add_dhcp_reservation(macaddr: str, ipaddr: str, description: str = "") -> dict:
    """Reserve `ipaddr` for the device with `macaddr` (sticky DHCP)."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "click", "selector": 'button:has-text("Add")'},
        {"type": "wait",  "ms": 500},
        {"type": "fill",  "selector": 'input[name="macaddr"], input[placeholder*="MAC"]', "value": macaddr},
        {"type": "fill",  "selector": 'input[name="ipaddr"], input[placeholder*="IP"]', "value": ipaddr},
    ]
    if description:
        actions.append({"type": "fill", "selector": 'input[name="description"], input[placeholder*="Description"]', "value": description})
    return await pb.drive_form("/dhcpServer", actions)


async def pw_remove_dhcp_reservation(macaddr: str) -> dict:
    """Remove a DHCP reservation by MAC address."""
    pb = await get_backend()
    return await pb.drive_form("/dhcpServer", [
        {"type": "click", "selector": f'tr:has-text("{macaddr}") button:has-text("Delete")'},
        {"type": "wait",  "ms": 500},
    ], save_selectors=(
        'button:has-text("Yes")',
        'button:has-text("Confirm")',
    ))


async def pw_set_dhcp_range(
    ip_start: str | None = None,
    ip_end: str | None = None,
    leasetime_min: int | None = None,
    pri_dns: str | None = None,
    snd_dns: str | None = None,
) -> dict:
    """Configure the DHCP server's IP range, lease time, and announced DNS."""
    pb = await get_backend()
    actions: list[dict] = []
    if ip_start: actions.append({"type": "fill", "selector": 'input[name="ipaddr_start"]', "value": ip_start})
    if ip_end:   actions.append({"type": "fill", "selector": 'input[name="ipaddr_end"]',   "value": ip_end})
    if leasetime_min is not None:
        actions.append({"type": "fill", "selector": 'input[name="leasetime"]', "value": str(leasetime_min)})
    if pri_dns:  actions.append({"type": "fill", "selector": 'input[name="pri_dns"]', "value": pri_dns})
    if snd_dns:  actions.append({"type": "fill", "selector": 'input[name="snd_dns"]', "value": snd_dns})
    return await pb.drive_form("/dhcpServer", actions)


# ────────────────────────────── LAN


async def pw_set_lan_ip(ip: str, netmask: str = "255.255.255.0") -> dict:
    """Change the router's LAN IP address. WARNING: changes the gateway IP for ALL home devices.
    All current connections drop. You'll need to reconnect to the router at the new IP."""
    pb = await get_backend()
    return await pb.drive_form("/lan", [
        {"type": "fill", "selector": 'input[name="ipaddr"]',  "value": ip},
        {"type": "fill", "selector": 'input[name="netmask"]', "value": netmask},
    ])


# ────────────────────────────── WAN / DNS


async def pw_set_wan_dns(primary: str, secondary: str = "") -> dict:
    """Set static WAN DNS servers (overrides ISP-assigned). Use 1.1.1.1 / 1.0.0.1 for Cloudflare."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "select", "selector": 'select[name="dns_mode"]', "option": "static"},
        {"type": "fill",   "selector": 'input[name="static_pridns"]', "value": primary},
    ]
    if secondary:
        actions.append({"type": "fill", "selector": 'input[name="static_snddns"]', "value": secondary})
    return await pb.drive_form("/internetBasic", actions)


async def pw_revert_wan_dns_to_isp() -> dict:
    """Revert WAN DNS to ISP-assigned (dynamic mode)."""
    pb = await get_backend()
    return await pb.drive_form("/internetBasic", [
        {"type": "select", "selector": 'select[name="dns_mode"]', "option": "dynamic"},
    ])


# ────────────────────────────── WIRELESS


async def pw_set_wifi_password(band: str, new_password: str) -> dict:
    """Change WiFi password for "2g" or "5g". Min 8 chars."""
    if band not in {"2g", "5g"}:
        raise ValueError("band must be '2g' or '5g'")
    if len(new_password) < 8:
        raise ValueError("WiFi password must be at least 8 characters")
    pb = await get_backend()
    route = "/wireless2g" if band == "2g" else "/wireless5g"
    return await pb.drive_form(route, [
        {"type": "fill", "selector": 'input[name="psk_key"], input[type="password"]:visible', "value": new_password},
    ])


async def pw_set_wifi_ssid(band: str, ssid: str) -> dict:
    """Change WiFi SSID (network name) for "2g" or "5g"."""
    if band not in {"2g", "5g"}: raise ValueError("band must be '2g' or '5g'")
    pb = await get_backend()
    route = "/wireless2g" if band == "2g" else "/wireless5g"
    return await pb.drive_form(route, [
        {"type": "fill", "selector": 'input[name="ssid"]', "value": ssid},
    ])


async def pw_toggle_wifi(band: str, enabled: bool) -> dict:
    """Enable/disable a WiFi band. band: "2g" or "5g"."""
    if band not in {"2g", "5g"}: raise ValueError("band must be '2g' or '5g'")
    pb = await get_backend()
    route = "/wireless2g" if band == "2g" else "/wireless5g"
    return await pb.drive_form(route, [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ])


async def pw_set_wifi_security(band: str, mode: str) -> dict:
    """Set WiFi encryption mode.
    band: "2g" or "5g"
    mode: "wpa2" / "wpa2_wpa3" (recommended) / "wpa3" / "open" (don't use)
    """
    map_ = {"wpa2": "WPA2-PSK", "wpa2_wpa3": "WPA2/WPA3", "wpa3": "WPA3", "open": "None"}
    if mode not in map_: raise ValueError("mode must be one of: " + ", ".join(map_))
    pb = await get_backend()
    route = "/wireless2g" if band == "2g" else "/wireless5g"
    return await pb.drive_form(route, [
        {"type": "select", "selector": 'select[name="encryption"], select.encryption-select', "option": map_[mode]},
    ])


# ────────────────────────────── IPv6


async def pw_set_ipv6(enabled: bool) -> dict:
    """Enable/disable IPv6."""
    pb = await get_backend()
    return await pb.drive_form("/ipv6", [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ])


# ────────────────────────────── EASYMESH / SMART CONNECT


async def pw_set_smart_connect(enabled: bool) -> dict:
    """Enable/disable Smart Connect (auto band-steering between 2.4G/5G)."""
    pb = await get_backend()
    return await pb.drive_form("/easyMesh", [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ])


# ────────────────────────────── TIME / NTP


async def pw_set_ntp_server(server1: str, server2: str = "") -> dict:
    """Configure NTP servers for time sync."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "fill", "selector": 'input[name="ntp_svr1"]', "value": server1},
    ]
    if server2:
        actions.append({"type": "fill", "selector": 'input[name="ntp_svr2"]', "value": server2})
    return await pb.drive_form("/timeSettings", actions)


# ────────────────────────────── SCHEDULED REBOOT


async def pw_set_scheduled_reboot(
    enabled: bool,
    cycle: str = "week",
    day: str = "Mon",
    time_hhmm: str = "03:00",
) -> dict:
    """Schedule a recurring router reboot. cycle: "day"/"week"/"month".
    day: weekday "Mon"/"Tue"/.../"Sun" (when cycle=week) or day-of-month (when cycle=month).
    time_hhmm: 24h format like "03:00".
    """
    actions: list[dict] = [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ]
    if enabled:
        actions += [
            {"type": "select", "selector": 'select[name="cycle"]', "option": cycle},
            {"type": "fill",   "selector": 'input[name="time"], input[type="time"]', "value": time_hhmm},
        ]
    pb = await get_backend()
    return await pb.drive_form("/reboot", actions)


# ────────────────────────────── ACCESS CONTROL


async def pw_set_access_control(enabled: bool, mode: str = "blacklist") -> dict:
    """Enable/disable Access Control. mode: "blacklist" (block listed) or "whitelist" (allow only listed)."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ]
    if enabled:
        actions.append({"type": "select", "selector": 'select[name="mode"]', "option": mode})
    return await pb.drive_form("/accessControl", actions)


# ────────────────────────────── VPN CLIENT


async def pw_set_vpn_client(enabled: bool, vpn_type: str = "openvpn") -> dict:
    """Enable/disable the router-as-VPN-client feature. vpn_type: "openvpn" / "pptp" / "l2tp"."""
    pb = await get_backend()
    actions: list[dict] = [
        {"type": "check", "selector": 'input[type="checkbox"]:visible', "checked": enabled},
    ]
    if enabled:
        actions.append({"type": "select", "selector": 'select[name="type"]', "option": vpn_type})
    return await pb.drive_form("/vpnClient", actions)


# ────────────────────────────── DESTRUCTIVE OPS (require explicit consent)


async def pw_reboot_router_now() -> dict:
    """Reboot the router immediately. Takes ~60-90s. ALL connections drop."""
    pb = await get_backend()
    return await pb.drive_form("/reboot", [
        {"type": "click", "selector": 'button:has-text("Reboot")'},
        {"type": "wait",  "ms": 500},
    ], save_selectors=('button:has-text("Yes")', 'button:has-text("Reboot")'))


async def pw_factory_reset() -> dict:
    """!!! WIPES ALL ROUTER SETTINGS !!! Returns to factory defaults.
    All WiFi credentials, port forwards, reservations, admin password — gone.
    Recommend reading current settings + saving a backup first.
    """
    pb = await get_backend()
    return await pb.drive_form("/factoryDefaults", [
        {"type": "click", "selector": 'button:has-text("Factory")'},
        {"type": "wait",  "ms": 500},
    ], save_selectors=('button:has-text("Yes")', 'button:has-text("Restore")'))


async def pw_change_admin_password(current_password: str, new_password: str) -> dict:
    """Change the router admin password (the one you use at http://192.168.0.1).
    Min 6 chars. After change, update Keychain:
        security add-generic-password -s "tplink-archer-mcp" -a "192.168.0.1" -w "<NEW>" -U
    """
    if len(new_password) < 6:
        raise ValueError("admin password must be at least 6 characters")
    pb = await get_backend()
    return await pb.drive_form("/loginPassword", [
        {"type": "fill", "selector": 'input[name="currentPassword"], input[name="oldPassword"]', "value": current_password},
        {"type": "fill", "selector": 'input[name="newPassword"]', "value": new_password},
        {"type": "fill", "selector": 'input[name="confirmPassword"]', "value": new_password},
    ])
