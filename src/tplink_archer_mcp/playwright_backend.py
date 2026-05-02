"""Playwright-driven backend for the TP-Link Archer MCP.

Maintains a long-lived headless browser session against the router and
exposes high-level read/write primitives that bypass the
`tplinkrouterc6u` library's limitations (the AX53 firmware refuses many
endpoints when called via the library, but the browser session has full
access).

Key trick: a `JSON.parse` hook installed via `add_init_script` captures
every decrypted JSON object the JS layer parses. So while the responses
on the wire are AES-encrypted, by the time they reach
`window.__parsedObjects`, they are plaintext.

Usage:

    from .playwright_backend import get_backend

    pb = await get_backend()
    data = await pb.read_page("/lan")
    # data is a list of decrypted JSON objects loaded by the LAN page

The backend is process-singleton: one browser per MCP server process.
Sessions are recycled on errors and re-logged-in transparently.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .config import load_config


_log = logging.getLogger(__name__)


# JS hook installed before any page script runs. Captures every JSON.parse
# result that looks like an API response. Result accessible at
# `window.__parsedObjects` (array, in chronological order).
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
                if (keys.length > 0) {
                    const looksApi = keys.includes('success')
                                  || keys.includes('errorcode')
                                  || keys.length >= 3;
                    if (looksApi && JSON.stringify(result).length < 80000) {
                        window.__parsedObjects.push({
                            keys: keys.slice(0, 25),
                            value: result,
                            ts: Date.now()
                        });
                    }
                }
            } else if (Array.isArray(result) && result.length > 0 && result.length < 500) {
                if (typeof result[0] === 'object' && result[0] !== null) {
                    window.__parsedObjects.push({
                        keys: ['(array)'],
                        value: result.slice(0, 100),
                        ts: Date.now()
                    });
                }
            }
        } catch(e) {}
        return result;
    };

    // Also capture every JSON.stringify of an object with `operation` key,
    // which is the request body the JS sends BEFORE encryption. This lets
    // us see write payloads.
    const origStringify = JSON.stringify;
    window.__stringifiedRequests = [];
    JSON.stringify = function(value, replacer, space) {
        const out = origStringify.call(this, value, replacer, space);
        try {
            if (value && typeof value === 'object' && 'operation' in value) {
                window.__stringifiedRequests.push({
                    body: value,
                    ts: Date.now(),
                    out_size: out ? out.length : 0
                });
            }
        } catch(e) {}
        return out;
    };
})();
"""


@dataclass
class PageReadResult:
    route: str
    objects: list[dict]
    request_paths: list[str]
    forms_seen: list[str]


