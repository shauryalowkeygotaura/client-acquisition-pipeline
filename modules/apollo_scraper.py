"""
modules/apollo_scraper.py

Apollo.io lead extractor running on top of Obscura (stealth headless Chrome
in 35 MB). Replaces the paid Apollo API for free-tier discovery.

How it works:
  1. obscura_launcher.serve() boots an Obscura CDP server in the background
  2. We connect via Playwright's connect_over_cdp
  3. Restore Apollo session cookies from APOLLO_COOKIES_JSON (env or file)
  4. Navigate the People Search URL with filters baked in as query params
  5. Iterate result rows, extract name / title / company / linkedin
  6. Click the "Access email" button per row to reveal the verified email
  7. Yield one lead dict per row, shape compatible with the rest of the pipeline

Output schema (matches what scraper.py / researcher.py downstream expect):
    {
      "company_name":   str,
      "job_title":      str,                   # the contact's title (e.g. "Owner")
      "location":       str,
      "company_website": str | None,
      "poster_name":    str,                   # contact's full name
      "linkedin_url":   str | None,
      "email":          str | None,            # verified by Apollo (skips researcher fallback)
      "domain":         str | None,
      "source":         "apollo",
    }

Limits & caveats:
  - Apollo's free plan unlocks ~5 emails/day. Beyond that, "Access email"
    is greyed out → email field comes back None. Pipeline handles that case.
  - Scraping the UI is against Apollo TOS. Use a burner account, not your
    main one.
  - First run: must save cookies via scripts/save_apollo_cookies.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode, urlparse

from slugify import slugify

from . import obscura_launcher

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / "apollo_cookies.json"

PEOPLE_SEARCH_URL = "https://app.apollo.io/#/people"
DEFAULT_PER_PAGE = 25  # Apollo's UI page size
ROW_RENDER_DELAY_S = 1.5
EMAIL_REVEAL_DELAY_S = 1.0


def _parse_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    domain = (parsed.netloc or parsed.path).lower().removeprefix("www.")
    return domain or None


def _load_cookies() -> list[dict]:
    """Load cookies from APOLLO_COOKIES_JSON env first, then apollo_cookies.json file."""
    raw = os.getenv("APOLLO_COOKIES_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.error("APOLLO_COOKIES_JSON is not valid JSON")
    if COOKIE_FILE.is_file():
        try:
            return json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.error("apollo_cookies.json is not valid JSON")
    return []


def _build_search_url(
    *,
    titles: list[str],
    locations: list[str],
    employee_min: int = 1,
    employee_max: int = 50,
    page: int = 1,
) -> str:
    """Apollo URL fragment search. Apollo uses a hash router so query lives after #."""
    params: list[tuple[str, str]] = []
    for t in titles:
        params.append(("personTitles[]", t))
    for loc in locations:
        params.append(("personLocations[]", loc))
    params.append(("organizationNumEmployeesRanges[]", f"{employee_min},{employee_max}"))
    params.append(("page", str(page)))
    return f"{PEOPLE_SEARCH_URL}?{urlencode(params, doseq=True)}"


# Selectors are documented as regex/text where possible since Apollo's CSS
# class hashes change on every release. Most are role/text-based so they
# survive minor UI churn.
_ROW_SELECTOR = "tr[data-cy='person-row'], tr.zp_aBhrx"  # primary, then fallback class
_NAME_SELECTOR = "[data-cy='person-name'], a[href*='/people/']"
_TITLE_SELECTOR = "[data-cy='person-title']"
_COMPANY_NAME_SELECTOR = "[data-cy='org-name']"
_COMPANY_WEBSITE_SELECTOR = "a[data-cy='org-website']"
_LOCATION_SELECTOR = "[data-cy='person-location']"
_LINKEDIN_SELECTOR = "a[href*='linkedin.com/in/']"
_EMAIL_BUTTON_SELECTOR = "button:has-text('Access email')"
_EMAIL_VALUE_SELECTOR = "[data-cy='person-email'], a[href^='mailto:']"


def _safe_text(locator) -> str:
    try:
        return (locator.first.inner_text(timeout=800) or "").strip()
    except Exception:
        return ""


def _safe_attr(locator, attr: str) -> str | None:
    try:
        v = locator.first.get_attribute(attr, timeout=800)
        return v.strip() if v else None
    except Exception:
        return None


