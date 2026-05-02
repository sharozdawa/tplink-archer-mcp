"""Round-trip safety test for the Playwright write tools.

For each tool tested:
    1. Read current value
    2. Write the OPPOSITE value
    3. Read again, verify it actually changed
    4. Write the ORIGINAL value back (always, even if step 3 failed)
    5. Read again, verify restoration

A try/finally guarantees the router state is restored even if any
intermediate step crashes.

Tools tested are picked for ZERO real-world impact:
    - Scheduled reboot enable: just toggles whether a future event is armed
    - Firewall WAN ping response: trivial ICMP responder
    - ALG FTP enable: rarely-used protocol helper

Tools NOT tested here (skip until user explicitly requests):
    - Wireless settings (would disconnect clients)
    - WAN/LAN/DHCP changes (would disrupt network)
    - Admin password (could lock us out)
    - Factory reset / reboot (destructive)
    - DMZ / port forwards (would touch live rules)
    - UPnP (currently in use by tailscale)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp import tools_playwright as pw_read
from tplink_archer_mcp import tools_playwright_write as pw_write
from tplink_archer_mcp.playwright_backend import close_backend


PASS, FAIL, SKIP = "✓ PASS", "✗ FAIL", "⊝ SKIP"
results: list[tuple[str, str, str]] = []


def log(name: str, status: str, details: str = "") -> None:
    results.append((name, status, details))
    print(f"  {status}  {name:50} {details}")


async def test_scheduled_reboot() -> None:
    """Toggle scheduled-reboot enable on/off, ensure round-trip clean."""
    name = "pw_set_scheduled_reboot"
    print(f"\n=== {name} ===")
    original_enabled: bool | None = None
    try:
        # 1. read current
        before = await pw_read.pw_get_scheduled_reboot()
        sched = before.get("scheduled_reboot", {})
        original_enabled_raw = sched.get("enable", "off")
        original_enabled = (original_enabled_raw == "on")
        print(f"  read.before: enable={original_enabled_raw!r}")
        target = not original_enabled

        # 2. write opposite
        write_result = await pw_write.pw_set_scheduled_reboot(
            enabled=target,
            cycle=sched.get("cycle", "week"),
            day=sched.get("day", "Mon"),
            time_hhmm=sched.get("time", "03:00"),
        )
        print(f"  write.opposite: ok={write_result.get('ok')}, save_resp={write_result.get('save_response')}")

        # 3. read after
        after = await pw_read.pw_get_scheduled_reboot()
        actual = (after.get("scheduled_reboot", {}).get("enable") == "on")
        print(f"  read.after: enable={actual!r}, expected={target!r}")
        if actual == target:
            log(name + " (toggle)", PASS, f"changed {original_enabled} → {actual}")
        else:
            log(name + " (toggle)", FAIL, f"expected {target}, got {actual}")
    except Exception as e:
        log(name + " (toggle)", FAIL, f"exception: {type(e).__name__}: {e!s:.80}")
    finally:
        # 4. restore
        if original_enabled is not None:
            try:
                await pw_write.pw_set_scheduled_reboot(
                    enabled=original_enabled,
                    cycle="week", day="Mon", time_hhmm="03:00",
                )
                final = await pw_read.pw_get_scheduled_reboot()
                final_enabled = (final.get("scheduled_reboot", {}).get("enable") == "on")
                if final_enabled == original_enabled:
                    log(name + " (restore)", PASS, f"restored to {original_enabled}")
                else:
                    log(name + " (restore)", FAIL,
                        f"NOT restored — was {original_enabled}, now {final_enabled} — MANUAL FIX NEEDED")
            except Exception as e:
                log(name + " (restore)", FAIL, f"restore exception: {type(e).__name__}: {e!s:.80}")


async def test_firewall_wan_ping() -> None:
    """Toggle firewall WAN ping on/off, round-trip safe."""
    name = "pw_set_firewall(wan_ping)"
    print(f"\n=== {name} ===")
    original: bool | None = None
    try:
        before = await pw_read.pw_get_firewall()
        fw = before.get("firewall", {})
        original_raw = fw.get("wan_ping", "off")
        original = (original_raw == "on")
        print(f"  read.before: wan_ping={original_raw!r}")
        target = not original

        write_result = await pw_write.pw_set_firewall(wan_ping=target)
        print(f"  write.opposite: ok={write_result.get('ok')}, save_resp={write_result.get('save_response')}")

        after = await pw_read.pw_get_firewall()
        actual = (after.get("firewall", {}).get("wan_ping") == "on")
        print(f"  read.after: wan_ping={actual!r}, expected={target!r}")
        if actual == target:
            log(name + " (toggle)", PASS, f"changed {original} → {actual}")
        else:
            log(name + " (toggle)", FAIL, f"expected {target}, got {actual}")
    except Exception as e:
        log(name + " (toggle)", FAIL, f"exception: {type(e).__name__}: {e!s:.80}")
    finally:
        if original is not None:
            try:
                await pw_write.pw_set_firewall(wan_ping=original)
                final = await pw_read.pw_get_firewall()
                final_val = (final.get("firewall", {}).get("wan_ping") == "on")
                if final_val == original:
                    log(name + " (restore)", PASS, f"restored to {original}")
                else:
                    log(name + " (restore)", FAIL,
                        f"NOT restored — was {original}, now {final_val} — MANUAL FIX NEEDED")
            except Exception as e:
                log(name + " (restore)", FAIL, f"restore exception: {type(e).__name__}: {e!s:.80}")


async def test_alg_ftp() -> None:
    """Toggle ALG FTP on/off, round-trip safe."""
    name = "pw_set_alg(ftp)"
    print(f"\n=== {name} ===")
    original: bool | None = None
    try:
        before = await pw_read.pw_get_alg()
        alg = before.get("alg", {})
        original_raw = alg.get("ftp", "off")
        original = (original_raw == "on")
        print(f"  read.before: alg.ftp={original_raw!r}")
        target = not original

        write_result = await pw_write.pw_set_alg(ftp=target)
        print(f"  write.opposite: ok={write_result.get('ok')}, save_resp={write_result.get('save_response')}")

        after = await pw_read.pw_get_alg()
        actual = (after.get("alg", {}).get("ftp") == "on")
        print(f"  read.after: alg.ftp={actual!r}, expected={target!r}")
        if actual == target:
            log(name + " (toggle)", PASS, f"changed {original} → {actual}")
        else:
            log(name + " (toggle)", FAIL, f"expected {target}, got {actual}")
    except Exception as e:
        log(name + " (toggle)", FAIL, f"exception: {type(e).__name__}: {e!s:.80}")
    finally:
        if original is not None:
            try:
                await pw_write.pw_set_alg(ftp=original)
                final = await pw_read.pw_get_alg()
                final_val = (final.get("alg", {}).get("ftp") == "on")
                if final_val == original:
                    log(name + " (restore)", PASS, f"restored to {original}")
                else:
                    log(name + " (restore)", FAIL,
                        f"NOT restored — was {original}, now {final_val} — MANUAL FIX NEEDED")
            except Exception as e:
                log(name + " (restore)", FAIL, f"restore exception: {type(e).__name__}: {e!s:.80}")


async def main() -> None:
    try:
        await test_scheduled_reboot()
        await test_firewall_wan_ping()
        await test_alg_ftp()
    finally:
        await close_backend()

    print("\n" + "═" * 70)
    print("SUMMARY")
    print("═" * 70)
    passes = sum(1 for _, s, _ in results if s == PASS)
    fails = sum(1 for _, s, _ in results if s == FAIL)
    for n, s, d in results:
        print(f"  {s}  {n:50} {d}")
    print(f"\n  {passes} pass / {fails} fail / {len(results)} total")

    # Exit non-zero if any failure (so we notice in CI)
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
