import asyncio
import json
import os
import random

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import LINKEDIN_DAILY_LIMIT

LINKEDIN_COOKIES_JSON = os.getenv("LINKEDIN_COOKIES_JSON")
_daily_count = 0


def is_logged_in_url(url: str) -> bool:
    bad = ["/login", "/checkpoint", "/authwall"]
    return not any(b in url for b in bad)


def truncate_message(msg: str, limit: int = 300) -> str:
    if len(msg) <= limit:
        return msg
    truncated = msg[:limit].rsplit(" ", 1)[0]
    return truncated + "..."


async def send_message_async(data: dict) -> bool:
    global _daily_count

    if _daily_count >= LINKEDIN_DAILY_LIMIT:
        return False

    linkedin_url = data.get("linkedin_url")
    if not linkedin_url:
        return False

    cookies = json.loads(LINKEDIN_COOKIES_JSON)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        await page.goto("https://www.linkedin.com/feed/", timeout=15000)
        if not is_logged_in_url(page.url):
            raise RuntimeError("LinkedIn session expired — re-run save_linkedin_cookies.py")

        await page.goto(linkedin_url, timeout=15000)
        await page.wait_for_timeout(random.randint(2000, 4000))

        msg = truncate_message(data["linkedin_msg"], 300)

        msg_btn = await page.query_selector('button[aria-label*="Message"]')
        if msg_btn:
            await msg_btn.click()
            await page.wait_for_timeout(1500)
            text_area = await page.query_selector('.msg-form__contenteditable')
            if text_area:
                await text_area.fill(msg)
                send_btn = await page.query_selector('button.msg-form__send-button')
                if send_btn:
                    await send_btn.click()
                    _daily_count += 1
                    await browser.close()
                    return True

        connect_btn = await page.query_selector('button[aria-label*="Connect"]')
        if connect_btn:
            await connect_btn.click()
            await page.wait_for_timeout(1000)
            add_note_btn = await page.query_selector('button[aria-label="Add a note"]')
            if add_note_btn:
                await add_note_btn.click()
                await page.wait_for_timeout(800)
                note_area = await page.query_selector('#custom-message')
                if note_area:
                    await note_area.fill(msg)
                    send_btn = await page.query_selector('button[aria-label="Send invitation"]')
                    if send_btn:
                        await send_btn.click()
                        _daily_count += 1
                        await browser.close()
                        await asyncio.sleep(random.uniform(30, 90))
                        return True

        await browser.close()
        return False


def send(data: dict) -> bool:
    return asyncio.run(send_message_async(data))
