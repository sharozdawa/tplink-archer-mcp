"""JS-backend tools — clean read/write API via the AX53's own update-store.

These tools call `store.read()`, `store.write()`, `store.insert()`,
`store.remove()`, `store.update()` from the browser's JS context. No UI
selectors, no encryption gymnastics — just the same API the web UI itself
uses, exposed as MCP tools.

⚠ CRITICAL PROTOCOL DETAIL: writes must be PARTIAL DIFFS containing ONLY
the fields you want to change. Sending the full read result back will fail
with `errorCode: server` because some fields are read-only / computed.

Naming:
    js_get_*  — reads
    js_set_*  — writes (partial diff)
    js_add_*  — inserts (add a row to a list)
    js_del_*  — removes (delete a row from a list)
    js_request, js_read, js_write — escape hatches
"""
from __future__ import annotations

from typing import Any

from .js_backend import get_js_backend, StoreError


# ──────────────────────────────────────────────────────────────────
#  ESCAPE HATCHES — call any endpoint with any method
# ──────────────────────────────────────────────────────────────────

async def js_read(path: str) -> Any:
    """Universal READ via the AX53's store.read(). Returns decrypted JSON.

    path: e.g. "/admin/network?form=lan_ipv4", "/admin/wireguard?form=config"
    """
    return await (await get_js_backend()).read(path)


async def js_write(path: str, body: dict) -> Any:
    """Universal WRITE via the AX53's store.write(). Send PARTIAL diff only.

    path: e.g. "/admin/nat?form=dmz"
    body: dict of fields to change (NOT full config — just the diff)
    """
    return await (await get_js_backend()).write(path, body)


async def js_insert(path: str, body: dict) -> Any:
    """Insert a new row (DHCP reservation, port-forward rule, etc.)."""
    return await (await get_js_backend()).insert(path, body)


async def js_remove(path: str, body: dict) -> Any:
    """Delete a row (by key)."""
    return await (await get_js_backend()).remove(path, body)


async def js_update(path: str, body: dict) -> Any:
    """Modify an existing row (must include the key)."""
    return await (await get_js_backend()).update(path, body)


# ──────────────────────────────────────────────────────────────────
#  WAN / INTERNET
# ──────────────────────────────────────────────────────────────────

async def js_get_wan_status() -> dict:
    """WAN status: public IP, gateway, netmask, DNS (ISP-assigned or static)."""
    return await (await get_js_backend()).read("/admin/network?form=status_ipv4")


async def js_get_pppoe_config() -> dict:
    """Full PPPoE WAN config: username, MTU, DNS mode, IP mode, conn status."""
    return await (await get_js_backend()).read("/admin/network?form=wan_ipv4_pppoe")


async def js_set_pppoe(
    username: str | None = None,
    password: str | None = None,
    pri_dns: str | None = None,
    snd_dns: str | None = None,
    dns_mode: str | None = None,    # "auto" or "static"
    mtu: int | None = None,
) -> Any:
    """Update PPPoE WAN settings (partial diff). dns_mode: "auto"/"static"."""
    body: dict = {}
    if username is not None:    body["username"] = username
    if password is not None:    body["password"] = password
    if pri_dns is not None:     body["inet_pridns"] = pri_dns; body["dns_mode"] = "static"
    if snd_dns is not None:     body["inet_snddns"] = snd_dns; body["dns_mode"] = "static"
    if dns_mode is not None:    body["dns_mode"] = dns_mode
    if mtu is not None:         body["mtu"] = str(mtu)
    return await (await get_js_backend()).write("/admin/network?form=wan_ipv4_pppoe", body)


async def js_get_wan_protocols() -> Any:
    """Available WAN connection types."""
    return await (await get_js_backend()).read("/admin/network?form=wan_ipv4_protos")


# ──────────────────────────────────────────────────────────────────
#  LAN / DHCP
# ──────────────────────────────────────────────────────────────────

async def js_get_lan_config() -> dict:
    """LAN IP, netmask, MAC, DNS announced via DHCP."""
    return await (await get_js_backend()).read("/admin/network?form=lan_ipv4")


