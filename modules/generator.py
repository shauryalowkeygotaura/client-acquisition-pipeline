import json
import os
import re

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL

LLM_API_KEY = os.getenv("GROQ_API_KEY")

REQUIRED_FIELDS = {"vapi_prompt", "email_subject", "email_body", "linkedin_msg"}


class GeneratorError(Exception):
    pass


def build_prompt(data: dict) -> str:
    company = data["company_name"]
    contact = data.get("poster_name") or "there"
    details = data.get("scraped_details") or data.get("job_description_text", "")
    services = data.get("services", "")
    hours = data.get("hours", "")
    address = data.get("address", "")

    return f"""
You are Shaurya, a Revenue Growth Engineer specialized in voice AI automation.

Company: {company}
Contact: {contact}
Details: {details[:1500]}

Generate a JSON object with exactly these four fields:

1. "vapi_prompt" — A high-conversion system prompt for a Vapi.ai voice receptionist. 
   - Persona: Professional, efficient, and helpful.
   - Task: Greet callers, answer specific FAQs about {company} services/hours, book appointments via Cal.com/Calendly link (placeholder), and capture lead info.
   - Format: Return a SINGLE PLAIN TEXT STRING. Use clear headings and bullet points within the string. Do NOT return a nested JSON object or list.

2. "email_subject" — Compelling subject line focusing on missed revenue. 
   Example: "Fixing {company}'s missed calls (Revenue Growth)"

3. "email_body" — Personalized outreach:
   - Mention they are hiring a receptionist at {company}.
   - Frame the AI agent as a "Revenue Protection" tool that handles the load while they find the right human hire.
   - List 3 technical benefits: 24/7 coverage, instant CRM sync, and automated scheduling.
   - Casual but authoritative tone. Keep under 75 words.
   - Sign off: "Shaurya | Revenue Growth Engineer"

4. "linkedin_msg" — Pattern-interrupt LinkedIn DM (under 40 words). 
   - High impact, low friction. Offer to send a demo link of the custom agent you built for {company}.

Return ONLY the JSON.
""".strip()


def parse_output(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    cleaned = re.sub(r'\s*```$', '', cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise GeneratorError(f"Invalid JSON from model: {e}\nRaw: {raw[:200]}")

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise GeneratorError(f"Model output missing fields: {missing}")

    return data


def generate(data: dict) -> dict:
    client = OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )

    prompt = build_prompt(data)

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw = response.choices[0].message.content
    parsed = parse_output(raw)

    return {**data, **parsed}


def run(data: dict) -> dict:
    return generate(data)
