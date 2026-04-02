import json
import os
import re

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL

LLM_API_KEY = os.getenv("GROQ_API_KEY")

REQUIRED_FIELDS = {
    "vapi_prompt", "email_subject",
    "email_body_pain", "email_body_curiosity", "email_body_roi",
    "linkedin_msg", "linkedin_post",
}


class GeneratorError(Exception):
    pass


# Psychographic profile of the ICP (small service business owner hiring a receptionist).
# Derived from icp-psychographic-mapper framework — baked in once so every lead gets
# copy that speaks to the actual human, not a job title.
_ICP_PSYCHOGRAPHIC = """
WHO YOU ARE WRITING TO (psychographic profile — use this to write in their language, not yours):
- 38–58 year old owner or office manager of a local service business (dental, physio, legal, medical, trades).
- They started or grew this business through referrals and reputation. They did not grow up with software.
- Their biggest daily fear: a patient or client called, nobody answered, and they booked somewhere else.
- They are not "innovation-minded." They do not want to be early adopters. They want something that works and doesn't cause problems.
- They are currently juggling the hiring gap stress: interviewing candidates, covering shifts, watching the front desk manually, and quietly worrying about what's slipping through.
- They have probably already tried: voicemail (people hang up), call forwarding to personal phones (burnt out), asking existing staff to cover (disruptive).
- They distrust vendors who overpromise. The phrase "AI-powered solution" makes them roll their eyes.
- What gets through to them: specificity (numbers, their niche, their city), brevity, and the sense that you actually looked at their business.
- Their internal monologue right now: "I just need this to work until I find someone. Or longer, if it actually works."
"""


