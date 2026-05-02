"""Config + secret resolution.

Password resolution order:
    1. TPLINK_ARCHER_PASSWORD env var (for CI / non-Mac use)
    2. macOS Keychain entry  (service=tplink-archer-mcp, account=<host>)
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


KEYCHAIN_SERVICE = "tplink-archer-mcp"
DEFAULT_HOST = "192.168.0.1"
DEFAULT_USERNAME = "admin"


@dataclass(frozen=True)
class RouterConfig:
    host: str
    username: str
    password: str


def _keychain_get(account: str) -> str | None:
    """Read a password from macOS Keychain. Returns None if not found / not on Mac."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            pw = result.stdout.rstrip("\n")
            return pw or None
    except FileNotFoundError:
        # `security` only exists on macOS
        return None
    return None


def keychain_set(account: str, password: str) -> None:
    """Store/update a password in macOS Keychain. Raises if not on macOS."""
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s", KEYCHAIN_SERVICE,
            "-a", account,
            "-w", password,
            "-U",  # update if exists
        ],
        check=True,
    )


def load_config() -> RouterConfig:
    host = os.getenv("TPLINK_ARCHER_HOST", DEFAULT_HOST)
    username = os.getenv("TPLINK_ARCHER_USERNAME", DEFAULT_USERNAME)
    password = os.getenv("TPLINK_ARCHER_PASSWORD") or _keychain_get(host)
    if not password:
        raise RuntimeError(
            f"No password found. Either set TPLINK_ARCHER_PASSWORD or store one in Keychain:\n"
            f'  security add-generic-password -s "{KEYCHAIN_SERVICE}" -a "{host}" -w "<pw>" -U'
        )
    return RouterConfig(host=host, username=username, password=password)
