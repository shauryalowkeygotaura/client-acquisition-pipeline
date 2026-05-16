"""
modules/obscura_launcher.py

Spins up the Obscura headless browser as a CDP server in the background and
exposes it as a Python context manager. Playwright (or any CDP client) can
connect to it via ws://127.0.0.1:<port>.

Why Obscura instead of bundled Chromium:
  - ~30 MB RAM vs ~250 MB
  - Built-in stealth (canvas/audio/fingerprint randomization, tracker block)
  - Single 35 MB binary, no Chromium download
  - Drop-in CDP, so existing Playwright code works unchanged

Install (Windows): the binary is unzipped to %USERPROFILE%\.local\bin\obscura.exe.
The launcher resolves it via OBSCURA_BIN env, then PATH, then that fallback.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

DEFAULT_PORT = 9222
DEFAULT_HOST = "127.0.0.1"
_BOOT_TIMEOUT_S = 12  # generous — first boot on Windows can be slow


def _resolve_binary() -> str | None:
    """Find obscura.exe in OBSCURA_BIN, then PATH, then user-local fallback."""
    explicit = os.getenv("OBSCURA_BIN")
    if explicit and Path(explicit).is_file():
        return explicit

    on_path = shutil.which("obscura") or shutil.which("obscura.exe")
    if on_path:
        return on_path

    home = Path.home()
    fallback = home / ".local" / "bin" / ("obscura.exe" if sys.platform == "win32" else "obscura")
    if fallback.is_file():
        return str(fallback)
    return None


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_ready(host: str, port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(0.2)
    return False


@contextmanager
def serve(
    *,
    port: int = DEFAULT_PORT,
    stealth: bool = True,
    user_agent: str | None = None,
    proxy: str | None = None,
    workers: int = 1,
    log_path: str | None = None,
) -> Iterator[str]:
    """
    Start obscura as a CDP server, yield the websocket endpoint, kill on exit.

    Usage:
        with obscura_launcher.serve() as ws:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(ws)
                ...

    If a server is already listening on `port` (e.g. user ran `obscura serve`
    in another terminal), reuses it instead of starting a duplicate.
    """
    ws_endpoint = f"ws://{DEFAULT_HOST}:{port}"

    if _port_open(DEFAULT_HOST, port):
        log.info("Obscura already running on %d, reusing.", port)
        yield ws_endpoint
        return

    binary = _resolve_binary()
    if not binary:
        raise RuntimeError(
            "obscura binary not found. Install from "
            "https://github.com/h4ckf0r0day/obscura/releases or set OBSCURA_BIN."
        )

    cmd: list[str] = [binary, "serve", "--port", str(port), "--workers", str(workers)]
    if stealth:
        cmd.append("--stealth")
    if user_agent:
        cmd += ["--user-agent", user_agent]
    if proxy:
        cmd += ["--proxy", proxy]

    log_handle = open(log_path, "ab") if log_path else subprocess.DEVNULL
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW

    log.info("Booting obscura: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=log_handle,
        creationflags=creationflags,
    )

    try:
        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        if not _wait_for_ready(DEFAULT_HOST, port, deadline):
            raise RuntimeError(
                f"obscura serve did not open port {port} within {_BOOT_TIMEOUT_S}s"
            )
        yield ws_endpoint
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if hasattr(log_handle, "close") and log_handle is not subprocess.DEVNULL:
            log_handle.close()


def is_available() -> bool:
    """Cheap check used by callers to decide between Obscura and bundled Chromium."""
    return _resolve_binary() is not None