async def js_set_lan_dns(pri_dns: str | None = None, snd_dns: str | None = None) -> Any:
    """Update DNS announced to LAN clients via DHCP. Partial diff."""
    body: dict = {}
    if pri_dns is not None: body["pri_dns"] = pri_dns
    if snd_dns is not None: body["snd_dns"] = snd_dns
    return await (await get_js_backend()).write("/admin/network?form=lan_ipv4", body)


async def js_get_dhcp_server() -> dict:
    """DHCP server config: enable, range, lease time, gateway, DNS."""
    return await (await get_js_backend()).read("/admin/dhcps?form=setting")


async def js_set_dhcp_server(
    enable: bool | None = None,
    leasetime_min: int | None = None,
    pri_dns: str | None = None,
    snd_dns: str | None = None,
) -> Any:
    """Update DHCP server settings (partial diff)."""
    body: dict = {}
    if enable is not None:        body["enable"] = "on" if enable else "off"
    if leasetime_min is not None: body["leasetime"] = str(leasetime_min)
    if pri_dns is not None:       body["pri_dns"] = pri_dns
    if snd_dns is not None:       body["snd_dns"] = snd_dns
    return await (await get_js_backend()).write("/admin/dhcps?form=setting", body)


async def js_get_dhcp_reservations() -> list:
    """All DHCP static reservations (sticky IP per MAC). Uses store.load."""
    return await (await get_js_backend()).load("/admin/dhcps?form=reservation")


async def js_get_dhcp_clients() -> list:
    """Currently leased DHCP clients (active leases). Uses store.load."""
    return await (await get_js_backend()).load("/admin/dhcps?form=client")


async def js_add_dhcp_reservation(mac: str, ip: str, name: str = "") -> Any:
    """Reserve `ip` for the device with `mac`. mac format: XX-XX-XX-XX-XX-XX (uppercase, dashes).
    AX53 fields: mac, ip, comment, hostname, enable.
    """
    body = {"mac": mac, "ip": ip, "enable": "on"}
    if name:
        body["comment"] = name
        body["hostname"] = name
    return await (await get_js_backend()).insert("/admin/dhcps?form=reservation", body)


async def js_del_dhcp_reservation(mac: str) -> Any:
    """Remove a DHCP reservation by MAC."""
    return await (await get_js_backend()).remove("/admin/dhcps?form=reservation", {"key": mac, "mac": mac})


# ──────────────────────────────────────────────────────────────────
#  NAT — port forwarding, port triggering, DMZ, ALG, UPnP
# ──────────────────────────────────────────────────────────────────

async def js_get_port_forwards() -> list:
    """All port-forwarding (virtual server) rules."""
    return await (await get_js_backend()).read("/admin/nat?form=vs")


async def js_add_port_forward(
    name: str, external_port: int, internal_port: int,
    internal_ip: str, protocol: str = "TCP",
) -> Any:
    """Add a new port-forward rule. protocol: "TCP", "UDP", or "ALL"."""
    body = {
        "name": name,
        "external_port": str(external_port),
        "internal_port": str(internal_port),
        "ipaddr": internal_ip,
        "protocol": protocol,
        "enable": "on",
    }
    return await (await get_js_backend()).insert("/admin/nat?form=vs", body)


async def js_del_port_forward(name: str) -> Any:
    """Delete a port-forward rule by service name."""
    return await (await get_js_backend()).remove("/admin/nat?form=vs", {"key": name, "name": name})


async def js_get_port_triggers() -> list:
    return await (await get_js_backend()).read("/admin/nat?form=pt")


async def js_get_dmz() -> dict:
    """DMZ host config."""
    return await (await get_js_backend()).read("/admin/nat?form=dmz")


async def js_set_dmz(enable: bool, ipaddr: str = "") -> Any:
    """Enable/disable DMZ (partial diff)."""
    body = {"enable": "on" if enable else "off"}
    if enable and ipaddr: body["ipaddr"] = ipaddr
    return await (await get_js_backend()).write("/admin/nat?form=dmz", body)


async def js_get_alg() -> dict:
    """ALG protocol enables (FTP, H323, IPSec, L2TP, PPTP, RTSP, SIP, TFTP)."""
    return await (await get_js_backend()).read("/admin/nat?form=alg")


