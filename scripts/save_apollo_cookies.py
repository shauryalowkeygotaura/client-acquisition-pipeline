"""
scripts/save_apollo_cookies.py

One-time interactive login to Apollo.io. Opens a REAL, VISIBLE browser window,
lets you log in (including any 2FA / captcha), then dumps session cookies to
apollo_cookies.json.

Why a visible Playwright Chromium and NOT Obscura: Obscura is headless (no GUI),
so you cannot manually log in through it — there is no window to see. Obscura is
still used for the automated pipeline scraping (headless), reusing the cookies
this script saves. Cookies are portable across browsers (same apollo.io session),
so a headful login here works for the headless scraper later.

Run: python scripts/save_apollo_cookies.py
Then: copy the JSON contents into the APOLLO_COOKIES_JSON GitHub secret (or send
the file to Claude) if you want CI to use the same session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running this file directly from /scripts/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COOKIE_FILE = ROOT / "apollo_cookies.json"
LOGIN_URL = "https://app.apollo.io/#/login"
DASHBOARD_HINT = "https://app.apollo.io/#/control-center"


def _launch_headful(p):
    """A visible browser to log in through. Prefer the user's real Google Chrome
    (least bot-like for Apollo's login), fall back to bundled Chromium."""
    try:
        return p.chromium.launch(headless=False, channel="chrome")
    except Exception as e:
        print(f"       (real Chrome unavailable [{type(e).__name__}], using bundled Chromium)")
        return p.chromium.launch(headless=False)  # raises loudly if this also fails


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_headful(p)
        ctx = browser.new_context()
        page = ctx.new_page()

        print("\n[1/3] Opening Apollo login in a VISIBLE browser window...")
        print("       (if no window appears, the browser is behind other windows — alt-tab)")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print("[2/3] Log in manually in that window.")
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
        print("       For CI: copy this file's contents into APOLLO_COOKIES_JSON secret,")
        print("       or just send apollo_cookies.json to Claude and I'll wire it.")
        browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
