"""Authenticated client wrapper around tplinkrouterc6u.

Provides:
    get_router()  -> AbstractRouter   (auto-detects model, opens session)
    router_session() context manager  (open/close cleanly)

Uses TplinkRouterProvider, which iterates the supported client classes and
returns the first one whose .supports() check passes against the actual
router. For Archer AX53 this normally resolves to TplinkRouter or
TplinkRouterV1_11 depending on firmware.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from tplinkrouterc6u import TplinkRouterProvider
from tplinkrouterc6u.client_abstract import AbstractRouter

from .config import load_config
from .legacy_client import build_legacy_client, is_legacy_router


_log = logging.getLogger(__name__)

# Cache the legacy-detection probe per host so we don't re-probe on every tool call.
_LEGACY_CACHE: dict[str, bool] = {}


def _is_legacy(host: str) -> bool:
    if host not in _LEGACY_CACHE:
        _LEGACY_CACHE[host] = is_legacy_router(host)
    return _LEGACY_CACHE[host]


def get_router() -> AbstractRouter:
    """Return an authenticated router client. Caller is responsible for .logout()."""
    cfg = load_config()

    # Older Archer firmware (e.g. Archer C5 v4) speaks the legacy /cgi_gdpr
    # protocol and is not handled by TplinkRouterProvider's auto-detection.
    # Probe for the getGDPRParm key page and use the patched MR client when found.
    if _is_legacy(cfg.host):
        _log.info("Detected legacy /cgi_gdpr router at %s; using LegacyRouterClient", cfg.host)
        return build_legacy_client(
            host=cfg.host,
            password=cfg.password,
            username=cfg.username,
            logger=_log,
            timeout=20,
        )

    router = TplinkRouterProvider.get_client(
        host=f"http://{cfg.host}",
        password=cfg.password,
        username=cfg.username,
        logger=_log,
        verify_ssl=False,
        timeout=15,
    )
    router.authorize()
    return router


@contextmanager
def router_session() -> Iterator[AbstractRouter]:
    """Context manager that opens and closes a router session cleanly."""
    router = get_router()
    try:
        yield router
    finally:
        try:
            router.logout()
        except Exception:  # noqa: BLE001
            pass