async def js_set_alg(
    ftp: bool | None = None, h323: bool | None = None, ipsec: bool | None = None,
    l2tp: bool | None = None, pptp: bool | None = None, rtsp: bool | None = None,
    sip: bool | None = None, tftp: bool | None = None,
) -> Any:
    """Toggle ALG protocols (partial diff). None = leave unchanged."""
    body: dict = {}
    for k, v in [("ftp", ftp), ("h323", h323), ("ipsec", ipsec), ("l2tp", l2tp),
                 ("pptp", pptp), ("rtsp", rtsp), ("sip", sip), ("tftp", tftp)]:
        if v is not None: body[k] = "on" if v else "off"
    return await (await get_js_backend()).write("/admin/nat?form=alg", body)


async def js_get_upnp() -> dict:
    """UPnP enable + service list."""
    js = await get_js_backend()
    enable = await js.read("/admin/upnp?form=enable")
    services = await js.read("/admin/upnp?form=service")
    return {"enable": enable, "services": services}


async def js_set_upnp(enable: bool) -> Any:
    """Enable/disable UPnP."""
    return await (await get_js_backend()).write("/admin/upnp?form=enable", {"enable": "on" if enable else "off"})


# ──────────────────────────────────────────────────────────────────
#  WIRELESS  (writes are PARTIAL — only the field you want to change)
# ──────────────────────────────────────────────────────────────────

async def js_get_wifi(band: str) -> dict:
    """Get WiFi config. band: "2g","5g","guest_2g","guest_5g","iot_2g","iot_5g"."""
    label = "wireless_" + band if band in ("2g", "5g") else band
    return await (await get_js_backend()).read(f"/admin/wireless?form={label}")


async def js_set_wifi_ssid(band: str, ssid: str) -> Any:
    """Change WiFi SSID. CAUTION: clients reconnect to new name."""
    return await (await get_js_backend()).write(f"/admin/wireless?form=wireless_{band}", {"ssid": ssid})


async def js_set_wifi_password(band: str, password: str) -> Any:
    """Change WiFi password. Min 8 chars. CAUTION: clients reconnect."""
    if len(password) < 8: raise ValueError("WiFi password must be ≥8 chars")
    return await (await get_js_backend()).write(f"/admin/wireless?form=wireless_{band}", {"psk_key": password})


async def js_set_wifi_security(band: str, mode: str) -> Any:
    """Set WiFi encryption mode: "wpa2"/"wpa2_wpa3" (recommended)/"wpa3"/"open"."""
    table = {
        "wpa2":      ("psk",     "rsn", "aes"),
        "wpa2_wpa3": ("psk_sae", "rsn", "aes"),
        "wpa3":      ("sae",     "rsn", "aes"),
        "open":      ("none",    "",    ""),
    }
    if mode not in table: raise ValueError("mode must be wpa2/wpa2_wpa3/wpa3/open")
    enc, ver, cipher = table[mode]
    return await (await get_js_backend()).write(
        f"/admin/wireless?form=wireless_{band}",
        {"encryption": enc, "psk_version": ver, "psk_cipher": cipher},
    )


async def js_set_wifi_channel(band: str, channel: str = "auto", htmode: str | None = None) -> Any:
    """Set WiFi channel + width. CAUTION: clients reconnect briefly."""
    body: dict = {"channel": str(channel)}
    if htmode is not None: body["htmode"] = str(htmode)
    return await (await get_js_backend()).write(f"/admin/wireless?form=wireless_{band}", body)


async def js_set_wifi_features(
    band: str,
    mu_mimo: bool | None = None,
    airtime_fairness: bool | None = None,
    hidden: bool | None = None,
    enable: bool | None = None,
) -> Any:
    """Toggle WiFi features (partial diff). None = leave unchanged."""
    body: dict = {}
    if mu_mimo is not None:          body["mu_mimo"] = "on" if mu_mimo else "off"
    if airtime_fairness is not None: body["airtime_fairness"] = "on" if airtime_fairness else "off"
    if hidden is not None:           body["hidden"] = "on" if hidden else "off"
    if enable is not None:           body["enable"] = "on" if enable else "off"
    return await (await get_js_backend()).write(f"/admin/wireless?form=wireless_{band}", body)


# WPS — endpoint may not be available on all firmware. Best-effort.
async def js_get_wps() -> dict:
    """WPS state. May return error on AX53 firmware 1.11.0 — UI doesn't expose WPS endpoint here."""
    js = await get_js_backend()
    # WPS state is encoded in wireless_5g.wps_state field
    cur = await js.read("/admin/wireless?form=wireless_5g")
    return {"wps_state": cur.get("wps_state", "unknown")}


