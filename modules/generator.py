import json
import os
import re

from openai import OpenAI

from config import OPENROUTER_MODEL, OPENROUTER_BASE_URL

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

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
You are writing outreach for Shaurya, who builds AI voice agents for businesses.

Company: {company}
Contact name: {contact}
Address: {address}
Services: {services}
Hours: {hours}
Additional info: {details[:1500]}

Generate a JSON object with exactly these four fields:

1. "vapi_prompt" — A system prompt for an AI voice receptionist for {company}.
   It should greet callers, answer FAQs about their services/hours, book appointments, and take messages.
   Be specific to this company using the info above.

2. "email_subject" — Subject line: "I built an AI receptionist for {company}"

3. "email_body" — Personalized email from Shaurya:
   - Mention {company} is urgently hiring a receptionist
   - Say he built an AI voice agent trained on {company}
   - List 3 key features: answers FAQs, automatically books appointments, updates their CRM
   - Offer to train it on whatever data they want to give him
   - Keep it under 80 words, casual and direct
   - Sign off as "Shaurya"

4. "linkedin_msg" — Shorter LinkedIn version (under 50 words), same key points, casual tone.

Return ONLY the JSON object. No markdown, no explanation.
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
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
    )

    prompt = build_prompt(data)

    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    raw = response.choices[0].message.content
    parsed = parse_output(raw)

    return {**data, **parsed}


def run(data: dict) -> dict:
    return generate(data)
