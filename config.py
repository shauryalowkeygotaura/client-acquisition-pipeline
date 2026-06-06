# config.py
import os

# ── 2026-05-29 niche-down: local clinics + schools ───────────────────────────
# Lead source is now clinics + schools (see MAPS_QUERIES). Kept all 6 metros so
# the harvest has volume; Jaipur stays first (home base + existing deployments).
CITIES = [
    "Jaipur, Rajasthan",
    "Delhi, Delhi",
    "Bangalore, Karnataka",
    "Mumbai, Maharashtra",
    "Hyderabad, Telangana",
    "Pune, Maharashtra",
]

# Google Maps harvest (LEAD_SOURCE=maps). Each query is run per city against the
# SerpAPI google_maps engine. The niche tag is attached to every result the query
# returns so the enricher/icebreaker know what they're looking at.
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

LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = "https://api.groq.com/openai/v1"

LINKEDIN_DAILY_LIMIT = 15
INDEED_DELAY_MIN = 3
INDEED_DELAY_MAX = 8

FIRECRAWL_HOMEPAGE_ONLY = True