async def js_set_wps(enable: bool) -> Any:
    """Enable/disable WPS by setting wps_state."""
    return await (await get_js_backend()).write(
        "/admin/wireless?form=wireless_5g",
        {"wps_state": "configured" if enable else "unconfigured"},
    )


async def js_get_smart_connect() -> dict:
    return await (await get_js_backend()).read("/admin/wireless?form=smart_connect")


async def js_set_smart_connect(enable: bool) -> Any:
    return await (await get_js_backend()).write(
        "/admin/wireless?form=smart_connect",
        {"smart_enable": "on" if enable else "off"},
    )


async def js_set_ofdma(enable: bool) -> Any:
    """Toggle OFDMA on 5G (Wi-Fi 6 multi-user efficiency)."""
    return await (await get_js_backend()).write("/admin/wireless?form=ofdma", {"enable": "on" if enable else "off"})


async def js_set_twt(enable: bool) -> Any:
    """Toggle TWT (Target Wake Time, IoT battery saver)."""
    return await (await get_js_backend()).write("/admin/wireless?form=twt", {"enable": "on" if enable else "off"})


# ──────────────────────────────────────────────────────────────────
#  SECURITY
# ──────────────────────────────────────────────────────────────────

async def js_get_firewall() -> dict:
    return await (await get_js_backend()).read("/admin/security_settings?form=new_enable")


async def js_set_firewall(
    enable: bool | None = None,
    lan_ping: bool | None = None,
    wan_ping: bool | None = None,
) -> Any:
    """Partial diff for firewall settings."""
    body: dict = {}
    if enable is not None:   body["enable"] = "on" if enable else "off"
    if lan_ping is not None: body["lan_ping"] = "on" if lan_ping else "off"
    if wan_ping is not None: body["wan_ping"] = "on" if wan_ping else "off"
    return await (await get_js_backend()).write("/admin/security_settings?form=new_enable", body)


async def js_get_access_control() -> dict:
    return await (await get_js_backend()).read("/admin/access_control?form=enable")


async def js_set_access_control(enable: bool) -> Any:
    """⚠ Use with extreme care — can block your own access."""
    return await (await get_js_backend()).write("/admin/access_control?form=enable", {"enable": "on" if enable else "off"})


# ──────────────────────────────────────────────────────────────────
#  IPv6 / EasyMesh / WireGuard / Time / Reboot
# ──────────────────────────────────────────────────────────────────

async def js_get_ipv6() -> dict:
    js = await get_js_backend()
    try:
        return await js.read("/admin/network?form=lan_ipv6")
    except StoreError:
        return await js.read("/admin/ipv6?form=lan_ipv6")


async def js_get_easymesh() -> dict:
    return await (await get_js_backend()).read("/admin/easymesh?form=easymesh_enable")


async def js_set_easymesh(enable: bool) -> Any:
    return await (await get_js_backend()).write("/admin/easymesh?form=easymesh_enable", {"enable": "on" if enable else "off"})


async def js_get_wireguard_server() -> dict:
    """Built-in WireGuard server config (private/public key, listen port)."""
    return await (await get_js_backend()).read("/admin/wireguard?form=config")


async def js_get_firmware() -> dict:
    """Current firmware: model, hardware version, firmware version."""
    return await (await get_js_backend()).read("/admin/firmware?form=upgrade")


async def js_get_scheduled_reboot() -> dict:
    """Scheduled reboot config."""
    return await (await get_js_backend()).read("/admin/reboot?form=set")


async def js_set_scheduled_reboot(
    enable: bool, cycle: str = "week", day: str = "Mon", time_hhmm: str = "03:00",
) -> Any:
    """Schedule recurring router reboot. cycle: day/week/month."""
    body: dict = {"enable": "on" if enable else "off"}
    if enable:
        body["cycle"] = cycle
        body["day"] = day
        body["time"] = time_hhmm
    return await (await get_js_backend()).write("/admin/reboot?form=set", body)


async def js_reboot_now() -> Any:
    """Reboot router NOW. Drops connections for ~60-90s."""
    return await (await get_js_backend()).request(
        "/admin/system?form=reboot",
        {"operation": "reboot"},
    )
