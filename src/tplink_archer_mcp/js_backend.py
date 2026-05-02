"""JS-direct backend — call the AX53's own update-store from the browser.

The router's web UI ships an `update-store` ES module that exports an object
with `.read(path)`, `.write(path, body)`, `.request(path, body)`, `.load(...)`,
`.insert(...)`, `.remove(...)`, `.update(...)`, `.file(...)` methods. These
methods handle the full encryption/auth pipeline — they're what the Vue
components use for every API call.

By dynamically importing this module from `page.evaluate`, we get clean
async access to every API the UI uses, with NO selector-based form driving
and NO library limitations.

Architecture:
    JsBackend (singleton, async)
        ├─ start() — launch headless browser, login
        ├─ read(path)        → store.read(path)
        ├─ write(path, body) → store.write(path, body)
        ├─ insert(path, body) → store.insert(path, body)  (add a row, e.g. DHCP reservation)
        ├─ remove(path, body) → store.remove(path, body)  (delete a row)
        ├─ update(path, body) → store.update(path, body)  (modify a row)
        ├─ load(path, body)   → store.load(path, body)    (read variant)
        └─ request(path, body, ignore_errors=False) → store.request(...)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .config import load_config


_log = logging.getLogger(__name__)


# Init script: install JSON.parse hook so we can also retrieve raw responses
# during page navigations if needed for debugging.
_JSON_HOOK = """
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
                if ((keys.includes('success') || keys.includes('errorcode'))
                    && JSON.stringify(result).length < 80000) {
                    window.__parsedObjects.push({keys: keys.slice(0, 25), value: result, ts: Date.now()});
                }
            }
        } catch(e) {}
        return result;
    };
})();
"""


class JsBackend:
    """Long-lived headless-browser session that exposes the AX53 store via JS."""

    def __init__(self) -> None:
        self._cfg = load_config()
        self._base = f"http://{self._cfg.host}"
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._update_store_url: str | None = None  # discovered after page load

    # ──────────────────────────────  lifecycle

    async def start(self) -> None:
        if self._pw is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._ctx = await self._browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1400, "height": 900},
        )
        await self._ctx.add_init_script(_JSON_HOOK)
        self._page = await self._ctx.new_page()
        await self._login()
        await self._discover_update_store_url()

    async def close(self) -> None:
        try:
            if self._page and not self._page.is_closed():
                try:
                    await self._page.evaluate(
                        "async () => { try { await fetch('/cgi-bin/luci/;stok=/login?form=logout',{method:'POST'}); } catch(e){} }"
                    )
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._pw = None
            self._browser = None
            self._ctx = None
            self._page = None

    async def _login(self) -> None:
        page = self._page
        assert page is not None
        await page.goto(self._base, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        for sel in ['input[type="password"]:visible', "#pc-login-password"]:
            try:
                await page.locator(sel).first.fill(self._cfg.password, timeout=2000); break
            except Exception:
                continue
        for sel in ['button:has-text("LOG IN")', 'button:has-text("Log In")']:
            try:
                await page.locator(sel).first.click(timeout=2000); break
            except Exception:
                continue
        await page.wait_for_function("!window.location.hash.includes('#/login')", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=10000)
        _log.info("JS backend logged in")

    async def _discover_update_store_url(self) -> None:
        """Find the current `update-store-<HASH>.js` chunk path from the loaded page.

        The hash changes per firmware build. We discover it by reading the
        `<script type=module>` src and chasing chunk references.
        """
        assert self._page is not None
        # First try a heuristic: look for the chunk in the index.html
        try:
            html = await self._page.content()
            m = re.search(r'/webpages/js/(update-store-[A-Za-z0-9_-]+\.js)', html)
            if m:
                self._update_store_url = "/webpages/js/" + m.group(1)
                _log.info(f"update-store at {self._update_store_url}")
                return
        except Exception:
            pass

        # Otherwise query already-loaded module map via dynamic import attempts
        result = await self._page.evaluate("""async () => {
            // Walk through script tags + module imports already loaded
            const scripts = Array.from(document.scripts);
            for (const s of scripts) {
                if (s.src.includes('update-store')) return s.src;
            }
            // Try fetching root index.html and parse
            try {
                const idx = await fetch('/webpages/js/').then(r=>r.text());
                const m = idx.match(/update-store-[A-Za-z0-9_-]+\\.js/);
                if (m) return '/webpages/js/' + m[0];
            } catch(e) {}
            // Fallback hardcoded
            return '/webpages/js/update-store-DQkZxaRI.js';
        }""")
        # Strip absolute URL if returned
        if result and result.startswith("http"):
            result = "/" + result.split("/", 3)[-1]
        self._update_store_url = result or "/webpages/js/update-store-DQkZxaRI.js"
        _log.info(f"update-store at {self._update_store_url}")

    async def _ensure_session(self) -> None:
        if not self._pw or not self._page or self._page.is_closed():
            await self.close()
            await self.start()
            return
        try:
            url = self._page.url
            if "#/login" in url:
                await self._login()
                await self._discover_update_store_url()
        except Exception:
            await self.close()
            await self.start()

    # ──────────────────────────────  the store methods

    async def _store_call(self, method: str, path: str, body: Any = None) -> Any:
        """Call store.<method>(path, body) via dynamic import in the page context."""
        async with self._lock:
            await self._ensure_session()
            page = self._page
            assert page is not None
            store_url = self._update_store_url
            assert store_url is not None
            # JS-side execution
            js_args = json.dumps({"path": path, "body": body, "method": method, "url": store_url})
            result = await page.evaluate(f"""async (args) => {{
                const m = await import(args.url);
                if (!m.s || typeof m.s[args.method] !== 'function') {{
                    return {{ok: false, error: 'store.' + args.method + ' not available'}};
                }}
                try {{
                    const data = (args.body !== null && args.body !== undefined)
                        ? await m.s[args.method](args.path, args.body)
                        : await m.s[args.method](args.path);
                    return {{ok: true, data}};
                }} catch(e) {{
                    // store rejects with the error response object; surface it
                    if (e && typeof e === 'object') return {{ok: false, error: e}};
                    return {{ok: false, error: String(e)}};
                }}
            }}""", json.loads(js_args))
            if result.get("ok"):
                return result["data"]
            err = result.get("error")
            raise StoreError(method, path, err)

    async def read(self, path: str) -> Any:
        """store.read(path) — fetch current state of a setting."""
        return await self._store_call("read", path)

    async def write(self, path: str, body: dict) -> Any:
        """store.write(path, body) — replace the entire object at this endpoint."""
        return await self._store_call("write", path, body)

    async def load(self, path: str, body: dict | None = None) -> Any:
        """store.load(path, body?) — variant of read with optional body params."""
        return await self._store_call("load", path, body)

    async def insert(self, path: str, body: dict) -> Any:
        """store.insert(path, body) — add a new row (DHCP reservation, port forward, etc.)."""
        return await self._store_call("insert", path, body)

    async def remove(self, path: str, body: dict) -> Any:
        """store.remove(path, body) — delete a row by key."""
        return await self._store_call("remove", path, body)

    async def update(self, path: str, body: dict) -> Any:
        """store.update(path, body) — modify an existing row."""
        return await self._store_call("update", path, body)

    async def request(self, path: str, body: dict | None = None) -> Any:
        """store.request(path, body?) — universal escape hatch (the JS underlying call)."""
        return await self._store_call("request", path, body)


class StoreError(Exception):
    def __init__(self, method: str, path: str, error: Any) -> None:
        self.method = method
        self.path = path
        self.error = error
        super().__init__(f"store.{method}({path!r}) failed: {error!r}")


# ──────────────────────────────  module-level singleton

_backend: JsBackend | None = None
_backend_lock = asyncio.Lock()


async def get_js_backend() -> JsBackend:
    global _backend
    async with _backend_lock:
        if _backend is None:
            _backend = JsBackend()
            await _backend.start()
    return _backend


async def close_js_backend() -> None:
    global _backend
    async with _backend_lock:
        if _backend is not None:
            await _backend.close()
            _backend = None
