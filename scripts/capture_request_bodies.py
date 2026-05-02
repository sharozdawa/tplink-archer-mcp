"""Capture the exact pre-encryption request bodies the browser sends.

We've already proven that the AX53 firmware refuses many endpoints when
called via the library, but works fine in the browser. The library handles
encryption correctly (proven by the endpoints that DO work), so the
difference must be in the *body content* being encrypted.

This script hooks `JSON.stringify` to capture every body the JS layer
serializes, correlates it with the URL of the next outgoing fetch, and
saves the (path, plaintext_body) pairs.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Page, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tplink_archer_mcp.config import load_config


OUT_PATH = Path(__file__).resolve().parent / "captured_request_bodies.json"


# Hook installed BEFORE any page script. Patches JSON.stringify and fetch
# to capture (timestamp, body) pairs that we can correlate.
HOOK = """
(() => {
    if (window.__capInstalled) return;
    window.__capInstalled = true;
    window.__captures = [];

    // 1. Hook JSON.stringify — capture ONLY plaintexts that look like an API
    //    request body (have 'operation' key OR very small, no localization keys)
    const origStringify = JSON.stringify;
    window.__bodyQueue = [];   // chronological queue of likely API bodies
    JSON.stringify = function(value, replacer, space) {
        const out = origStringify.call(this, value, replacer, space);
        try {
            if (value && typeof value === 'object' && !Array.isArray(value)) {
                const k = Object.keys(value);
                // Skip i18n: {l:"en_US", k:"...", s:"..."}
                if (k.length === 3 && k.includes('l') && k.includes('k') && k.includes('s')) {
                    /* skip */
                } else if (k.includes('operation') || (k.length <= 8 && k.length > 0 && out.length < 500)) {
                    window.__bodyQueue.push({
                        body: value,
                        keys: k,
                        json_size: out.length,
                        ts: performance.now()
                    });
                    // cap to last 200 entries
                    if (window.__bodyQueue.length > 200) {
                        window.__bodyQueue = window.__bodyQueue.slice(-200);
                    }
                }
            }
        } catch(e) {}
        return out;
    };

    // 2. Hook fetch — captures the actual outgoing wire request
    const origFetch = window.fetch;
    window.fetch = async function(...args) {
        const url = typeof args[0] === 'string' ? args[0] : args[0].url;
        const opts = args[1] || (args[0] && args[0].body !== undefined ? args[0] : {});
        const wireBody = opts && opts.body ? opts.body : '';
        const isApi = typeof url === 'string' && url.includes('/cgi-bin/luci');
        const captureSnap = isApi ? {
            transport: 'fetch',
            url,
            method: (opts && opts.method) || 'GET',
            wire_body: typeof wireBody === 'string' ? wireBody.slice(0, 4000) : '(binary)',
            wire_body_size: typeof wireBody === 'string' ? wireBody.length : -1,
            inferred_plaintext: lastStringified ? lastStringified.body : null,
            ts: performance.now()
        } : null;
        const resp = await origFetch.apply(this, args);
        if (captureSnap) {
            try {
                const respClone = resp.clone();
                const respText = await respClone.text();
                captureSnap.response_text = respText.slice(0, 1000);
            } catch(e) {}
            window.__captures.push(captureSnap);
        }
        return resp;
    };

    // 3. Hook XMLHttpRequest — Vue/axios apps usually use XHR
    const OrigXHR = window.XMLHttpRequest;
    function PatchedXHR() {
        const xhr = new OrigXHR();
        let snap = null;

        const origOpen = xhr.open;
        xhr.open = function(method, url, ...rest) {
            const isApi = typeof url === 'string' && url.includes('/cgi-bin/luci');
            if (isApi) {
                snap = {
                    transport: 'xhr',
                    url,
                    method,
                    wire_body: '',
                    wire_body_size: 0,
                    inferred_plaintext: null,
                    candidate_plaintexts: [],
                    ts: performance.now()
                };
            }
            return origOpen.apply(xhr, [method, url, ...rest]);
        };

        const origSend = xhr.send;
        xhr.send = function(body) {
            if (snap) {
                snap.wire_body = (typeof body === 'string') ? body.slice(0, 4000) : (body ? '(binary)' : '');
                snap.wire_body_size = (typeof body === 'string') ? body.length : (body ? -1 : 0);
                // Take the LAST 5 captured API-looking body candidates from the queue.
                // The actual request body is almost certainly the most recent one.
                const queue = window.__bodyQueue || [];
                snap.candidate_plaintexts = queue.slice(-5).reverse();
                if (queue.length > 0) {
                    snap.inferred_plaintext = queue[queue.length - 1].body;
                }
            }
            return origSend.apply(xhr, arguments);
        };

        xhr.addEventListener('load', function() {
            if (snap) {
                try { snap.response_text = xhr.responseText.slice(0, 1500); } catch(e) {}
                snap.status = xhr.status;
                window.__captures.push(snap);
            }
        });

        return xhr;
    }
    PatchedXHR.prototype = OrigXHR.prototype;
    window.XMLHttpRequest = PatchedXHR;
})();
"""


async def main() -> None:
    cfg = load_config()
    base = f"http://{cfg.host}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900})
        await ctx.add_init_script(HOOK)
        page = await ctx.new_page()

        # Login
        print(f"  → login")
        await page.goto(base, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        for sel in ['input[type="password"]:visible']:
            try:
                await page.locator(sel).first.fill(cfg.password, timeout=2000); break
            except Exception: continue
        for sel in ['button:has-text("LOG IN")']:
            try:
                await page.locator(sel).first.click(timeout=2000); break
            except Exception: continue
        await page.wait_for_function("!window.location.hash.includes('#/login')", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=10000)

        # Navigate to specific pages we want bodies for
        target_routes = [
            "/lan",
            "/dhcpServer",
            "/portForwarding",
            "/dmz",
            "/firewall",
            "/internetBasic",
            "/upnp",
            "/easyMesh",
            "/timeSettings",
            "/ipv6",
            "/qos",
            "/parentalControlsV4",
            "/wireless2g",  # known-working baseline
        ]
        for route in target_routes:
            print(f"  → {route}")
            url = base + "/webpages/index.html#" + route
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"    ! {e}")

        # Read captures
        captures = await page.evaluate("window.__captures || []")

        # Logout
        try:
            await page.evaluate("async () => { try { await fetch('/cgi-bin/luci/;stok=/login?form=logout',{method:'POST'}); } catch(e){} }")
        except Exception:
            pass
        await browser.close()

    # Group by API path + form
    by_form: dict = {}
    for cap in captures:
        u = urlparse(cap["url"])
        # Strip the stok segment so similar requests group together
        path_no_stok = "/".join(part for part in u.path.split("/") if not part.startswith("stok="))
        qs = parse_qs(u.query)
        form = qs.get("form", [""])[0]
        key = f"{path_no_stok}?form={form}"
        if key not in by_form:
            by_form[key] = []
        by_form[key].append({
            "wire_size": cap["wire_body_size"],
            "wire_preview": cap["wire_body"][:200],
            "inferred_plaintext": cap["inferred_plaintext"],
            "response_preview": cap.get("response_text", "")[:300],
        })

    OUT_PATH.write_text(json.dumps(by_form, indent=2, default=str))
    print(f"\n✓ {len(captures)} captures across {len(by_form)} unique form-paths")
    print(f"  Saved: {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
