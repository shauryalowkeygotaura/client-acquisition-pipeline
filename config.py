# config.py
import os

CITIES = [
    "Jaipur, Rajasthan",
    "Delhi, Delhi",
    "Bangalore, Karnataka",
    "Mumbai, Maharashtra",
    "Hyderabad, Telangana",
    "Pune, Maharashtra",
]

LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = "https://api.groq.com/openai/v1"

LINKEDIN_DAILY_LIMIT = 15
INDEED_DELAY_MIN = 3
INDEED_DELAY_MAX = 8

FIRECRAWL_HOMEPAGE_ONLY = True
