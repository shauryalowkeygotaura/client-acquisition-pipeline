"""
Usage: python scripts/create_agent.py <slug>
Example: python scripts/create_agent.py meridian-dental-llc

Creates a Vapi assistant from the stored prompt in Google Sheets.
Run this only when a prospect replies and you're prepping for a call.
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from modules import sheets_writer

VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_BASE = "https://api.vapi.ai"


def create_vapi_assistant(company_name: str, vapi_prompt: str) -> str:
    """Create a Vapi assistant and return its ID."""
    payload = {
        "name": f"{company_name} AI Receptionist",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": vapi_prompt}],
        },
        "voice": {
            "provider": "11labs",
            "voiceId": "rachel",
        },
        "firstMessage": f"Thank you for calling {company_name}, how can I help you today?",
    }

    resp = requests.post(
        f"{VAPI_BASE}/assistant",
        headers={"Authorization": f"Bearer {VAPI_API_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_agent.py <slug>")
        sys.exit(1)

    slug = sys.argv[1]
    print(f"Looking up: {slug}")

    lead = sheets_writer.get_by_slug(slug)
    if not lead:
        print(f"ERROR: No lead found with slug '{slug}'")
        sys.exit(1)

    company_name = lead["company_name"]
    vapi_prompt = lead["vapi_prompt"]

    if not vapi_prompt:
        print(f"ERROR: No Vapi prompt stored for {company_name}")
        sys.exit(1)

    if lead.get("vapi_assistant_id"):
        print(f"Agent already exists: {lead['vapi_assistant_id']}")
        print(f"Open Vapi dashboard → {company_name} AI Receptionist → Test")
        sys.exit(0)

    print(f"Creating Vapi assistant for: {company_name}")
    assistant_id = create_vapi_assistant(company_name, vapi_prompt)

    sheets_writer.update_field(slug, "vapi_assistant_id", assistant_id)
    sheets_writer.update_field(slug, "status", "agent_created")

    print(f"\nAgent created: {assistant_id}")
    print(f"Open Vapi dashboard → '{company_name} AI Receptionist' → Test")
    print("Share your screen and demo it live on the call.")


if __name__ == "__main__":
    main()
