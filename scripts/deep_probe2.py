"""Deep endpoint discovery v2 — bypass encryption by hooking JSON.parse.

The AX53 web UI receives encrypted responses, then decrypts them, then
JSON.parses the decrypted text. By monkey-patching JSON.parse before login,
we capture every parsed object — including all decrypted API responses.

The result: complete plaintext access to every API call's response.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Page

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp.config import load_config


OUT_PATH = Path(__file__).resolve().parent / "deep_probe_v2_results.json"


PAGES = [
    "/networkMap","/internetBasic","/wireless2g","/wireless5g",
    "/wirelessAdvancedSettings","/guestNetwork","/wireless/wps",
    "/wireless/schedule","/wireless/wdsBridge","/wireless/statistics",
    "/lan","/dhcpServer","/ddns","/staticRoute","/portForwarding",
    "/portTriggering","/upnp","/dmz","/firewall","/accessControl",
    "/ipMacBinding","/alg","/openVpnServer","/pptpVpnServer","/vpnClient",
    "/ipv6","/easyMesh","/timeSettings","/backupRestore","/firmwareUpgrade",
    "/factoryDefaults","/reboot","/systemLog","/diagnostic","/ledControl",
    "/loginPassword","/cloudUserManagement","/homeShield","/parentalControlsV4","/qos",
]


JSON_HOOK = """
(() => {
    if (window.__hookInstalled) return;
    window.__hookInstalled = true;
    window.__parsedObjects = [];
    const orig = JSON.parse;
    JSON.parse = function(text, reviver) {
        const result = orig.call(this, text, reviver);
        try {
            if (result && typeof result === 'object' && !Array.isArray(result)) {
                const keys = Object.keys(result);
                // Filter: skip empty objects, skip Vue/internal noise (single letter keys, etc.)
                if (keys.length > 0) {
                    // Capture if it looks like an API response (has 'success' or many fields)
                    const looksApi = keys.includes('success') || keys.includes('errorcode')
                                  || keys.length >= 3;
                    if (looksApi && JSON.stringify(result).length < 50000) {
                        window.__parsedObjects.push({
                            keys: keys.slice(0, 20),
                            value: result,
                            timestamp: Date.now()
                        });
                    }
                }
            } else if (Array.isArray(result) && result.length > 0 && result.length < 200) {
                if (typeof result[0] === 'object' && result[0] !== null) {
                    window.__parsedObjects.push({
                        keys: ['(array)'],
                        value: result.slice(0, 50),
                        timestamp: Date.now()
                    });
                }
            }
        } catch(e) {}
        return result;
    };
})();
"""


async def login(page: Page, base: str, password: str) -> None:
    print(f"  → navigating to {base}")
    await page.goto(base, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    for sel in ['input[type="password"]:visible', '#pc-login-password']:
        try:
            await page.locator(sel).first.fill(password, timeout=2000)
            break
        except Exception:
            continue
    for sel in ['button:has-text("LOG IN")', 'button:has-text("Log In")']:
        try:
            await page.locator(sel).first.click(timeout=2000)
            break
        except Exception:
            continue
    await page.wait_for_function("!window.location.hash.includes('#/login')", timeout=15000)
    await page.wait_for_load_state("networkidle", timeout=10000)
    print(f"  ✓ logged in")


async def main() -> None:
    cfg = load_config()
    base = f"http://{cfg.host}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        # Install hook on every page navigation (in case the SPA reloads JSON)
        await page.add_init_script(JSON_HOOK)

        await login(page, base, cfg.password)
        await page.wait_for_timeout(1500)

        # Map of page → request path → decrypted response
        results: dict = {}
        all_endpoints: dict = {}  # path → first decrypted response found

        # Track which API path was just requested (sequentially)
        recent_requests: list = []

        async def on_request(req) -> None:
            if "/cgi-bin/luci" not in req.url or "/login" in req.url:
                return
            url = urlparse(req.url)
            qs = parse_qs(url.query)
            form = qs.get("form", [""])[0]
            recent_requests.append({"path": url.path, "form": form, "ts": asyncio.get_event_loop().time()})

        page.on("request", on_request)

        for route in PAGES:
            print(f"  → {route}")
            # Reset the captured objects before navigating
            try:
                await page.evaluate("window.__parsedObjects = []")
                recent_requests.clear()
            except Exception:
                pass

            try:
                await page.goto(base + "/webpages/index.html#" + route, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(2500)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
            except Exception as e:
                print(f"    ! {e}")
                continue

            # Read what we captured
            captured = await page.evaluate("window.__parsedObjects || []")
            req_paths = [r["path"] for r in recent_requests]
            forms = sorted(set(r["form"] for r in recent_requests if r["form"]))

            results[route] = {
                "request_paths": list(set(req_paths)),
                "forms": forms,
                "captured_count": len(captured),
                "captured": captured[:30],  # cap per page
            }

            # Aggregate by form name into all_endpoints
            for obj in captured:
                if 'success' in obj.get('keys', []) or len(obj.get('keys', [])) >= 3:
                    val = obj.get('value', {})
                    if isinstance(val, dict):
                        # Tag with the form name(s) seen on this page
                        for f in forms:
                            if f not in all_endpoints:
                                all_endpoints[f] = {"sample": val, "page": route}

        await browser.close()

    OUT_PATH.write_text(json.dumps({
        "per_page": results,
        "endpoints_with_data": all_endpoints,
    }, indent=2, default=str))

    total_captured = sum(p["captured_count"] for p in results.values())
    pages_with_data = sum(1 for p in results.values() if p["captured_count"] > 0)
    print()
    print(f"✓ {pages_with_data}/{len(PAGES)} pages had decrypted data")
    print(f"  {total_captured} total captured objects")
    print(f"  {len(all_endpoints)} unique forms with sample data")
    print(f"  Output: {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
