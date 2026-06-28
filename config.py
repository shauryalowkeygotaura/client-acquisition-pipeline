# config.py
import os

# ── 2026-05-29 niche-down: local clinics + schools ───────────────────────────
# Kept all 6 metros so the funnel has volume; Jaipur stays first (home base +
# existing deployments). Lead-source priority (see pipeline.py LEAD_SOURCE):
#   primary (indeed→google_jobs fallback, or apollo) → maps backfill → osm floor.
CITIES = [
    "Jaipur, Rajasthan",
    "Delhi, Delhi",
    "Bangalore, Karnataka",
    "Mumbai, Maharashtra",
    "Hyderabad, Telangana",
    "Pune, Maharashtra",
]

# Google Maps backfill (last paid resort before the keyless OSM floor). Each
# query runs per city against the SerpAPI google_maps engine; the niche tag is
# attached to every result so enricher/scorer know what they're looking at.
# Fires only when the primary source returns fewer than MAPS_BACKFILL_MIN leads
# for a city AND SerpAPI quota remains (maps is paid; osm is the free floor).
# (query string, niche)
MAPS_QUERIES = [
    ("dental clinic", "dental"),
    ("dental implant clinic", "dental"),
    ("multispeciality clinic", "medical"),
    ("physiotherapy clinic", "physio"),
    ("eye clinic", "optometry"),
    ("school", "school"),
    ("CBSE school", "school"),
    ("coaching institute", "school"),
    ("play school", "school"),
]

# How many result pages (20 listings each) to pull per query per city.
MAPS_PAGES_PER_QUERY = int(os.getenv("MAPS_PAGES_PER_QUERY", "1"))

# Per-city lead count below which maps backfill kicks in.
MAPS_BACKFILL_MIN = int(os.getenv("MAPS_BACKFILL_MIN", "5"))

LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = "https://api.groq.com/openai/v1"

LINKEDIN_DAILY_LIMIT = 15
INDEED_DELAY_MIN = 3
INDEED_DELAY_MAX = 8

FIRECRAWL_HOMEPAGE_ONLY = True