def build_prompt(data: dict) -> str:
    company = data["company_name"]
    contact = data.get("poster_name") or "there"
    details = data.get("scraped_details") or data.get("job_description_text", "")
    services = data.get("services", "")
    location = data.get("location", "")
    industry = data.get("industry", "service business")

    return f"""
SENDER: Shaurya — a 22-year-old developer who builds AI voice receptionists for small businesses.
RECIPIENT: The owner or office manager at {company}, who posted a job listing for a human receptionist.
PURPOSE: Shaurya is writing a cold outreach email TO the business owner, FROM himself. He is not the one hiring. He is not affiliated with {company}. He is pitching his AI receptionist product to them.

CRITICAL: Every word of the email_body and linkedin_msg must be written FROM Shaurya TO the recipient. The "I" in the email is Shaurya. The "you" is the business owner at {company}. Never flip this. The job listing context below is background research — do NOT echo it back as if Shaurya is the one hiring.

{_ICP_PSYCHOGRAPHIC}

LEAD DATA (research context — do not parrot this back verbatim):
Company: {company}
Contact (use "there" if unknown): {contact}
Location: {location}
Industry/type: {industry}
Services/details: {services or details[:1500]}

---

COPY RULES (apply to every word in every field):
- Viking English: state the actual thing. Not a category, not a feeling, not a description of a benefit. The real fact.
  BAD: "improve your front desk operations" | GOOD: "answer calls while your hiring gap is open"
  BAD: "scalable AI solution" | GOOD: "a voice agent that picks up, asks what they need, and books via cal.com"
- If a sentence could describe any business in any industry without changing a word, rewrite it.
- No jargon: no "leverage", "synergy", "scalable", "pain points", "seamless", "AI-powered solution", "ROI", "value proposition".
- Short sentences. One idea per sentence. Sound like a real person.

---

Generate a JSON object with exactly these seven fields:

1. "vapi_prompt"
   Instructions written FOR an AI voice agent, telling it how to behave when it answers the phone at {company}.
   This is NOT a job description. This is NOT written by or about Shaurya. This is the agent's operating instructions.
   The agent speaks AS the receptionist for {company} — it picks up the phone on their behalf.

   Write it as: "You are the receptionist for {company}. When someone calls, [what to do]..."
   3–4 sentences. Must cover: greet by company name, find out what the caller needs, offer to book via cal.com, take name and callback number if they prefer a callback.
   Use actual details from the services/location data above where possible (e.g. mention their specific service type).
   Return as a single plain string with no line breaks inside.

2. "email_subject"
   2–4 words MAX. Lowercase. No punctuation. No capitalization.
   The goal: look like an email from a colleague or vendor they already know. Completely mundane.
   A subject that promises nothing, reveals nothing, sounds like nothing special.

   The test: would this subject appear in a thread between two people who already work together? If yes, use it. If it sounds like it was "written for outreach," reject it.

   Strong examples (use these patterns, not these exact words):
   - "front desk coverage"
   - "call gap"
   - "while interviewing"
   - "quick question"
   - "the front desk"
   - "[city] clinics"

   Never use: "while you're still hiring", "for the gap before the hire", "re: your job posting", any phrase with "AI", "receptionist solution", "revenue", "grow", or anything that implies a benefit or a pitch.

3. "email_body_pain"
   Angle: COST OF INACTION. Every day without coverage = bookings they'll never recover.
   Follow H-A-O-P-CTA. Do NOT label sections. Write as flowing prose.

   HOOK (1 sentence): A specific observable fact — the job posting, the gap duration, the city. Make it about them, not you.
   Forbidden openers: "I hope", "My name is", "I came across", "I wanted to reach out", "I noticed your posting".
   Strong examples: "You've had that receptionist role open at [company] for [X days]." / "Running a {industry} in {location} without front-desk cover is [specific consequence]."

   AGITATE (2 sentences): Surface the business impact first, then the dollar cost. Use "~" for estimates. Never invent exact figures. Compare to what competitors are doing if natural.

   OUTCOME (2 sentences): The measurable result. Lead with numbers. Use "would" or "could". Never say "AI", "software", "solution".
   Example: "Most {industry} businesses here recover 30–40% of missed calls in the first few weeks."

   PROOF (1 sentence only): OMIT ENTIRELY if no real proof available — do NOT invent it.

   CTA (1–2 sentences): Offer a free 2-min demo clip. Ask one yes/no question. Never say "hop on a call", "book a meeting", "schedule time".

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 160–220 words. "I" = Shaurya, "you" = business owner.

4. "email_body_curiosity"
   Angle: CURIOSITY GAP. Ask a question they can't answer without engaging.
   Same H-A-O-P-CTA structure, same hard rules, same word count.

   HOOK (1 sentence): A question or observation that reveals a gap they haven't thought about. Specific to their niche and city. Should feel like something only someone who looked at their business would say.
   Examples: "Do you know how many calls go unanswered at {company} on a Tuesday afternoon?" / "Most {industry} owners in {location} don't know their missed-call rate until I show them."

   AGITATE (2 sentences): Expand on the unknown — what they can't see is costing them. Make the invisible visible with a plausible number.

   OUTCOME (2 sentences): What clarity (and a fix) would look like. Numbers if possible. No product mention.

   PROOF (1 sentence only): OMIT if unavailable.

   CTA (1–2 sentences): Offer to share a missed-call estimate or a 2-min clip. One yes/no ask.

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 160–220 words. "I" = Shaurya.

5. "email_body_roi"
   Angle: ROI / COMPETITOR COMPARISON. Show them the number, compare to what competitors do.
   Same H-A-O-P-CTA structure, same hard rules, same word count.

   HOOK (1 sentence): A concrete revenue number tied to their niche. Make it feel like something they should already know.
   Examples: "A {industry} business in {location} typically gets 40–80 inbound calls a week." / "The {industry} practice two blocks from {company} just stopped missing calls."

   AGITATE (2 sentences): If they're losing X calls a day at Y value per booking, that's Z per month. Competitor angle — similar businesses have already solved this.

   OUTCOME (2 sentences): What a fix would add back in revenue terms. Use conservative "~" estimates.

   PROOF (1 sentence only): OMIT if unavailable.

   CTA (1–2 sentences): Offer a quick clip or a specific estimate for their practice. One yes/no ask.

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 160–220 words. "I" = Shaurya.

6. "linkedin_msg"
   70–100 words. A genuine LinkedIn DM or connection note — not a pitch, not a template.

   Pick ONE of these angle types and write from it (choose whichever fits the lead best):
   - TIMING ANGLE: The job posting is live = they are in the problem RIGHT NOW. Open with the timing.
   - LOSS-FRAMING ANGLE: Every day without coverage = calls they'll never get back. Open with what's already lost.
   - CURIOSITY GAP ANGLE: Ask a question they can't answer without engaging (specific to their niche/location).
   - SOCIAL PROOF ANGLE: Open with what a similar business in their area or niche has already done.

   Structure: one sharp opening observation (their situation, not your product) → one sentence explaining what you built and exactly what it does → one soft ask (send a 2-min clip, not a call).
   No "I hope you're doing well", no "exciting opportunity", no buzzwords. Write like you typed it in a hurry because you meant it.

7. "linkedin_post"
   A LinkedIn post Shaurya can publish from his own profile. 150–200 words. First-person. No list format.
   Based on what you observed about this type of business (use {industry} and {location} as context — do NOT name {company} specifically).

   Structure:
   - Hook line (1 sentence, no label): A sharp observation about something you noticed while doing research. Make it specific — a number, a pattern, a tension. Must make someone stop scrolling.
   - Story (3–4 sentences): What you found, what it means, what's actually happening in this niche right now.
   - Insight (2 sentences): The non-obvious thing most people miss about this problem or this type of business.
   - Soft close (1 sentence): A question to the reader or a quiet CTA. Not "DM me." Not "link in bio."

   Tone: thoughtful, direct, slightly contrarian. Sounds like a builder observing the world — not a marketer selling something.
   No emojis, no hashtags, no bullet lists.

Return ONLY valid JSON. No markdown fences, no explanation, no extra keys.
""".strip()


