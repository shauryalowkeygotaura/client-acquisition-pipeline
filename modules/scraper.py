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
    """Query SerpAPI Indeed engine for urgent receptionist jobs in one city."""
    params = {
        "engine": "indeed",
        "q": "receptionist urgently hiring",
        "l": city,
        "sort": "date",
        "from_age": "3",
        "limit": "20",
        "api_key": os.environ["SERPAPI_KEY"],
    }

    search = GoogleSearch(params)
    results = search.get_dict()
    raw_jobs = results.get("jobs_results", [])
    print(f"    [DEBUG] SerpAPI keys: {list(results.keys())}")
    print(f"    [DEBUG] error: {results.get('error')}")
    print(f"    [DEBUG] raw jobs: {len(raw_jobs)} for {city}")
    for j in raw_jobs[:3]:
        print(f"    [DEBUG] sample: {j.get('company_name')} | extensions: {j.get('extensions', [])}")

    jobs = []
    for j in raw_jobs:
        extensions = j.get("extensions", [])
        # Only keep urgently hiring
        if not any("urgent" in e.lower() for e in extensions):
            continue

        description = j.get("description", "")
        website = extract_website_from_text(description)
        company_name = j.get("company_name", "").strip()

        if not company_name:
            continue

        jobs.append({
            "company_name": company_name,
            "job_title": j.get("title", "Receptionist").strip(),
            "location": j.get("location", city).strip(),
            "company_website": website,
            "poster_name": None,
            "date_posted": "today",
            "job_description_text": description[:2000],
            "slug": parse_slug(company_name),
            "domain": parse_domain(website),
        })

    return jobs


def run(city: str) -> list[dict]:
    return search_city(city)
