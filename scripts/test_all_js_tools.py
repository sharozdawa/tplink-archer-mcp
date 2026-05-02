"""End-to-end test of every js_* tool.

Phase A: Read all js_get_* tools (no router state changes)
Phase B: Round-trip-test each write tool (read → write opposite → verify
         → restore → verify). Skip destructive writes (admin pw, factory
         reset, lan ip, wifi creds, dhcp range that would invalidate
         existing leases).

Output: scripts/test_results.json with per-tool pass/fail + actual data
captured. Also print summary.

Exit nonzero if anything failed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp import tools_js as js
from tplink_archer_mcp.js_backend import close_js_backend, StoreError


REPORT_PATH = Path(__file__).resolve().parent / "test_results.json"
results: dict[str, dict] = {}


def log(name: str, status: str, **fields: Any) -> None:
    results[name] = {"status": status, **fields}
    extras = " | ".join(f"{k}={v!s:.50}" for k, v in fields.items() if k != "data")
    print(f"  {status} {name:42} {extras}")


# ──────────────────────────────  Phase A: reads


READS = [
    ("js_get_wan_status",         js.js_get_wan_status,           []),
    ("js_get_pppoe_config",       js.js_get_pppoe_config,         []),
    ("js_get_wan_protocols",      js.js_get_wan_protocols,        []),
    ("js_get_lan_config",         js.js_get_lan_config,           []),
    ("js_get_dhcp_server",        js.js_get_dhcp_server,          []),
    ("js_get_dhcp_reservations",  js.js_get_dhcp_reservations,    []),
    ("js_get_port_forwards",      js.js_get_port_forwards,        []),
    ("js_get_port_triggers",      js.js_get_port_triggers,        []),
    ("js_get_dmz",                js.js_get_dmz,                  []),
    ("js_get_alg",                js.js_get_alg,                  []),
    ("js_get_upnp",               js.js_get_upnp,                 []),
    ("js_get_wifi_2g",            js.js_get_wifi,                 ["2g"]),
    ("js_get_wifi_5g",            js.js_get_wifi,                 ["5g"]),
    ("js_get_wifi_guest_2g",      js.js_get_wifi,                 ["guest_2g"]),
    ("js_get_wifi_guest_5g",      js.js_get_wifi,                 ["guest_5g"]),
    ("js_get_wifi_iot_2g",        js.js_get_wifi,                 ["iot_2g"]),
    ("js_get_wifi_iot_5g",        js.js_get_wifi,                 ["iot_5g"]),
    ("js_get_wps",                js.js_get_wps,                  []),
    ("js_get_smart_connect",      js.js_get_smart_connect,        []),
    ("js_get_firewall",           js.js_get_firewall,             []),
    ("js_get_access_control",     js.js_get_access_control,       []),
    ("js_get_ipv6",               js.js_get_ipv6,                 []),
    ("js_get_easymesh",           js.js_get_easymesh,             []),
    ("js_get_wireguard_server",   js.js_get_wireguard_server,     []),
    ("js_get_firmware",           js.js_get_firmware,             []),
    ("js_get_scheduled_reboot",   js.js_get_scheduled_reboot,     []),
]


async def run_reads() -> None:
    print("\n" + "═" * 75)
    print("PHASE A: read all js_get_* tools (no router changes)")
    print("═" * 75)
    for name, fn, args in READS:
        try:
            data = await fn(*args)
            if isinstance(data, dict):
                summary = f"keys={list(data.keys())[:5]}"
            elif isinstance(data, list):
                summary = f"list[{len(data)}]"
            else:
                summary = f"{type(data).__name__}"
            log(name, "✓", summary=summary, data=data if not isinstance(data, dict) else {k:v for k,v in list(data.items())[:8]})
        except StoreError as e:
            log(name, "✗", err=str(e.error)[:100])
        except Exception as e:
            log(name, "✗", err=f"{type(e).__name__}: {e!s:.80}")


# ──────────────────────────────  Phase B: round-trip writes


async def round_trip_test(
    name: str,
    read_fn,
    write_fn,
    extract_current,        # extract current value from read result (e.g., d => d["enable"] == "on")
    set_to_opposite,        # call write_fn with opposite value
    set_to_original,        # call write_fn with original value
    *,
    pre_args=(),
    skip_reason: str | None = None,
):
    """Read current → flip → verify → restore → verify."""
    print(f"\n--- {name} ---")
    if skip_reason:
        log(name, "⊝ SKIP", reason=skip_reason)
        return

    original_state = None
    try:
        before = await read_fn(*pre_args)
        original_state = extract_current(before)
        print(f"  read.before: {original_state}")
        target = not original_state

        # Try to flip
        try:
            wr = await set_to_opposite(target, before)
            print(f"  write.flip OK: {str(wr)[:80]}")
        except Exception as e:
            log(name + " (flip)", "✗", err=f"flip failed: {type(e).__name__}: {e!s:.80}")
            return

        # Verify
        await asyncio.sleep(1)
        after = await read_fn(*pre_args)
        actual = extract_current(after)
        if actual == target:
            log(name + " (flip)", "✓", from_to=f"{original_state}→{actual}")
        else:
            log(name + " (flip)", "✗", err=f"expected {target}, got {actual}")
    except Exception as e:
        log(name + " (flip)", "✗", err=f"exception: {type(e).__name__}: {e!s:.80}")
    finally:
        # Restore
        if original_state is not None:
            try:
                wr = await set_to_original(original_state, before)
                await asyncio.sleep(1)
                final = await read_fn(*pre_args)
                final_state = extract_current(final)
                if final_state == original_state:
                    log(name + " (restore)", "✓", restored_to=original_state)
                else:
                    log(name + " (restore)", "✗ MANUAL FIX",
                        was=original_state, now=final_state)
            except Exception as e:
                log(name + " (restore)", "✗ MANUAL FIX",
                    err=f"{type(e).__name__}: {e!s:.80}",
                    expected_state=original_state)


async def run_writes() -> None:
    print("\n" + "═" * 75)
    print("PHASE B: round-trip test write tools (router state always restored)")
    print("═" * 75)

    # 1. ALG.ftp toggle (very safe)
    await round_trip_test(
        "js_set_alg(ftp)",
        read_fn=js.js_get_alg,
        write_fn=lambda v, _: js.js_set_alg(ftp=v),
        extract_current=lambda d: d.get("ftp") == "on",
        set_to_opposite=lambda target, _: js.js_set_alg(ftp=target),
        set_to_original=lambda original, _: js.js_set_alg(ftp=original),
    )

    # 2. Firewall WAN ping toggle (currently off — toggle on/off)
    await round_trip_test(
        "js_set_firewall(wan_ping)",
        read_fn=js.js_get_firewall,
        write_fn=lambda v, _: js.js_set_firewall(wan_ping=v),
        extract_current=lambda d: d.get("wan_ping") == "on",
        set_to_opposite=lambda target, _: js.js_set_firewall(wan_ping=target),
        set_to_original=lambda original, _: js.js_set_firewall(wan_ping=original),
    )

    # 3. UPnP enable toggle — careful, currently used by tailscale
    await round_trip_test(
        "js_set_upnp",
        read_fn=js.js_get_upnp,
        write_fn=lambda v, _: js.js_set_upnp(v),
        extract_current=lambda d: d.get("enable", {}).get("enable") == "on",
        set_to_opposite=lambda target, _: js.js_set_upnp(target),
        set_to_original=lambda original, _: js.js_set_upnp(original),
    )

    # 4. EasyMesh toggle
    await round_trip_test(
        "js_set_easymesh",
        read_fn=js.js_get_easymesh,
        write_fn=lambda v, _: js.js_set_easymesh(v),
        extract_current=lambda d: d.get("enable") == "on",
        set_to_opposite=lambda target, _: js.js_set_easymesh(target),
        set_to_original=lambda original, _: js.js_set_easymesh(original),
    )

    # 5. Smart Connect toggle
    await round_trip_test(
        "js_set_smart_connect",
        read_fn=js.js_get_smart_connect,
        write_fn=lambda v, _: js.js_set_smart_connect(v),
        extract_current=lambda d: d.get("smart_enable") == "on",
        set_to_opposite=lambda target, _: js.js_set_smart_connect(target),
        set_to_original=lambda original, _: js.js_set_smart_connect(original),
    )

    # 6. WiFi 5G mu_mimo toggle (no client disconnect)
    await round_trip_test(
        "js_set_wifi_features(5g, mu_mimo)",
        read_fn=lambda: js.js_get_wifi("5g"),
        write_fn=lambda v, _: js.js_set_wifi_features("5g", mu_mimo=v),
        extract_current=lambda d: d.get("mu_mimo") == "on",
        set_to_opposite=lambda target, _: js.js_set_wifi_features("5g", mu_mimo=target),
        set_to_original=lambda original, _: js.js_set_wifi_features("5g", mu_mimo=original),
    )

    # 7. Scheduled reboot enable toggle (low-risk; even if accidentally left on, fires Mon 3am)
    await round_trip_test(
        "js_set_scheduled_reboot",
        read_fn=js.js_get_scheduled_reboot,
        write_fn=lambda v, _: js.js_set_scheduled_reboot(enable=v),
        extract_current=lambda d: d.get("enable") == "on",
        set_to_opposite=lambda target, _: js.js_set_scheduled_reboot(enable=target),
        set_to_original=lambda original, _: js.js_set_scheduled_reboot(enable=original),
    )

    # 8. DMZ enable toggle (currently off, no IP — toggle on with bogus IP, then off)
    await round_trip_test(
        "js_set_dmz",
        read_fn=js.js_get_dmz,
        write_fn=lambda v, _: js.js_set_dmz(v, "192.168.0.99"),
        extract_current=lambda d: d.get("enable") == "on",
        set_to_opposite=lambda target, before: js.js_set_dmz(target, "192.168.0.99" if target else ""),
        set_to_original=lambda original, before: js.js_set_dmz(original, before.get("ipaddr", "")),
    )


# ──────────────────────────────  main


async def main() -> None:
    try:
        await run_reads()
        await run_writes()
    except Exception:
        traceback.print_exc()
    finally:
        await close_js_backend()

    REPORT_PATH.write_text(json.dumps(results, indent=2, default=str))

    # Summary
    print("\n" + "═" * 75)
    print("SUMMARY")
    print("═" * 75)
    by_status: dict[str, int] = {}
    for name, info in results.items():
        s = info["status"]
        by_status[s] = by_status.get(s, 0) + 1
    for s, c in by_status.items():
        print(f"  {s}: {c}")
    print(f"\nReport: {REPORT_PATH}")
    fails = sum(1 for v in results.values() if v["status"].startswith("✗"))
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
