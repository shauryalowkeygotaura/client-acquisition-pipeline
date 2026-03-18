import asyncio
import random
import re
import time
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from slugify import slugify

from config import INDEED_DELAY_MIN, INDEED_DELAY_MAX


def build_indeed_url(city: str) -> str:
    """Build Indeed search URL for urgent receptionist jobs in a city."""
    encoded_city = quote_plus(city)
    return (
        f"https://www.indeed.com/jobs"
        f"?q=receptionist"
        f"&l={encoded_city}"
        f"&sc=0kf%3Aattr(DSQF7)%3B"  # urgently hiring
        f"&fromage=1"                  # posted today
        f"&sort=date"
    )


def parse_slug(company_name: str) -> str:
    """Convert company name to URL-safe slug."""
    # Strip apostrophes before slugifying so "Smith's" → "smiths" not "smith-s"
    cleaned = company_name.replace("'", "").replace("\u2019", "")
    return slugify(cleaned)


def parse_domain(website: str | None) -> str | None:
    """Extract bare domain from a URL."""
    if not website:
        return None
    parsed = urlparse(website)
    domain = parsed.netloc or parsed.path
    domain = domain.lower().removeprefix("www.")
    return domain if domain else None


def extract_website_from_text(text: str) -> str | None:
    """Extract first URL from job description text."""
    pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    matches = re.findall(pattern, text)
    for match in matches:
        if not any(x in match for x in ["indeed.com", "google.com", "facebook.com"]):
            return match
    return None


async def scrape_city(city: str) -> list[dict]:
    """Scrape Indeed for urgent receptionist jobs in one city."""
    jobs = []
    url = build_indeed_url(city)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(random.randint(2000, 4000))

            job_cards = await page.query_selector_all('[data-jk]')

            for card in job_cards:
                try:
                    company_el = await card.query_selector('[data-testid="company-name"]')
                    title_el = await card.query_selector('[data-testid="jobTitle"]')
                    location_el = await card.query_selector('[data-testid="text-location"]')

                    company_name = await company_el.inner_text() if company_el else None
                    job_title = await title_el.inner_text() if title_el else None
                    location = await location_el.inner_text() if location_el else city

                    if not company_name:
                        continue

                    await card.click()
                    await page.wait_for_timeout(random.randint(1500, 3000))

                    desc_el = await page.query_selector('#jobDescriptionText')
                    desc_text = await desc_el.inner_text() if desc_el else ""

                    website = extract_website_from_text(desc_text)

                    job = {
                        "company_name": company_name.strip(),
                        "job_title": job_title.strip() if job_title else "Receptionist",
                        "location": location.strip(),
                        "company_website": website,
                        "poster_name": None,
                        "date_posted": "today",
                        "job_description_text": desc_text[:2000],
                        "slug": parse_slug(company_name),
                        "domain": parse_domain(website),
                    }
                    jobs.append(job)

                    delay = random.uniform(INDEED_DELAY_MIN, INDEED_DELAY_MAX)
                    time.sleep(delay)

                except Exception:
                    continue

        finally:
            await browser.close()

    return jobs


def run(city: str) -> list[dict]:
    """Sync wrapper for async scrape."""
    return asyncio.run(scrape_city(city))
