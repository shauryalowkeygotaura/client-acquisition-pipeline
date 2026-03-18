# config.py
import os

CITIES = [
    "New York, NY",
    "Los Angeles, CA",
    "Chicago, IL",
]

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

LINKEDIN_DAILY_LIMIT = 15
INDEED_DELAY_MIN = 3
INDEED_DELAY_MAX = 8

FIRECRAWL_HOMEPAGE_ONLY = True
