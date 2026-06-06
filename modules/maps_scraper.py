"""
modules/maps_scraper.py — Local-business lead source (Google Maps via SerpAPI).

Unlike scraper.py (which finds businesses by their *receptionist job posting*),
this finds EVERY clinic / school in a city straight from Google Maps. Every
listing comes with a phone number, address, rating and review count — exactly
the fields needed to (a) call/WhatsApp them and (b) write a lead-specific
icebreaker.

run(city) iterates config.MAPS_QUERIES and returns a flat list of lead dicts
shaped like scraper.run() output, so the rest of the pipeline (researcher,
enricher, scorer, icebreaker, sheets_writer) consumes them unchanged.
"""
import logging
import os
import time
from urllib.parse import urlparse

from serpapi import GoogleSearch
from slugify import slugify

from config import MAPS_QUERIES, MAPS_PAGES_PER_QUERY

log = logging.getLogger(__name__)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

# Aggregator / directory domains that are not the business's own site.
_DIRECTORY_DOMAINS = (
    "justdial.com", "sulekha.com", "practo.com", "facebook.com",
    "instagram.com", "indiamart.com", "google.com", "linkedin.com",
)


def parse_slug(name: str) -> str:
    cleaned = name.replace("'", "").replace("’", "")
    return slugify(cleaned)


def parse_domain(website: str | None) -> str | None:
    if not website:
        return None
    parsed = urlparse(website)
    domain = (parsed.netloc or parsed.path).lower().removeprefix("www.")
    domain = domain.split("/")[0]
    return domain or None


def _maps_url(item: dict) -> str:
    place_id = item.get("place_id")
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    return item.get("link", "") or ""


def _own_website(website: str | None) -> str | None:
    """Drop directory/aggregator links — keep only the business's own site."""
    if not website:
        return None
    if any(d in website.lower() for d in _DIRECTORY_DOMAINS):
        return None
    return website


def _to_lead(item: dict, city: str, niche: str) -> dict | None:
    name = (item.get("title") or "").strip()
    if not name:
        return None

    website = _own_website(item.get("website"))
    phone = (item.get("phone") or "").strip()
    biz_type = item.get("type") or (item.get("types") or [""])[0] or ""
    address = item.get("address") or ""
    hours = ""
    oh = item.get("operating_hours") or item.get("hours")
    if isinstance(oh, str):
        hours = oh

    # Synthetic description so the enricher's niche/maturity regexes have a corpus.
    desc = f"{name}. {biz_type}. {address}. {item.get('description', '')}".strip()

    return {
        "company_name": name,
        "job_title": "",
        "location": city,
        "company_website": website,
        "poster_name": None,
        "date_posted": "recent",
        "job_description_text": desc[:2000],
        "slug": parse_slug(name),
        "domain": parse_domain(website),
        # Maps-native fields
        "phone": phone,
        "maps_niche": niche,
        "business_type": biz_type,
        "address": address,
        "hours": hours,
        "rating": item.get("rating", ""),
        "review_count": item.get("reviews", ""),
        "maps_url": _maps_url(item),
    }


def _search(query: str, city: str, niche: str) -> list[dict]:
    leads: list[dict] = []
    for page in range(MAPS_PAGES_PER_QUERY):
        params = {
            "engine": "google_maps",
            "type": "search",
            "q": f"{query} in {city}",
            "hl": "en",
            "start": page * 20,
            "api_key": SERPAPI_KEY,
        }
        try:
            results = GoogleSearch(params).get_dict()
        except Exception as e:
            print(f"    [MAPS ERROR] '{query}' {city} p{page}: {e}")
            break

        if "error" in results:
            print(f"    [MAPS] '{query}' {city} p{page}: {results['error']}")
            break

        items = results.get("local_results")
        if items is None and results.get("place_results"):
            items = [results["place_results"]]  # single exact match
        items = items or []

        for it in items:
            lead = _to_lead(it, city, niche)
            if lead:
                leads.append(lead)

        if len(items) < 20:
            break  # last page
        time.sleep(1.0)  # be gentle on the API
    return leads


def run(city: str) -> list[dict]:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY env var is not set.")
    all_leads: list[dict] = []
    for query, niche in MAPS_QUERIES:
        found = _search(query, city, niche)
        print(f"    [MAPS] {city} | '{query}' -> {len(found)} listings")
        all_leads.extend(found)
    return all_leads
