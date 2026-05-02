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


_log = logging.getLogger(__name__)


def get_router() -> AbstractRouter:
    """Return an authenticated router client. Caller is responsible for .logout()."""
    cfg = load_config()
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
