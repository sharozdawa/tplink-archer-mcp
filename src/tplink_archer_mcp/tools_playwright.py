"""Playwright-driven typed tools — full coverage of the AX53 web UI.

These tools navigate the actual web UI in headless Chromium and harvest the
decrypted JSON via the JSON.parse hook. They cover everything the SG-library
fast path can't reach.

Each tool follows the same pattern:
    1. Navigate to the page that loads the relevant data
    2. Filter the captured JSON.parse results for the object that matches the
       expected key signature
    3. Return a clean dict
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from .playwright_backend import get_backend


# ────────────────────────────── helpers


def _objects(captured: list[dict]) -> Iterable[dict]:
    """Yield the inner `data` dict from each captured success object."""
    for obj in captured:
        val = obj.get("value", {})
        if isinstance(val, dict):
            if val.get("success") is True and isinstance(val.get("data"), (dict, list)):
                yield val["data"]
            elif "success" not in val and val:
                # Some endpoints return data without a success wrapper
                yield val
        elif isinstance(val, list):
            yield val


def _pick_with_keys(captured: list[dict], required: set[str], min_match: int = 1) -> dict | None:
    """Find the captured data object that contains the most of `required` keys."""
    best, best_score = None, 0
    for d in _objects(captured):
        if isinstance(d, dict):
            score = sum(1 for k in required if k in d)
            if score > best_score:
                best, best_score = d, score
    return best if best_score >= min_match else None


def _pick_list_of_dicts_with_keys(captured: list[dict], required: set[str]) -> list[dict] | None:
    """Find a list of dicts where each item has at least one of `required` keys."""
    for d in _objects(captured):
        if isinstance(d, list) and d and isinstance(d[0], dict):
            if any(k in d[0] for k in required):
                return d
    return None


async def _read_page(route: str, settle_ms: int = 2500):
    pb = await get_backend()
    return await pb.read_page(route, settle_ms=settle_ms)


# ────────────────────────────── INTERNET / WAN


async def pw_get_internet_full() -> dict:
    """Full internet/WAN status: WAN IP, gateway, netmask, DNS, conn type/status, dial mode, etc.
    Includes both the high-level status and the full PPPoE/dynamic config detail.
    """
    res = await _read_page("/internetBasic")
    out = {"_route": "/internetBasic", "_forms": res.forms_seen}
    status = _pick_with_keys(res.objects, {
        "lan_ipv4_dhcp_enable", "wan_ipv4_ipaddr", "wan_ipv4_gateway",
        "wan_ipv4_pridns", "wan_ipv4_snddns",
    })
    if status:
        out["status"] = status
    detail = _pick_with_keys(res.objects, {
        "conntype", "conn_mode", "conn_status", "dns_mode",
    })
    if detail and detail is not status:
        out["detail"] = detail
    return out


async def pw_get_internet_protos() -> dict:
    """Available WAN protocols this firmware supports (PPPoE, DHCP, Static, L2TP, PPTP, BigPond, OCN, V6Plus, etc.)."""
    res = await _read_page("/internetBasic")
    protos = _pick_list_of_dicts_with_keys(res.objects, {"name", "value"})
    return {"_route": "/internetBasic", "protocols": protos or []}


# ────────────────────────────── LAN / DHCP


async def pw_get_lan_settings() -> dict:
    """LAN settings: LAN IP, netmask, MAC, DHCP server enable."""
    res = await _read_page("/lan")
    return {
        "_route": "/lan",
        "_forms": res.forms_seen,
        "lan": _pick_with_keys(res.objects, {
            "lan_ipv4_ipaddr", "lan_ipv4_netmask", "lan_macaddr", "lan_ipv4_dhcp_enable",
        }) or {},
    }


async def pw_get_dhcp_server_config() -> dict:
    """Full DHCP server config: range start/end, lease time, gateway, primary/secondary DNS, domain."""
    res = await _read_page("/dhcpServer")
    return {
        "_route": "/dhcpServer",
        "_forms": res.forms_seen,
        "dhcp": _pick_with_keys(res.objects, {
            "ipaddr_start", "ipaddr_end", "leasetime", "gateway", "pri_dns", "snd_dns",
        }) or {},
    }


async def pw_get_dhcp_reservations_full() -> dict:
    """All DHCP reservations (static IP pinnings)."""
    res = await _read_page("/dhcpServer")
    # Reservations come as a list of dicts
    rsv = _pick_list_of_dicts_with_keys(res.objects, {"macaddr", "ipaddr"})
    return {"_route": "/dhcpServer", "reservations": rsv or []}


# ────────────────────────────── NAT (port forwarding, triggering, DMZ)


async def pw_get_port_forwards() -> dict:
    """All virtual server / port forwarding rules."""
    res = await _read_page("/portForwarding")
    rules = _pick_list_of_dicts_with_keys(res.objects, {
        "external_port", "internal_port", "protocol", "ipaddr",
    })
    return {"_route": "/portForwarding", "rules": rules or []}


async def pw_get_port_triggers() -> dict:
    """All port triggering rules."""
    res = await _read_page("/portTriggering")
    rules = _pick_list_of_dicts_with_keys(res.objects, {
        "trigger_port", "incoming_port",
    })
    return {"_route": "/portTriggering", "rules": rules or []}


async def pw_get_dmz() -> dict:
    """DMZ host config: enable + IP address."""
    res = await _read_page("/dmz")
    return {
        "_route": "/dmz",
        "_forms": res.forms_seen,
        "dmz": _pick_with_keys(res.objects, {"enable", "ipaddr"}) or {},
    }


async def pw_get_upnp_full() -> dict:
    """UPnP enable + currently mapped service list."""
    res = await _read_page("/upnp")
    enable = _pick_with_keys(res.objects, {"enable"}, min_match=1)
    services = _pick_list_of_dicts_with_keys(res.objects, {
        "external_port", "internal_port", "protocol",
    })
    return {
        "_route": "/upnp",
        "_forms": res.forms_seen,
        "enable": enable.get("enable") if enable else None,
        "services": services or [],
    }


async def pw_get_alg() -> dict:
    """ALG (Application Layer Gateway) protocol enables: FTP, H323, IPSec, L2TP, PPTP, RTSP, SIP, TFTP."""
    res = await _read_page("/alg")
    return {
        "_route": "/alg",
        "alg": _pick_with_keys(res.objects, {"ftp", "h323", "ipsec", "l2tp", "pptp", "rtsp", "sip", "tftp"}) or {},
    }


# ────────────────────────────── SECURITY


async def pw_get_firewall() -> dict:
    """Firewall enable + LAN/WAN ping response settings."""
    res = await _read_page("/firewall")
    return {
        "_route": "/firewall",
        "firewall": _pick_with_keys(res.objects, {"enable", "lan_ping", "wan_ping"}) or {},
    }


async def pw_get_access_control() -> dict:
    """Access control mode + host MAC list."""
    res = await _read_page("/accessControl")
    return {
        "_route": "/accessControl",
        "_forms": res.forms_seen,
        "config": _pick_with_keys(res.objects, {"enable", "host_mac"}) or {},
    }


async def pw_get_ip_mac_bindings() -> dict:
    """IP-MAC binding rules (force devices to specific IPs/MACs)."""
    res = await _read_page("/ipMacBinding")
    bindings = _pick_list_of_dicts_with_keys(res.objects, {"ipaddr", "macaddr"})
    return {"_route": "/ipMacBinding", "bindings": bindings or []}


# ────────────────────────────── VPN


async def pw_get_vpn_client_full() -> dict:
    """Built-in VPN client config + supported third-party VPN vendors (Surfshark, NordVPN, etc.)."""
    res = await _read_page("/vpnClient")
    config = _pick_with_keys(res.objects, {"enable", "type"})
    vendors = _pick_with_keys(res.objects, {"vendor", "support_third_vpn"})
    return {
        "_route": "/vpnClient",
        "config": config or {},
        "vendor_support": vendors or {},
    }


# ────────────────────────────── IPv6


async def pw_get_ipv6() -> dict:
    """IPv6 status: enable, conntype, address, assignment type, prefix info."""
    res = await _read_page("/ipv6")
    main = _pick_with_keys(res.objects, {"enable", "conntype"})
    detail = _pick_with_keys(res.objects, {
        "address", "assign_type", "dhcp_prefix", "ndp_prefix", "rdnss_prefix",
    })
    return {
        "_route": "/ipv6",
        "main": main or {},
        "detail": detail or {},
    }


# ────────────────────────────── EasyMesh


async def pw_get_easymesh() -> dict:
    """EasyMesh enable + smart connect + 5G channel/htmode."""
    res = await _read_page("/easyMesh")
    smart = _pick_with_keys(res.objects, {"smart_enable"})
    wireless5g = _pick_with_keys(res.objects, {"channel", "htmode", "airtime_fairness"})
    return {
        "_route": "/easyMesh",
        "_forms": res.forms_seen,
        "smart_connect": smart.get("smart_enable") if smart else None,
        "wireless_5g": wireless5g or {},
    }


# ────────────────────────────── TIME / SCHEDULE


async def pw_get_time_settings() -> dict:
    """Time, NTP servers, DST, timezone region info."""
    res = await _read_page("/timeSettings")
    time_cfg = _pick_with_keys(res.objects, {"date", "ntp_svr1", "ntp_svr2"})
    dst = _pick_with_keys(res.objects, {"dst_enable", "dst_status", "start_day", "end_day"})
    region = _pick_with_keys(res.objects, {"country", "region_list"})
    return {
        "_route": "/timeSettings",
        "time": time_cfg or {},
        "dst": dst or {},
        "region": region or {},
    }


async def pw_get_scheduled_reboot() -> dict:
    """Scheduled reboot config: enable, cycle (daily/weekly), day, time."""
    res = await _read_page("/reboot")
    return {
        "_route": "/reboot",
        "scheduled_reboot": _pick_with_keys(res.objects, {"enable", "cycle", "day", "time"}) or {},
    }


# ────────────────────────────── SYSTEM / BACKUP / FIRMWARE


async def pw_get_backup_state() -> dict:
    """Backup/restore state (totaltime field tracks remote backup)."""
    res = await _read_page("/backupRestore")
    return {"_route": "/backupRestore", "captured_objects": len(res.objects)}


async def pw_get_diagnostic_state() -> dict:
    """Diagnostic page state (ping/traceroute config, etc.)."""
    res = await _read_page("/diagnostic")
    return {"_route": "/diagnostic", "_forms": res.forms_seen, "captured_objects": len(res.objects)}


async def pw_get_qos_state() -> dict:
    """QoS/HomeShield bandwidth allocation state."""
    res = await _read_page("/qos")
    return {"_route": "/qos", "_forms": res.forms_seen, "captured_objects": len(res.objects)}


# ────────────────────────────── NETWORK MAP / CLIENTS


async def pw_get_network_map() -> dict:
    """Full network map — internet status, modem status, mesh devices, clients summary."""
    res = await _read_page("/networkMap")
    internet = _pick_with_keys(res.objects, {"wan_internet_status", "internet_status"})
    return {
        "_route": "/networkMap",
        "_forms": res.forms_seen,
        "internet_status": internet or {},
        "captured_count": len(res.objects),
    }


# ────────────────────────────── DEVICE INFO


async def pw_get_device_info() -> dict:
    """Device capability descriptor — what this firmware supports (Wi-Fi 7, MU-MIMO, OFDMA, etc.)."""
    res = await _read_page("/networkMap")
    caps = _pick_with_keys(res.objects, {
        "supportVpnServerType", "supportWifi7", "supportUdpProxy",
        "supportSpeedTest", "mainNetwork",
    })
    return {"_route": "/networkMap", "device_capabilities": caps or {}}


# ────────────────────────────── ESCAPE HATCH


async def pw_read_page_raw(route: str) -> dict:
    """Navigate to any UI route and return ALL captured decrypted JSON objects.

    Use this when no typed tool fits. Pass routes like:
        "/wireless2g", "/portForwarding", "/parentalControlsV4", "/qos", etc.

    Returns the full list of decrypted objects + the API forms that were called.
    """
    res = await _read_page(route)
    return {
        "route": res.route,
        "forms": res.forms_seen,
        "request_paths": res.request_paths,
        "objects_count": len(res.objects),
        "objects": res.objects,
    }
