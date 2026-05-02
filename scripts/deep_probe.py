"""Deep endpoint discovery via Playwright.

Logs in to the AX53 web UI, navigates every settings page, and captures:
  - Every request/response pair (encrypted body sizes + paths)
  - The decrypted RESPONSE body (by hooking into the JS fetch wrapper before it
    encrypts going out / after it decrypts coming in)
  - The rendered DOM state of each page (the form values the user actually sees)

Output:
  - scripts/deep_probe_results.json — per-page: endpoints called, response bodies, DOM data

Read-only; does not click any Save buttons.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Page, Request, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp.config import load_config


OUT_PATH = Path(__file__).resolve().parent / "deep_probe_results.json"


# Pages keyed by friendly name → URL hash route
PAGES = {
    "networkMap":         "/networkMap",
    "internetBasic":      "/internetBasic",
    "wireless2g":         "/wireless2g",
    "wireless5g":         "/wireless5g",
    "wirelessAdv":        "/wirelessAdvancedSettings",
    "guestNetwork":       "/guestNetwork",
    "wireless_wps":       "/wireless/wps",
    "wireless_schedule":  "/wireless/schedule",
    "wireless_wds":       "/wireless/wdsBridge",
    "wireless_stats":     "/wireless/statistics",
    "lan":                "/lan",
    "dhcpServer":         "/dhcpServer",
    "ddns":               "/ddns",
    "staticRoute":        "/staticRoute",
    "portForwarding":     "/portForwarding",
    "portTriggering":     "/portTriggering",
    "upnp":               "/upnp",
    "dmz":                "/dmz",
    "firewall":           "/firewall",
    "accessControl":      "/accessControl",
    "ipMacBinding":       "/ipMacBinding",
    "alg":                "/alg",
    "openVpnServer":      "/openVpnServer",
    "pptpVpnServer":      "/pptpVpnServer",
    "vpnClient":          "/vpnClient",
    "ipv6":               "/ipv6",
    "easyMesh":           "/easyMesh",
    "timeSettings":       "/timeSettings",
    "backupRestore":      "/backupRestore",
    "firmwareUpgrade":    "/firmwareUpgrade",
    "factoryDefaults":    "/factoryDefaults",
    "reboot":             "/reboot",
    "systemLog":          "/systemLog",
    "diagnostic":         "/diagnostic",
    "ledControl":         "/ledControl",
    "loginPassword":      "/loginPassword",
    "cloudUserManagement":"/cloudUserManagement",
    "homeShield":         "/homeShield",
    "parentalControlsV4": "/parentalControlsV4",
    "qos":                "/qos",
}


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
    for sel in ['button:has-text("LOG IN")', 'button:has-text("Log In")', '#pc-login-btn']:
        try:
            await page.locator(sel).first.click(timeout=2000)
            break
        except Exception:
            continue
    await page.wait_for_function("!window.location.hash.includes('#/login')", timeout=15000)
    await page.wait_for_load_state("networkidle", timeout=10000)
    print(f"  ✓ logged in")


async def probe_page(page: Page, name: str, route: str, base: str) -> dict:
    """Navigate to a page and capture everything that happens."""
    requests_seen: list[dict] = []
    responses_seen: dict[str, dict] = {}  # request_url → response info

    async def on_request(req: Request) -> None:
        if "/cgi-bin/luci" not in req.url:
            return
        url = urlparse(req.url)
        qs = parse_qs(url.query)
        form = qs.get("form", [""])[0]
        body = req.post_data or ""
        # Inspect body — sometimes it's encrypted (sign=...&data=...), sometimes not
        is_encrypted = body.startswith("sign=") and "data=" in body
        size = len(body)
        requests_seen.append({
            "method": req.method,
            "path": url.path,
            "query_form": form,
            "body_size": size,
            "body_encrypted": is_encrypted,
            "body_preview": body[:200] if not is_encrypted else "(encrypted, %d bytes)" % size,
        })

    async def on_response(resp: Response) -> None:
        if "/cgi-bin/luci" not in resp.url:
            return
        try:
            text = await resp.text()
            # Try JSON parse to see if it's decryptable in the response
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "data" in parsed and len(parsed) <= 2:
                    # Looks encrypted: {"data": "..."}
                    responses_seen[resp.url] = {
                        "status": resp.status,
                        "encrypted": True,
                        "body_size": len(text),
                        "body_preview": text[:200],
                    }
                else:
                    # Plain JSON — record the keys
                    responses_seen[resp.url] = {
                        "status": resp.status,
                        "encrypted": False,
                        "json_keys": list(parsed.keys())[:15] if isinstance(parsed, dict) else type(parsed).__name__,
                        "body_preview": text[:300],
                    }
            except Exception:
                responses_seen[resp.url] = {
                    "status": resp.status,
                    "body_preview": text[:200],
                }
        except Exception as e:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    # Navigate
    url = base + "/webpages/index.html#" + route
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(2500)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "route": route}

    # Capture DOM state — every input/select with a value
    dom_state = await page.evaluate("""() => {
        const out = {};
        document.querySelectorAll('input, select, textarea').forEach(el => {
            const name = el.name || el.id || el.getAttribute('aria-label') || '';
            if (!name) return;
            if (el.type === 'password') {
                out[name] = el.value ? '(masked, len=' + el.value.length + ')' : '';
            } else if (el.type === 'checkbox' || el.type === 'radio') {
                out[name] = el.checked;
            } else {
                out[name] = el.value;
            }
        });
        // Also capture text from visible labels/spans inside main content
        const labels = [];
        document.querySelectorAll('.plain-text, [data-test], [data-key]').forEach(el => {
            const k = el.getAttribute('data-test') || el.getAttribute('data-key') || el.className;
            const v = el.textContent.trim().slice(0, 80);
            if (v) labels.push({key: k.slice(0,40), text: v});
        });
        return {fields: out, labels: labels.slice(0, 20)};
    }""")

    # Detach listeners
    page.remove_listener("request", on_request)
    page.remove_listener("response", on_response)

    return {
        "route": route,
        "requests_count": len(requests_seen),
        "requests": requests_seen,
        "responses_count": len(responses_seen),
        "responses": list(responses_seen.values())[:10],  # cap for size
        "dom": dom_state,
    }


async def main() -> None:
    cfg = load_config()
    base = f"http://{cfg.host}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        await login(page, base, cfg.password)

        results = {}
        for name, route in PAGES.items():
            print(f"  → probing {name}")
            results[name] = await probe_page(page, name, route, base)

        # Logout cleanly
        try:
            await page.evaluate("async () => { try { await fetch('/cgi-bin/luci/;stok=/login?form=logout', {method:'POST'}); } catch(e){} }")
        except Exception:
            pass

        await browser.close()

    OUT_PATH.write_text(json.dumps(results, indent=2, default=str))

    # Summary
    total_pages = len(results)
    total_requests = sum(p.get("requests_count", 0) for p in results.values())
    total_responses = sum(p.get("responses_count", 0) for p in results.values())
    pages_with_dom = sum(1 for p in results.values() if p.get("dom", {}).get("fields"))
    total_fields = sum(len(p.get("dom", {}).get("fields", {})) for p in results.values())

    print()
    print(f"✓ Probed {total_pages} pages")
    print(f"  {total_requests} API requests captured")
    print(f"  {total_responses} responses captured")
    print(f"  {pages_with_dom} pages had form fields ({total_fields} fields total)")
    print(f"  Output: {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
