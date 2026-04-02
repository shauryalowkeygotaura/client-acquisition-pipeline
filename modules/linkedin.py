import asyncio
import json
import logging
import os
import random
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import LINKEDIN_DAILY_LIMIT

log = logging.getLogger(__name__)

LINKEDIN_COOKIES_JSON = os.getenv("LINKEDIN_COOKIES_JSON")

# Persist daily count to disk so restarts don't reset the limit
_COUNTER_FILE = Path(__file__).parent.parent / ".linkedin_daily_count"


def _load_count() -> int:
    try:
        if _COUNTER_FILE.exists():
            data = json.loads(_COUNTER_FILE.read_text())
            # Reset if it was saved on a different calendar day
            import datetime
            if data.get("date") == datetime.date.today().isoformat():
                return int(data.get("count", 0))
    except Exception:
        pass
    return 0


def _save_count(count: int) -> None:
    import datetime
    try:
        _COUNTER_FILE.write_text(json.dumps({
            "date": datetime.date.today().isoformat(),
            "count": count,
        }))
    except Exception as e:
        log.warning("Could not persist LinkedIn daily count: %s", e)


def is_logged_in_url(url: str) -> bool:
    bad = ["/login", "/checkpoint", "/authwall"]
    return not any(b in url for b in bad)


def truncate_message(msg: str, limit: int = 300) -> str:
    if len(msg) <= limit:
        return msg
    truncated = msg[:limit].rsplit(" ", 1)[0]
    return truncated + "..."


async def send_message_async(data: dict) -> bool:
    daily_count = _load_count()

    if daily_count >= LINKEDIN_DAILY_LIMIT:
        log.warning("LinkedIn daily limit (%d) reached — skipping.", LINKEDIN_DAILY_LIMIT)
        return False

    linkedin_url = data.get("linkedin_url")
    if not linkedin_url:
        return False

    if not LINKEDIN_COOKIES_JSON:
        log.error("LINKEDIN_COOKIES_JSON env var is not set.")
        return False

    try:
        cookies = json.loads(LINKEDIN_COOKIES_JSON)
    except json.JSONDecodeError as e:
        log.error("LINKEDIN_COOKIES_JSON is malformed JSON: %s", e)
        return False

    msg = truncate_message(data.get("linkedin_msg", ""), 300)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            await page.goto("https://www.linkedin.com/feed/", timeout=20000)
            if not is_logged_in_url(page.url):
                raise RuntimeError("LinkedIn session expired — re-run save_linkedin_cookies.py")

            await page.goto(linkedin_url, timeout=20000)
            await page.wait_for_timeout(random.randint(2000, 4000))

            # For cold outreach, Connect-with-note is the correct primary path.
            # Message button is only present if already connected — use it as secondary.
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
                            daily_count += 1
                            _save_count(daily_count)
                            log.info("LinkedIn connect+note sent to %s (%d/%d today)",
                                     linkedin_url, daily_count, LINKEDIN_DAILY_LIMIT)
                            await browser.close()
                            await asyncio.sleep(random.uniform(30, 90))
                            return True

            # Already connected — send direct message
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
                        daily_count += 1
                        _save_count(daily_count)
                        log.info("LinkedIn DM sent to %s (%d/%d today)",
                                 linkedin_url, daily_count, LINKEDIN_DAILY_LIMIT)
                        await browser.close()
                        await asyncio.sleep(random.uniform(30, 90))
                        return True

            log.warning("No Connect or Message button found on %s", linkedin_url)

        except Exception as e:
            log.error("LinkedIn automation error for %s: %s", linkedin_url, e)
        finally:
            await browser.close()

    return False


def send(data: dict) -> bool:
    return asyncio.run(send_message_async(data))