def parse_output(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    cleaned = cleaned.replace("\\'", "'")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise GeneratorError(f"Invalid JSON from model: {e}\nRaw: {raw[:200]}")

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise GeneratorError(f"Model output missing fields: {missing}")

    return data


def generate(data: dict) -> dict:
    if not LLM_API_KEY:
        raise GeneratorError("GROQ_API_KEY env var is not set.")

    client = OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )

    prompt = build_prompt(data)

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2800,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        raise GeneratorError(f"LLM API call failed: {e}")

    if not response.choices:
        raise GeneratorError("LLM returned empty choices list.")

    raw = response.choices[0].message.content
    if not raw:
        raise GeneratorError("LLM returned empty message content.")

    parsed = parse_output(raw)
    return {**data, **parsed}


_PAIN_NICHES = {"dental", "medical", "legal", "physio", "optometry", "veterinary"}
_ROI_NICHES = {"salon", "trades", "hotel"}


def _select_variant(data: dict) -> tuple[str, str]:
    """
    Pick the best email body variant for this lead.
    Returns (email_body, message_variant_id).

    Rules:
      - High-urgency + pain niche → pain (they know what's at stake, hit the nerve)
      - trades/salon/hotel → ROI (revenue numbers resonate more than pain)
      - Everything else → curiosity (lowest friction, works across niches)
    """
    niche = data.get("niche", "general")
    urgency = data.get("hiring_urgency", "medium")
    priority = data.get("lead_priority", "medium")

    if urgency == "high" and niche in _PAIN_NICHES:
        return data["email_body_pain"], "pain"
    if niche in _ROI_NICHES:
        return data["email_body_roi"], "roi"
    if priority == "high" and niche in _PAIN_NICHES:
        return data["email_body_pain"], "pain"
    return data["email_body_curiosity"], "curiosity"


def run(data: dict) -> dict:
    result = generate(data)
    email_body, variant_id = _select_variant(result)
    result["email_body"] = email_body
    result["message_variant_id"] = variant_id
    return result
