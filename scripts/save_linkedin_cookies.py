"""
Run locally once to capture LinkedIn session cookies.
Copy printed JSON into LINKEDIN_COOKIES_JSON GitHub Secret.
Re-run every 2-4 weeks when cookies expire.
"""
import asyncio
import json
from playwright.async_api import async_playwright


async def save_cookies():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/login")

        print("Log in to LinkedIn in the browser window.")
        print("Press ENTER here when fully logged in and on the feed...")
        input()

        cookies = await context.cookies()
        print("\nCopy this JSON and save as LINKEDIN_COOKIES_JSON GitHub Secret:\n")
        print(json.dumps(cookies, indent=2))

        await browser.close()


asyncio.run(save_cookies())
