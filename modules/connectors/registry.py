"""
modules/connectors/registry.py

Decides which platforms are live this run. Reads SOCIAL_PLATFORMS (comma
separated). If unset, defaults to just `console` so the agent runs free with
zero configuration. Any platform that is listed but missing credentials is
skipped with a notice — never a crash.

To add a new platform: write modules/connectors/<name>.py with a Connector
subclass, register it in _BUILDERS below, done.
"""
from __future__ import annotations

import os
from collections.abc import Callable

from .base import Connector
from .command_center import CommandCenterConnector
from .console import ConsoleConnector
from .discord import DiscordConnector
from .telegram import TelegramConnector

# name → factory. New connectors get one line here.
_BUILDERS: dict[str, Callable[[], Connector]] = {
    "console": ConsoleConnector,
    "command_center": CommandCenterConnector,
    "telegram": TelegramConnector,
    "discord": DiscordConnector,
}

# Default surface: read simulated inbound from console (free, offline) and
# publish posts to the Command Center dashboard. Telegram is available but not
# default — the Command Center is the primary surface now.
_DEFAULT_PLATFORMS = "console,command_center"


def enabled_platforms() -> list[str]:
    raw = os.getenv("SOCIAL_PLATFORMS", _DEFAULT_PLATFORMS)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def load_connectors(verbose: bool = True) -> list[Connector]:
    """Instantiate every requested connector that is actually usable."""
    live: list[Connector] = []
    for name in enabled_platforms():
        builder = _BUILDERS.get(name)
        if not builder:
            if verbose:
                print(f"    [registry] unknown platform '{name}' — skipping")
            continue
        conn = builder()
        if not conn.available():
            if verbose:
                print(f"    [registry] '{name}' configured but missing credentials — skipping")
            continue
        live.append(conn)
    if not live:
        # Never leave the agent with nothing to do.
        if verbose:
            print("    [registry] no live connectors — falling back to console")
        live.append(ConsoleConnector())
    if verbose:
        print(f"    [registry] live platforms: {', '.join(c.name for c in live)}")
    return live
