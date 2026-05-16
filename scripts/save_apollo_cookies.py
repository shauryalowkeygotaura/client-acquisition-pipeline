"""
scripts/save_apollo_cookies.py

One-time interactive login to Apollo.io. Opens a non-headless browser via
Obscura, lets the user log in (including any 2FA / captcha), then dumps
session cookies to apollo_cookies.json.

The pipeline reuses these cookies on every run so we never re-login from a
CI-style flow (which trips Apollo's bot detection).

Run: python scripts/save_apollo_cookies.py
Then: copy the JSON contents into the APOLLO_COOKIES_JSON GitHub secret if
you want CI to use the same session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running this file directly from /scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import obscura_launcher  # noqa: E402

COOKIE_FILE = ROOT / "apollo_cookies.json"
LOGIN_URL = "https://app.apollo.io/#/login"
DASHBOARD_HINT = "https://app.apollo.io/#/control-center"


def main() -> int:
    from playwright.sync_api import sync_playwright

    with obscura_launcher.serve(stealth=True) as ws:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()

            print("\n[1/3] Opening Apollo login page...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            print("[2/3] Log in manually in the browser window.")
            print("       Complete any 2FA / captcha if prompted.")
            print("       When you see your dashboard, come back here and press ENTER.")
            input("       Ready? press ENTER to dump cookies > ")

            cookies = ctx.cookies()
            apollo_cookies = [c for c in cookies if "apollo.io" in c.get("domain", "")]

            if not apollo_cookies:
                print("[FAIL] No apollo.io cookies found. Are you actually logged in?")
                browser.close()
                return 1

            COOKIE_FILE.write_text(json.dumps(apollo_cookies, indent=2), encoding="utf-8")
            print(f"[3/3] Saved {len(apollo_cookies)} cookies → {COOKIE_FILE}")
            print("       For CI: copy this file's contents into APOLLO_COOKIES_JSON secret.")
            browser.close()
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
