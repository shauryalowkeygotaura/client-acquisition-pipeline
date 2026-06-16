"""
modules/osm_scraper.py — Free, keyless local-business lead source (OpenStreetMap).

Why this exists: scraper.py and maps_scraper.py both run on SerpAPI, whose free
plan is 250 searches/month. When that wall is hit the whole funnel goes to zero.
This module queries the Overpass API (OpenStreetMap) instead:

  - no API key, no cookies, no browser, no monthly quota
  - structured data: name, phone, website, and sometimes a real email tag
  - deterministic and anti-bot-free (it's a public read API, not a scrape)

Trade-off: OSM contact-data coverage in India is patchier than Google Maps, so
expect a reliable trickle (names always; phone/website/email on a minority)
rather than a firehose. Leads with a website feed email_finder (Hunter/Snov,
which are domain-based and do NOT touch SerpAPI); leads with only a phone still
feed the WhatsApp/phone channels.

run(city) returns a flat list of lead dicts shaped exactly like
maps_scraper.run() output, so researcher / enricher / scorer / icebreaker /
sheets_writer consume them unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from slugify import slugify

log = logging.getLogger(__name__)

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
_USER_AGENT = "client-acquisition-pipeline/1.0 (lead research; contact via website)"
_TIMEOUT_S = int(os.getenv("OVERPASS_TIMEOUT_S", "60"))
_SLEEP_BETWEEN_QUERIES_S = float(os.getenv("OVERPASS_SLEEP_S", "1.5"))
_MAX_PER_NICHE = int(os.getenv("OSM_MAX_PER_NICHE", "80"))

# Aggregator / platform domains that are not the business's own site.
_DIRECTORY_DOMAINS = (
    "justdial.com", "sulekha.com", "practo.com", "facebook.com",
    "instagram.com", "indiamart.com", "google.com", "linkedin.com",
    "wa.me", "whatsapp.com", "maps.app.goo.gl",
)

# (niche, [Overpass element filters]). Each filter list is unioned into one
# query per niche per city; every result is tagged with the niche so the
# enricher/icebreaker know what they're looking at.
OSM_NICHE_FILTERS: list[tuple[str, list[str]]] = [
    ("dental", [
        'nwr["amenity"="dentist"](area.a);',
        'nwr["healthcare"="dentist"](area.a);',
    ]),
    ("medical", [
        'nwr["amenity"="clinic"](area.a);',
        'nwr["amenity"="doctors"](area.a);',
        'nwr["healthcare"="clinic"](area.a);',
    ]),
    ("physio", [
        'nwr["healthcare"="physiotherapist"](area.a);',
        'nwr["healthcare:speciality"="physiotherapy"](area.a);',
    ]),
    ("optometry", [
        'nwr["shop"="optician"](area.a);',
        'nwr["healthcare"="optometrist"](area.a);',
    ]),
    ("school", [
        'nwr["amenity"="school"](area.a);',
        'nwr["amenity"="college"](area.a);',
        'nwr["amenity"="kindergarten"](area.a);',
        'nwr["amenity"="language_school"](area.a);',
    ]),
]


def parse_slug(name: str) -> str:
    cleaned = name.replace("'", "").replace("’", "")
    return slugify(cleaned)


def parse_domain(website: str | None) -> str | None:
    if not website:
        return None
    parsed = urlparse(website if "://" in website else f"https://{website}")
    domain = (parsed.netloc or parsed.path).lower().removeprefix("www.")
    domain = domain.split("/")[0]
    return domain or None


def _area_name(city: str) -> str:
    """'Jaipur, Rajasthan' -> 'Jaipur'. Overpass matches the admin boundary name.

    Whitelist-sanitized to a safe charset so a stray quote/bracket/newline in a
    city string can never break out of the quoted area filter (Overpass QL
    injection guard). city originates from config.CITIES today, but this keeps
    the query well-formed if it ever comes from anywhere less trusted.
    """
    raw = city.split(",")[0].strip()
    safe = re.sub(r"[^A-Za-z0-9 .\-]", "", raw).strip()
    return safe


def _own_website(website: str | None) -> str | None:
    """Drop directory/aggregator links — keep only the business's own site."""
    if not website:
        return None
    if any(d in website.lower() for d in _DIRECTORY_DOMAINS):
        return None
    return website


def _build_query(city: str, filters: list[str]) -> str:
    area = _area_name(city).replace('"', "")
    body = "\n  ".join(filters)
    # Match an administrative boundary by name, then pull the elements inside it.
    return (
        f'[out:json][timeout:{_TIMEOUT_S}];\n'
        f'area["name"="{area}"]["boundary"="administrative"]->.a;\n'
        f'(\n  {body}\n);\n'
        f'out center tags {_MAX_PER_NICHE};'
    )


def _post_overpass(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        OVERPASS_URL, data=data, headers={"User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S + 10) as resp:
        payload = json.load(resp)
    return payload.get("elements", []) or []


def _to_lead(el: dict, city: str, niche: str) -> dict | None:
    tags = el.get("tags") or {}
    name = (tags.get("name") or tags.get("official_name") or "").strip()
    if not name:
        return None

    website = _own_website(tags.get("website") or tags.get("contact:website"))
    phone = (tags.get("phone") or tags.get("contact:phone") or "").strip()
    email = (tags.get("email") or tags.get("contact:email") or "").strip()
    biz_type = tags.get("amenity") or tags.get("healthcare") or tags.get("shop") or ""

    # Address from OSM addr:* tags (often partial — fine for an icebreaker corpus).
    parts = [
        tags.get("addr:housenumber"), tags.get("addr:street"),
        tags.get("addr:suburb"), tags.get("addr:city"), tags.get("addr:postcode"),
    ]
    address = ", ".join(p for p in parts if p)

    desc = f"{name}. {biz_type}. {address}.".strip()

    lead = {
        "company_name": name,
        "job_title": "",
        "location": city,
        "company_website": website,
        "poster_name": None,
        "date_posted": "recent",
        "job_description_text": desc[:2000],
        "slug": parse_slug(name),
        "domain": parse_domain(website),
        # Maps-native fields (same keys maps_scraper emits)
        "phone": phone,
        "maps_niche": niche,
        "business_type": biz_type,
        "address": address,
        "hours": tags.get("opening_hours", ""),
        "rating": "",
        "review_count": "",
        "maps_url": "",
        "source": "osm",
    }
    # OSM sometimes ships a real, verified email tag — pass it straight through so
    # the enricher/email_finder can skip discovery entirely for these.
    if email and "@" in email:
        lead["email"] = email
    return lead


def run(city: str) -> list[dict]:
    """Return free OSM leads for a city, deduped by slug. Never raises on a
    single failed niche query — a transient Overpass error skips that niche
    only, so the rest of the run still produces leads."""
    all_leads: list[dict] = []
    seen: set[str] = set()

    for niche, filters in OSM_NICHE_FILTERS:
        query = _build_query(city, filters)
        try:
            elements = _post_overpass(query)
        except Exception as e:
            print(f"    [OSM] {city} | '{niche}' query failed: {e}")
            log.warning("Overpass query failed for %s/%s: %s", city, niche, e)
            time.sleep(_SLEEP_BETWEEN_QUERIES_S)
            continue

        kept = 0
        for el in elements:
            lead = _to_lead(el, city, niche)
            if not lead:
                continue
            slug = lead["slug"]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            all_leads.append(lead)
            kept += 1

        print(f"    [OSM] {city} | '{niche}' -> {len(elements)} elements, {kept} new leads")
        time.sleep(_SLEEP_BETWEEN_QUERIES_S)

    return all_leads