def _extract_row(row) -> dict:
    name = _safe_text(row.locator(_NAME_SELECTOR))
    title = _safe_text(row.locator(_TITLE_SELECTOR))
    company = _safe_text(row.locator(_COMPANY_NAME_SELECTOR))
    location = _safe_text(row.locator(_LOCATION_SELECTOR))
    website = _safe_attr(row.locator(_COMPANY_WEBSITE_SELECTOR), "href")
    linkedin = _safe_attr(row.locator(_LINKEDIN_SELECTOR), "href")

    email: str | None = None
    # If email is already revealed (Apollo caches reveals across sessions)
    pre_revealed = _safe_text(row.locator(_EMAIL_VALUE_SELECTOR))
    if pre_revealed and "@" in pre_revealed:
        email = pre_revealed
    else:
        try:
            btn = row.locator(_EMAIL_BUTTON_SELECTOR).first
            if btn.is_visible(timeout=400):
                btn.click(timeout=2000)
                time.sleep(EMAIL_REVEAL_DELAY_S)
                revealed = _safe_text(row.locator(_EMAIL_VALUE_SELECTOR))
                if revealed and "@" in revealed:
                    email = revealed
        except Exception as e:
            log.debug("Email reveal failed for %s: %s", name, e)

    if email:
        # Strip stray whitespace/zero-width chars Apollo sometimes ships in copy buttons
        email = re.sub(r"\s+", "", email)

    return {
        "company_name":   company,
        "job_title":      title,
        "location":       location,
        "company_website": website,
        "poster_name":    name,
        "linkedin_url":   linkedin if linkedin and "linkedin.com/in/" in linkedin else None,
        "email":          email if email and "@" in email else None,
        "domain":         _parse_domain(website),
        "source":         "apollo",
        "slug":           slugify(company) if company else "",
    }


def search(
    *,
    titles: list[str] | None = None,
    locations: list[str] | None = None,
    employee_min: int = 1,
    employee_max: int = 50,
    pages: int = 1,
    headless: bool = True,
) -> Iterator[dict]:
    """
    Yield Apollo lead dicts.

    `titles`/`locations` are case-insensitive substrings Apollo uses to filter.
    `pages` controls how many result pages to walk; Apollo serves 25/page by
    default, so pages=2 → up to 50 leads per call.
    """
    titles = titles or ["Owner", "Office Manager", "Practice Manager"]
    locations = locations or ["India"]

    cookies = _load_cookies()
    if not cookies:
        log.error("No Apollo cookies. Run: python scripts/save_apollo_cookies.py")
        return

    from playwright.sync_api import sync_playwright

    with obscura_launcher.serve(stealth=True) as ws:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            ctx.add_cookies(cookies)

            page = ctx.new_page()
            for page_num in range(1, pages + 1):
                url = _build_search_url(
                    titles=titles,
                    locations=locations,
                    employee_min=employee_min,
                    employee_max=employee_max,
                    page=page_num,
                )
                log.info("Apollo search page %d: %s", page_num, url)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                except Exception as e:
                    log.error("Apollo page load failed: %s", e)
                    continue

                # Wait for at least one row to render
                try:
                    page.wait_for_selector(_ROW_SELECTOR, timeout=12_000)
                except Exception:
                    log.warning("No rows rendered on page %d (login may have expired)", page_num)
                    if page_num == 1:
                        # First-page failure means cookies are dead
                        log.error("Apollo session is invalid. Re-run save_apollo_cookies.py")
                        break
                    continue
                time.sleep(ROW_RENDER_DELAY_S)

                rows = page.locator(_ROW_SELECTOR)
                count = rows.count()
                log.info("Page %d: %d rows", page_num, count)

                for i in range(count):
                    try:
                        lead = _extract_row(rows.nth(i))
                    except Exception as e:
                        log.warning("Row %d extract failed: %s", i, e)
                        continue
                    if not lead.get("company_name"):
                        continue
                    yield lead

            browser.close()


def run(city: str | None = None) -> list[dict]:
    """
    Convenience wrapper matching scraper.run(city)'s signature, so pipeline.py
    can drop in apollo_scraper as the lead source without other changes.
    """
    locations = [city] if city else None
    return list(search(locations=locations, pages=int(os.getenv("APOLLO_PAGES", "1"))))
