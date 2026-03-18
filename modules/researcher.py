import os
import re

from firecrawl import FirecrawlApp
from serpapi import GoogleSearch  # pip: google-search-results

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")


def extract_email_from_text(text: str) -> list[str]:
    """Extract valid contact emails from plain text."""
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    found = re.findall(pattern, text)
    blocked = {"noreply", "no-reply", "donotreply", "support", "automated"}
    return [e for e in found if e.split("@")[0].lower() not in blocked]


def extract_emails_from_html(html: str) -> list[str]:
    """Extract emails from HTML including mailto: links."""
    mailto = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html)
    plain = extract_email_from_text(html)
    return list(set(mailto + plain))


def find_linkedin_url(poster_name: str | None, company_name: str) -> str | None:
    """Search for LinkedIn profile URL via SerpAPI."""
    if not SERPAPI_KEY or not poster_name:
        return None
    try:
        search = GoogleSearch({
            "q": f'site:linkedin.com/in/ "{poster_name}" "{company_name}"',
            "api_key": SERPAPI_KEY,
            "num": 1,
        })
        results = search.get_dict()
        organic = results.get("organic_results", [])
        if organic:
            link = organic[0].get("link", "")
            if "linkedin.com/in/" in link:
                return link
    except Exception:
        pass
    return None


def extract_structured_fields(markdown: str) -> dict:
    """
    Extract address, phone, services, hours from scraped markdown.
    Uses regex heuristics — best-effort, empty string if not found.
    """
    fields = {"address": "", "phone": "", "services": "", "hours": ""}

    phone_match = re.search(
        r'(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}', markdown
    )
    if phone_match:
        fields["phone"] = phone_match.group(0).strip()

    hours_match = re.search(
        r'(mon|tue|wed|thu|fri|sat|sun).{0,60}(am|pm)',
        markdown, re.IGNORECASE
    )
    if hours_match:
        fields["hours"] = hours_match.group(0).strip()

    services_match = re.search(
        r'(?:services?|we offer|specializ)[:\s]+([^\n]{10,200})',
        markdown, re.IGNORECASE
    )
    if services_match:
        fields["services"] = services_match.group(1).strip()

    address_match = re.search(
        r'\d{1,5}\s+[A-Z][a-z]+\s+(?:St|Ave|Blvd|Dr|Rd|Ln|Way|Place|Court)[.,\s]',
        markdown
    )
    if address_match:
        fields["address"] = address_match.group(0).strip()

    return fields


def scrape_company(job: dict) -> dict:
    """
    Enrich a job dict with company research.
    Scrapes homepage via Firecrawl, extracts email + linkedin_url + structured fields.
    Falls back to /contact page if no email on homepage.
    Falls back to job_description_text if no website.
    """
    website = job.get("company_website")
    desc_text = job.get("job_description_text", "")
    company_name = job["company_name"]
    poster_name = job.get("poster_name")

    research = {
        "address": "",
        "phone": "",
        "services": "",
        "hours": "",
        "email": None,
        "linkedin_url": None,
        "scraped_details": "",
        "tone": "professional",
    }

    if website and FIRECRAWL_API_KEY:
        try:
            app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
            # firecrawl-py v0.0.8+ uses keyword args, not params={} wrapper
            result = app.scrape_url(website, formats=["markdown", "html"])
            markdown = result.get("markdown", "")
            html = result.get("html", "")

            research["scraped_details"] = markdown[:3000]
            structured = extract_structured_fields(markdown)
            research.update(structured)

            emails = extract_emails_from_html(html) or extract_email_from_text(markdown)

            # /contact fallback if no email on homepage
            if not emails:
                contact_url = website.rstrip("/") + "/contact"
                try:
                    contact_result = app.scrape_url(contact_url, formats=["markdown", "html"])
                    contact_html = contact_result.get("html", "")
                    contact_md = contact_result.get("markdown", "")
                    emails = (extract_emails_from_html(contact_html)
                              or extract_email_from_text(contact_md))
                except Exception:
                    pass

            if emails:
                research["email"] = emails[0]
            elif desc_text:
                emails = extract_email_from_text(desc_text)
                if emails:
                    research["email"] = emails[0]

        except Exception:
            research["scraped_details"] = desc_text[:2000]
    else:
        research["scraped_details"] = desc_text[:2000]

    research["linkedin_url"] = find_linkedin_url(poster_name, company_name)

    return {**job, **research}


def run(job: dict) -> dict:
    return scrape_company(job)
