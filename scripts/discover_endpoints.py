"""Auto-discover every API endpoint the TP-Link router web UI uses.

Drives Chromium through every page of the AX53 admin UI, intercepts every
XHR/fetch request (post-login), and writes them out as a JSON catalog.

Usage:
    uv run python scripts/discover_endpoints.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp.config import load_config


CATALOG_PATH = Path(__file__).resolve().parents[1] / "src" / "tplink_archer_mcp" / "endpoints_catalog.json"


PAGES_TO_VISIT = [
    "/webpages/index.html#/networkMap",
    "/webpages/index.html#/internetBasic",
    "/webpages/index.html#/wireless2g",
    "/webpages/index.html#/wireless5g",
    "/webpages/index.html#/wirelessAdvancedSettings",
    "/webpages/index.html#/guestNetwork",
    "/webpages/index.html#/wireless/wps",
    "/webpages/index.html#/wireless/schedule",
    "/webpages/index.html#/wireless/wdsBridge",
    "/webpages/index.html#/wireless/statistics",
    "/webpages/index.html#/lan",
    "/webpages/index.html#/dhcpServer",
    "/webpages/index.html#/ddns",
    "/webpages/index.html#/staticRoute",
    "/webpages/index.html#/portForwarding",
    "/webpages/index.html#/portTriggering",
    "/webpages/index.html#/upnp",
    "/webpages/index.html#/dmz",
    "/webpages/index.html#/firewall",
    "/webpages/index.html#/accessControl",
    "/webpages/index.html#/ipMacBinding",
    "/webpages/index.html#/alg",
    "/webpages/index.html#/openVpnServer",
    "/webpages/index.html#/pptpVpnServer",
    "/webpages/index.html#/vpnClient",
    "/webpages/index.html#/ipv6",
    "/webpages/index.html#/easyMesh",
    "/webpages/index.html#/timeSettings",
    "/webpages/index.html#/backupRestore",
    "/webpages/index.html#/firmwareUpgrade",
    "/webpages/index.html#/factoryDefaults",
    "/webpages/index.html#/reboot",
    "/webpages/index.html#/systemLog",
    "/webpages/index.html#/diagnostic",
    "/webpages/index.html#/ledControl",
    "/webpages/index.html#/loginPassword",
    "/webpages/index.html#/cloudUserManagement",
    "/webpages/index.html#/homeShield",
    "/webpages/index.html#/parentalControlsV4",
    "/webpages/index.html#/qos",
]


async def main() -> None:
    cfg = load_config()
    base = f"http://{cfg.host}"

    requests_seen: list[dict] = []
    logged_in = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        async def on_request(req: Request) -> None:
            if "/cgi-bin/luci" not in req.url:
                return
            url = urlparse(req.url)
            qs = parse_qs(url.query)
            form_qs = qs.get("form", [""])[0]
            body = req.post_data or ""
            payload = None
            form_body = ""
            try:
                if body:
                    payload = json.loads(body)
            except Exception:
                payload = body[:200]

            # The JSON-RPC POST body sometimes carries the operation/form keys
            op = ""
            if isinstance(payload, dict):
                op = payload.get("operation", "")
                if not form_qs:
                    form_body = payload.get("form", "")

            requests_seen.append({
                "method": req.method,
                "path": url.path,
                "stok_segment": [seg for seg in url.path.split("/") if seg.startswith("stok=")],
                "form": form_qs or form_body,
                "operation": op,
                "payload": payload,
                "page_route": page.url.split("#")[-1] if "#" in page.url else "(no-hash)",
                "post_login": logged_in,
            })

        page.on("request", on_request)

        # ---- LOGIN ----
        print(f"→ navigating to {base}")
        await page.goto(base, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # Try multiple known password selectors (different firmware versions)
        password_selectors = [
            "#pc-login-password",
            'input[type="password"]:visible',
            'input.text-text-default[type="password"]',
            'input[autocomplete="current-password"]',
        ]
        login_button_selectors = [
            "#pc-login-btn",
            'button.btn-msg:has-text("LOG IN")',
            'button:has-text("LOG IN")',
            'button:has-text("Log In")',
        ]

        filled = False
        for sel in password_selectors:
            try:
                await page.locator(sel).first.fill(cfg.password, timeout=2000)
                print(f"✓ filled password using {sel}")
                filled = True
                break
            except Exception:
                continue
        if not filled:
            print("✗ could not find password input")
            await browser.close()
            return

        clicked = False
        for sel in login_button_selectors:
            try:
                await page.locator(sel).first.click(timeout=2000)
                print(f"✓ clicked login using {sel}")
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            await page.keyboard.press("Enter")
            print("✓ submitted via Enter key")

        # Wait for redirect to dashboard (#/networkMap or any non-login hash)
        try:
            await page.wait_for_function(
                "!window.location.hash.includes('#/login')",
                timeout=15000,
            )
            await page.wait_for_load_state("networkidle", timeout=10000)
            logged_in = True
            print(f"✓ logged in — current URL: {page.url}")
        except Exception as e:
            print(f"✗ login appears to have failed: {e}")
            print(f"  current URL: {page.url}")
            await browser.close()
            return

        # Allow the dashboard's first batch of XHRs to fire
        await page.wait_for_timeout(2500)

        # ---- WALK ----
        for path in PAGES_TO_VISIT:
            url = base + path
            print(f"→ {path}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                # let lazy-loaded forms + their per-section XHRs settle
                await page.wait_for_timeout(2000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
            except Exception as e:
                print(f"  ! {path}: {type(e).__name__}: {e}")

        # Try logout cleanly so we don't leave a dangling session
        try:
            await page.evaluate(
                "async () => { try { await fetch('/cgi-bin/luci/;stok=/login?form=logout', {method:'POST'}); } catch(e){} }"
            )
        except Exception:
            pass

        await browser.close()

    # ---- AGGREGATE ----
    discovered: dict[str, dict] = {}
    for req in requests_seen:
        if not req["post_login"]:
            continue
        form = req["form"] or "(no-form)"
        bucket = discovered.setdefault(form, {
            "form": form,
            "paths": set(),
            "methods": set(),
            "operations": set(),
            "seen_on_pages": set(),
            "first_payload_sample": None,
        })
        bucket["paths"].add(req["path"])
        bucket["methods"].add(req["method"])
        if req["operation"]:
            bucket["operations"].add(req["operation"])
        if req["page_route"]:
            bucket["seen_on_pages"].add(req["page_route"])
        if bucket["first_payload_sample"] is None and req["payload"] is not None:
            bucket["first_payload_sample"] = req["payload"]

    out = {
        form: {k: (sorted(v) if isinstance(v, set) else v) for k, v in bucket.items()}
        for form, bucket in discovered.items()
    }

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(out, indent=2, default=str))
    pre_login = sum(1 for r in requests_seen if not r["post_login"])
    print(
        f"\n✓ wrote {CATALOG_PATH}\n"
        f"  {len(out)} unique forms across {len(requests_seen) - pre_login} post-login requests "
        f"({pre_login} pre-login requests skipped)"
    )


if __name__ == "__main__":
    asyncio.run(main())
