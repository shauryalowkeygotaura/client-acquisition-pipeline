"""
Extracts LinkedIn cookies from your real Chrome browser.
No login needed — uses your existing Chrome session directly.

Steps:
1. Make sure LinkedIn is open and you're logged in in Chrome
2. Close all Chrome windows (Chrome must be fully closed)
3. Run: python scripts/save_linkedin_cookies.py
4. Copy the printed JSON → paste into .env as LINKEDIN_COOKIES_JSON (single line)
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME_PROFILE = Path(os.environ["USERPROFILE"]) / "AppData/Local/Google/Chrome/User Data"


def save_cookies():
    if not CHROME_PROFILE.exists():
        print(f"[ERROR] Chrome profile not found at: {CHROME_PROFILE}")
        print("Make sure Google Chrome is installed.")
        return

    print(f"\nUsing Chrome profile: {CHROME_PROFILE}")
    print("Make sure ALL Chrome windows are closed before continuing.")
    input("Press ENTER when Chrome is fully closed > ")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            channel="chrome",          # use real Chrome, not Playwright's Chromium
            headless=False,
            args=["--profile-directory=Default"],
        )
        page = context.new_page()
        page.goto("https://www.linkedin.com/feed/")
        page.wait_for_timeout(3000)

        if any(b in page.url for b in ["/login", "/checkpoint", "/authwall"]):
            print(f"\n[ERROR] Not logged in to LinkedIn in Chrome. URL: {page.url}")
            print("Log in to LinkedIn in Chrome first, then re-run this script.")
            context.close()
            return

        print(f"\nLogged in. Current URL: {page.url}")
        cookies = context.cookies("https://www.linkedin.com")
        context.close()

    if not cookies:
        print("\n[ERROR] No LinkedIn cookies found.")
        return

    single_line = json.dumps(cookies)
    print("\n" + "=" * 60)
    print("Copy the line below into your .env as LINKEDIN_COOKIES_JSON:")
    print("=" * 60)
    print(single_line)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    save_cookies()
