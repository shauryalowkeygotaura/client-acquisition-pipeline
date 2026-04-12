"""
modules/personalizer.py — Person-level personalization

Scrapes LinkedIn profiles and searches recent company news to extract
concrete, specific hooks for cold email openers.

Runs AFTER scorer (only for qualified leads) and BEFORE generator.
Silently degrades to empty hooks if any step fails — never blocks the pipeline.
"""
import json
import logging
import os

from firecrawl import FirecrawlApp
from openai import OpenAI
from serpapi import GoogleSearch

from config import LLM_BASE_URL, LLM_MODEL

log = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
LLM_API_KEY = os.getenv("GROQ_API_KEY")

_EMPTY_HOOKS = {"person_hook": "", "company_hook": ""}


def _scrape_linkedin_profile(linkedin_url: str) -> str:
    """Scrape LinkedIn profile via Firecrawl. Returns markdown text or empty string."""
    if not FIRECRAWL_API_KEY or not linkedin_url:
        return ""
    try:
        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        result = app.scrape(linkedin_url, formats=["markdown"])
        return (result.markdown or "")[:4000]
    except Exception as e:
        log.warning("LinkedIn profile scrape failed for %s: %s", linkedin_url, e)
        return ""


def _search_company_news(company_name: str, location: str) -> str:
    """Search recent news (last 3 months) about the company via SerpAPI Google News."""
    if not SERPAPI_KEY or not company_name:
        return ""
    try:
        search = GoogleSearch({
            "q": f'"{company_name}" {location}',
            "tbm": "nws",         # Google News tab
            "tbs": "qdr:m3",      # last 3 months
            "api_key": SERPAPI_KEY,
            "num": 5,
        })
        results = search.get_dict()
        items = results.get("news_results", [])[:3]
        snippets = [
            f"- {r.get('title', '')}: {r.get('snippet', '')}"
            for r in items
        ]
        return "\n".join(snippets)
    except Exception as e:
        log.warning("Company news search failed for %s: %s", company_name, e)
        return ""


def _extract_hooks(
    profile_text: str,
    news_text: str,
    company_name: str,
    poster_name: str,
) -> dict:
    """LLM call to distill 1-2 concrete personalization hooks from raw text."""
    if not LLM_API_KEY:
        return _EMPTY_HOOKS
    if not profile_text and not news_text:
        return _EMPTY_HOOKS

    prompt = f"""You are extracting personalization hooks for a cold outreach email to {company_name}.

PERSON — LinkedIn profile of {poster_name or 'the decision maker at this company'}:
{profile_text[:2000] if profile_text else 'Not available'}

RECENT COMPANY NEWS (last 3 months):
{news_text[:1000] if news_text else 'Not available'}

Extract exactly two hooks as a JSON object:

"person_hook": One specific, verifiable fact about this person from the profile above. Must make a cold email opener feel non-generic — e.g. a recent role change, a credential, a post they wrote, a specific background detail. Max 1 sentence. Empty string if nothing specific is available.

"company_hook": One specific, verifiable fact about this company from the news above — e.g. a recent opening, award, expansion, or challenge mentioned. Must come from the news. Max 1 sentence. Empty string if no useful news.

Rules:
- Only state facts clearly present in the provided text — never invent or infer
- If the text is too generic, return empty strings rather than padding with vague observations
- No filler words. Bare facts only.

Return ONLY valid JSON with exactly "person_hook" and "company_hook" keys.
"""

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,   # near-deterministic — we're extracting facts, not creating
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        return {
            "person_hook": str(parsed.get("person_hook", "")).strip(),
            "company_hook": str(parsed.get("company_hook", "")).strip(),
        }
    except Exception as e:
        log.warning("Hook extraction failed: %s", e)
        return _EMPTY_HOOKS


def run(data: dict) -> dict:
    """
    Enrich lead dict with person_hook and company_hook.
    Either hook may be an empty string — generator handles both cases.
    """
    linkedin_url = data.get("linkedin_url")
    company_name = data.get("company_name", "")
    poster_name = data.get("poster_name", "")
    location = data.get("location", "")

    profile_text = _scrape_linkedin_profile(linkedin_url) if linkedin_url else ""
    news_text = _search_company_news(company_name, location)

    hooks = _extract_hooks(profile_text, news_text, company_name, poster_name)

    if hooks.get("person_hook"):
        log.info("[PERSONALIZER] Person hook for %s: %s", company_name, hooks["person_hook"])
    if hooks.get("company_hook"):
        log.info("[PERSONALIZER] Company hook for %s: %s", company_name, hooks["company_hook"])

    return {**data, **hooks}
