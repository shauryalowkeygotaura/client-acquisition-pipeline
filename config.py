# config.py
import os

CITIES = [
    "Jaipur",
    "Delhi",
    "Bangalore",
    "Mumbai",
    "Hyderabad",
    "Pune",
]

LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.1-8b-instant")
LLM_BASE_URL = "https://api.groq.com/openai/v1"

LINKEDIN_DAILY_LIMIT = 15
INDEED_DELAY_MIN = 3
INDEED_DELAY_MAX = 8

FIRECRAWL_HOMEPAGE_ONLY = True