class PlaywrightBackend:
    """Long-lived headless-browser session for the AX53 web UI."""

    def __init__(self) -> None:
        self._cfg = load_config()
        self._base = f"http://{self._cfg.host}"
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._logged_in = False

    # ────────────────────────────── lifecycle

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
            self._logged_in = False

    async def _login(self) -> None:
        assert self._page is not None
        page = self._page
        await page.goto(self._base, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        for sel in ['input[type="password"]:visible', "#pc-login-password"]:
            try:
                await page.locator(sel).first.fill(self._cfg.password, timeout=2000)
                break
            except Exception:
                continue
        for sel in ['button:has-text("LOG IN")', 'button:has-text("Log In")', "#pc-login-btn"]:
            try:
                await page.locator(sel).first.click(timeout=2000)
                break
            except Exception:
                continue
        try:
            await page.wait_for_function(
                "!window.location.hash.includes('#/login')", timeout=15000
            )
            await page.wait_for_load_state("networkidle", timeout=10000)
            self._logged_in = True
            _log.info("Playwright backend logged in")
        except Exception as e:
            self._logged_in = False
            raise RuntimeError(f"Login failed: {e}")

    async def _ensure_session(self) -> None:
        """Restart the browser session if it died or got logged out."""
        if not self._pw or not self._page or self._page.is_closed():
            await self.close()
            await self.start()
            return
        # Cheap liveness check: are we still on a non-login URL?
        try:
            url = self._page.url
            if "#/login" in url:
                await self._login()
        except Exception:
            await self.close()
            await self.start()

    # ────────────────────────────── primitives

    async def read_page(self, route: str, settle_ms: int = 2500) -> PageReadResult:
        """Navigate to `route` (a hash route like "/lan") and capture every
        decrypted JSON object loaded by that page.
        """
        async with self._lock:
            await self._ensure_session()
            page = self._page
            assert page is not None

            req_paths: list[str] = []
            forms_seen: list[str] = []

            def on_req(req) -> None:
                if "/cgi-bin/luci" not in req.url:
                    return
                from urllib.parse import urlparse, parse_qs
                u = urlparse(req.url)
                req_paths.append(u.path)
                qs = parse_qs(u.query)
                form = qs.get("form", [""])[0]
                if form and form not in forms_seen:
                    forms_seen.append(form)

            page.on("request", on_req)
            try:
                await page.evaluate("window.__parsedObjects = []")
                url = self._base + "/webpages/index.html#" + route
                await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                await page.wait_for_timeout(settle_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                captured = await page.evaluate("window.__parsedObjects || []")
            finally:
                page.remove_listener("request", on_req)

            return PageReadResult(
                route=route,
                objects=captured,
                request_paths=list(set(req_paths)),
                forms_seen=forms_seen,
            )

    async def evaluate(self, js_expr: str) -> Any:
        """Run JS in the page context. For advanced/escape-hatch use only."""
        async with self._lock:
            await self._ensure_session()
            assert self._page is not None
            return await self._page.evaluate(js_expr)

    async def click_save_and_capture(
        self, route: str, save_button_selectors: Iterable[str], wait_ms: int = 2500
    ) -> dict:
        """Navigate to `route`, click a Save button, return whatever objects
        were parsed during the save request/response cycle.

        DOES NOT touch any settings — caller must have already filled the form
        via `evaluate()` or `fill_field()` BEFORE calling this.
        """
        async with self._lock:
            await self._ensure_session()
            page = self._page
            assert page is not None
            url = self._base + "/webpages/index.html#" + route
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(1500)
            await page.evaluate("window.__parsedObjects = []")
            for sel in save_button_selectors:
                try:
                    await page.locator(sel).first.click(timeout=3000)
                    break
                except Exception:
                    continue
            await page.wait_for_timeout(wait_ms)
            captured = await page.evaluate("window.__parsedObjects || []")
            return {"route": route, "captured": captured}

    async def drive_form(
        self,
        route: str,
        actions: list[dict],
        save_selectors: Iterable[str] = (
            'button:has-text("Save")',
            'su-button:has-text("Save")',
            'button.btn-msg:has-text("Save")',
            'button[data-test="save"]',
        ),
        confirm_selectors: Iterable[str] = (
            'button:has-text("Yes")',
            'button:has-text("OK")',
            'button:has-text("Continue")',
        ),
        wait_load_ms: int = 2000,
        wait_save_ms: int = 3000,
    ) -> dict:
        """Generic Vue-form driver. Navigates, applies each action in order,
        clicks Save (and any confirm dialog), captures the success/failure
        response from the JSON.parse hook.

        actions: list of dicts. Supported types:
            {"type": "fill",    "selector": "...", "value": "..."}
            {"type": "check",   "selector": "...", "checked": True/False}
            {"type": "click",   "selector": "..."}
            {"type": "select",  "selector": "...", "option": "..."}
            {"type": "wait",    "ms": 500}
        """
        async with self._lock:
            await self._ensure_session()
            page = self._page
            assert page is not None
            url = self._base + "/webpages/index.html#" + route
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(wait_load_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass

            for action in actions:
                t = action.get("type", "")
                sel = action.get("selector", "")
                try:
                    if t == "fill":
                        await page.locator(sel).first.fill(str(action["value"]), timeout=4000)
                    elif t == "check":
                        loc = page.locator(sel).first
                        cur = await loc.is_checked()
                        if cur != bool(action["checked"]):
                            await loc.click(timeout=4000)
                    elif t == "click":
                        await page.locator(sel).first.click(timeout=4000)
                    elif t == "select":
                        await page.locator(sel).first.select_option(action["option"], timeout=4000)
                    elif t == "wait":
                        await page.wait_for_timeout(int(action.get("ms", 500)))
                except Exception as e:
                    return {"route": route, "ok": False, "error": f"{t} {sel}: {e}"}

            # Reset capture, click Save, wait for response
            await page.evaluate("window.__parsedObjects = []")
            saved = False
            for sel in save_selectors:
                try:
                    await page.locator(sel).first.click(timeout=3000)
                    saved = True
                    break
                except Exception:
                    continue
            if not saved:
                return {"route": route, "ok": False, "error": "could not find Save button"}

            # Optional confirm-dialog click
            await page.wait_for_timeout(500)
            for sel in confirm_selectors:
                try:
                    await page.locator(sel).first.click(timeout=1000)
                    break
                except Exception:
                    continue

            await page.wait_for_timeout(wait_save_ms)
            captured = await page.evaluate("window.__parsedObjects || []")
            # Find the Save response: success/failure dict
            success = None
            for obj in reversed(captured):
                v = obj.get("value", {})
                if isinstance(v, dict) and "success" in v:
                    success = v
                    break
            ok = bool(success and success.get("success"))
            return {
                "route": route,
                "ok": ok,
                "save_response": success,
                "captured_count": len(captured),
            }


# ────────────────────────────── module-level singleton

_backend: PlaywrightBackend | None = None
_backend_lock = asyncio.Lock()


async def get_backend() -> PlaywrightBackend:
    """Return the process-wide Playwright backend, starting it if needed."""
    global _backend
    async with _backend_lock:
        if _backend is None:
            _backend = PlaywrightBackend()
            await _backend.start()
    return _backend


async def close_backend() -> None:
    global _backend
    async with _backend_lock:
        if _backend is not None:
            await _backend.close()
            _backend = None
