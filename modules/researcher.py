import logging
import os
import re
import socket
from urllib.parse import urlparse

import dns.resolver
from firecrawl import FirecrawlApp
from serpapi import GoogleSearch  # pip: google-search-results

log = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")


def parse_domain(website: str) -> str | None:
    """Extract bare domain from a URL (duplicated from scraper to avoid circular import)."""
    if not website:
        return None
    parsed = urlparse(website)
    domain = parsed.netloc or parsed.path
    domain = domain.lower().removeprefix("www.")
    return domain if domain else None


# Domains where guessing info@/contact@ makes no sense
_NO_GUESS_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com",
    "rediffmail.com", "icloud.com", "protonmail.com",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
    "indeed.com", "naukri.com", "glassdoor.com", "justdial.com",
    "sulekha.com", "indiamart.com", "tradeindia.com",
}


SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# Known fake/artifact TLDs that appear in scraped HTML (MHTML artifacts, etc.)
_FAKE_TLDS = {"blikc", "local", "invalid", "test", "example", "localhost", "internal"}

# Real TLDs are 2–6 alpha chars; anything longer is almost certainly garbage
_TLD_RE = re.compile(r'^[a-zA-Z]{2,6}$')

# Cache MX results to avoid repeated DNS calls for the same domain
_mx_cache: dict[str, bool] = {}


def _is_valid_email_domain(email: str) -> bool:
    """Return True only if the domain has real MX records (can actually receive mail)."""
    try:
        domain = email.split("@")[1].lower()
    except IndexError:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if tld in _FAKE_TLDS or not _TLD_RE.match(tld):
        return False
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        dns.resolver.resolve(domain, "MX", lifetime=4)
        _mx_cache[domain] = True
        return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers,
            dns.exception.Timeout):
        _mx_cache[domain] = False
        return False
    except Exception:
        # Unexpected DNS error — fall back to A-record check
        try:
            socket.setdefaulttimeout(3)
            socket.getaddrinfo(domain, None)
            _mx_cache[domain] = True
            return True
        except (socket.gaierror, OSError):
            _mx_cache[domain] = False
            return False


def extract_email_from_text(text: str) -> list[str]:
    """Extract valid contact emails from plain text."""
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    found = re.findall(pattern, text)
    blocked = {"noreply", "no-reply", "donotreply", "support", "automated"}
    candidates = [e for e in found if e.split("@")[0].lower() not in blocked]
    return [e for e in candidates if _is_valid_email_domain(e)]


def extract_emails_from_html(html: str) -> list[str]:
    """Extract emails from HTML via mailto: links only.

    Running the plain-text regex on raw HTML pulls in CSS/JS artifacts.
    mailto: links are what site owners deliberately placed there.
    """
    mailto = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html)
    blocked = {"noreply", "no-reply", "donotreply", "support", "automated"}
    candidates = [e for e in mailto if e.split("@")[0].lower() not in blocked]
    return [e for e in candidates if _is_valid_email_domain(e)]


def find_company_website(company_name: str, location: str = "") -> str | None:
    """Google search for the company's official website."""
    if not SERPAPI_KEY or not company_name or company_name.lower() == "confidential":
        return None
    try:
        query = f'"{company_name}" official website'
        if location:
            query += f" {location}"
        search = GoogleSearch({
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 3,
        })
        results = search.get_dict()
        blocked = ["indeed.com", "linkedin.com", "facebook.com", "naukri.com",
                   "glassdoor.com", "justdial.com", "sulekha.com", "twitter.com"]
        for r in results.get("organic_results", []):
            link = r.get("link", "")
            if link and not any(b in link for b in blocked):
                return link
    except Exception as e:
        log.warning("Company website search failed for %s: %s", company_name, e)
    return None


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
    except Exception as e:
        log.warning("LinkedIn URL search failed for %s @ %s: %s", poster_name, company_name, e)
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
    location = job.get("location", "")

    # If scraper couldn't find a website, Google it
    if not website:
        website = find_company_website(company_name, location)
        if website:
            log.info("Found website via search for %s: %s", company_name, website)

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
            result = app.scrape(website, formats=["markdown", "html"])
            markdown = result.markdown or ""
            html = result.html or ""

            research["scraped_details"] = markdown[:3000]
            structured = extract_structured_fields(markdown)
            research.update(structured)

            emails = extract_emails_from_html(html) or extract_email_from_text(markdown)

            # /contact fallback if no email on homepage
            if not emails:
                contact_url = website.rstrip("/") + "/contact"
                try:
                    contact_result = app.scrape(contact_url, formats=["markdown", "html"])
                    contact_html = contact_result.html or ""
                    contact_md = contact_result.markdown or ""
                    emails = (extract_emails_from_html(contact_html)
                              or extract_email_from_text(contact_md))
                except Exception as e:
                    log.warning("Contact page scrape failed for %s: %s", contact_url, e)

            if emails:
                research["email"] = emails[0]
            elif desc_text:
                emails = extract_email_from_text(desc_text)
                if emails:
                    research["email"] = emails[0]

        except Exception as e:
            log.error("Firecrawl failed for %s (%s): %s", company_name, website, e)
            research["scraped_details"] = desc_text[:2000]
    else:
        research["scraped_details"] = desc_text[:2000]

    research["linkedin_url"] = find_linkedin_url(poster_name, company_name)

    # ── Email fallback: guess common patterns if nothing found ───────────────
    # Most Indian SMB websites don't publish emails but ~35% respond to info@/contact@
    # Skip if domain is a free provider or social/job platform — would send to wrong inbox
    if not research.get("email"):
        domain = parse_domain(website) if website else None
        if domain and domain not in _NO_GUESS_DOMAINS:
            for prefix in ("info", "contact", "hello", "reception", "admin"):
                guessed = f"{prefix}@{domain}"
                if _is_valid_email_domain(guessed):
                    research["email"] = guessed
                    log.info("Guessed email for %s: %s", company_name, guessed)
                    break

    return {**job, **research}


def run(job: dict) -> dict:
    return scrape_company(job)
