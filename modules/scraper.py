import logging
import os
import re
from urllib.parse import urlparse

from serpapi import GoogleSearch
from slugify import slugify

log = logging.getLogger(__name__)

SERPAPI_KEY = os.environ.get("SERPAPI_KEY")


def parse_slug(company_name: str) -> str:
    """Convert company name to URL-safe slug."""
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
    """Extract first non-platform URL from text."""
    pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    matches = re.findall(pattern, text)
    for match in matches:
        if not any(x in match for x in ["indeed.com", "google.com", "facebook.com"]):
            return match
    return None


def search_city(city: str) -> list[dict]:
    """Query SerpAPI Indeed engine (with co=in) for receptionist jobs. Robust key fallback."""
    # 1. Try Indeed
    params = {
        "engine": "indeed",
        "q": "receptionist",
        "l": city,
        "co": "in", # Target Indeed India specifically
        "from_age": "14", # Increase to last 14 days
        "limit": "30",
        "api_key": SERPAPI_KEY,
    }

    raw_jobs = []
    indeed_keys: list[str] = []
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        indeed_keys = list(results.keys())

        if "error" in results:
            print(f"    [SCRAPER ERROR] SerpAPI Indeed returned error: {results['error']}")

        if "jobs_results" in results:
            raw_jobs = results["jobs_results"]
        elif "organic_results" in results:
            raw_jobs = results["organic_results"]
        elif "results" in results:
            raw_jobs = results["results"]
    except Exception as e:
        print(f"    [SCRAPER ERROR] Indeed call raised: {e}")

    # Fallback chain: Google Jobs → Google web search
    if not raw_jobs:
        print(f"    [SCRAPER] Indeed empty for {city}. Response keys: {indeed_keys}. Trying Google Jobs.")
        g_params = {
            "engine": "google_jobs",
            "q": f"receptionist jobs in {city}",
            "gl": "in",
            "hl": "en",
            "api_key": SERPAPI_KEY,
        }
        try:
            g_results = GoogleSearch(g_params).get_dict()
            g_keys = list(g_results.keys())
            if "error" in g_results:
                print(f"    [SCRAPER ERROR] google_jobs returned error: {g_results['error']}")
            raw_jobs = g_results.get("jobs_results", [])
            if not raw_jobs:
                print(f"    [SCRAPER] google_jobs empty for {city}. Response keys: {g_keys}.")
        except Exception as e:
            print(f"    [SCRAPER ERROR] google_jobs call raised: {e}")

    # Final fallback: plain Google search → mine organic results for hiring pages
    if not raw_jobs:
        print(f"    [SCRAPER] Trying plain Google search fallback for {city}.")
        try:
            web_results = GoogleSearch({
                "engine": "google",
                "q": f'"receptionist" ("hiring" OR "we are hiring" OR "join our team") {city}',
                "gl": "in",
                "hl": "en",
                "num": 20,
                "api_key": SERPAPI_KEY,
            }).get_dict()
            if "error" in web_results:
                print(f"    [SCRAPER ERROR] google web returned error: {web_results['error']}")
            organic = web_results.get("organic_results", [])
            print(f"    [SCRAPER] google web returned {len(organic)} organic results for {city}.")
            for r in organic:
                raw_jobs.append({
                    "company_name": (r.get("source") or r.get("displayed_link") or "").split(" › ")[0].strip(),
                    "title": r.get("title", "Receptionist"),
                    "snippet": r.get("snippet", ""),
                    "link": r.get("link", ""),
                })
        except Exception as e:
            print(f"    [SCRAPER ERROR] google web fallback raised: {e}")

    jobs = []
    for j in raw_jobs:
        company_name = (j.get("company_name") or j.get("company") or "").strip()
        if not company_name:
            continue

        description = j.get("description") or j.get("snippet") or ""
        website = extract_website_from_text(description)

        jobs.append({
            "company_name": company_name,
            "job_title": (j.get("title") or j.get("job_title") or "Receptionist").strip(),
            "location": (j.get("location") or city).strip(),
            "company_website": website,
            "poster_name": None,
            "date_posted": "recent",
            "job_description_text": description[:2000],
            "slug": parse_slug(company_name),
            "domain": parse_domain(website),
        })

    return jobs


def run(city: str) -> list[dict]:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY env var is not set.")
    return search_city(city)
