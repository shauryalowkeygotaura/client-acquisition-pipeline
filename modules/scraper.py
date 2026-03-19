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
        "q": "receptionist",
        "l": city,
        "from_age": "7",  # Broadened to last 7 days
        "limit": "30",
        "api_key": os.environ["SERPAPI_KEY"],
    }

    search = GoogleSearch(params)
    results = search.get_dict()
    raw_jobs = results.get("jobs_results", [])
    print(f"    [DEBUG] Indeed returned {len(raw_jobs)} raw jobs for {city}")

    jobs = []
    for j in raw_jobs:
        company_name = (j.get("company_name") or "").strip()
        if not company_name:
            continue

        # Look for 'urgently hiring' or similar in extensions/description
        extensions = j.get("extensions", [])
        is_urgent = any("urgent" in e.lower() for e in extensions)
        
        description = j.get("description", "")
        if not is_urgent and "urgent" not in description.lower():
            continue

        website = extract_website_from_text(description)

        jobs.append({
            "company_name": company_name,
            "job_title": j.get("title", "Receptionist").strip(),
            "location": j.get("location", city).strip(),
            "company_website": website,
            "poster_name": None,
            "date_posted": "recent",
            "job_description_text": description[:2000],
            "slug": parse_slug(company_name) or slugify(company_name),
            "domain": parse_domain(website),
        })

    return jobs


def run(city: str) -> list[dict]:
    return search_city(city)
