import os
import re
from urllib.parse import urlparse

from serpapi import GoogleSearch
from slugify import slugify


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
        "api_key": os.environ["SERPAPI_KEY"],
    }

    raw_jobs = []
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        # Robust key detection
        if "jobs_results" in results:
            raw_jobs = results["jobs_results"]
        elif "organic_results" in results:
            raw_jobs = results["organic_results"]
        elif "results" in results:
            raw_jobs = results["results"]
            
        print(f"    [DEBUG] Indeed response keys: {list(results.keys())}")
        print(f"    [DEBUG] Indeed returned {len(raw_jobs)} raw jobs for {city}")
    except Exception as e:
        print(f"    [ERROR] Indeed search failed: {e}")

    # 2. Fallback to Google Jobs if Indeed is empty
    if not raw_jobs:
        print(f"    [DEBUG] Indeed yielded 0. Trying Google Jobs for {city}...")
        g_params = {
            "engine": "google_jobs",
            "q": f"receptionist jobs in {city}",
            "gl": "in",
            "hl": "en",
            "api_key": os.environ["SERPAPI_KEY"],
        }
        try:
            g_search = GoogleSearch(g_params)
            g_results = g_search.get_dict()
            raw_jobs = g_results.get("jobs_results", [])
            print(f"    [DEBUG] Google Jobs fallback returned {len(raw_jobs)} raw jobs for {city}")
        except Exception as e:
            print(f"    [ERROR] Google Jobs fallback failed: {e}")

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
    return jobs


def run(city: str) -> list[dict]:
    return search_city(city)
