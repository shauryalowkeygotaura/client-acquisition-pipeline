import json
import os
import random
import re

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL

# Self-improving loop: how often to EXPLORE (ignore the learned winner and let
# the fixed rules pick) vs EXPLOIT the best-performing variant for this niche.
# 0.2 = exploit the winner 80% of the time, keep exploring 20% so a variant that
# stops working can be dethroned. See modules/learning.py.
EXPLORE_EPSILON = float(os.getenv("GENERATOR_EXPLORE_EPSILON", "0.2"))
_LEARNED_CACHE: dict | None = None


def _learned() -> dict:
    """Last run's learned levers (cached). Empty dict on first run / any error."""
    global _LEARNED_CACHE
    if _LEARNED_CACHE is None:
        try:
            from modules import learning
            _LEARNED_CACHE = learning.load()
        except Exception:
            _LEARNED_CACHE = {}
    return _LEARNED_CACHE

LLM_API_KEY = os.getenv("GROQ_API_KEY")

REQUIRED_FIELDS = {
    "vapi_prompt", "email_subject",
    "email_body_pain", "email_body_curiosity", "email_body_roi",
    "email_body_question", "email_body_outcome",
    "linkedin_msg", "linkedin_post",
}


class GeneratorError(Exception):
    pass


# Psychographic profile of the ICP (small service business owner hiring a receptionist).
# Derived from icp-psychographic-mapper framework — baked in once so every lead gets
# copy that speaks to the actual human, not a job title.
#
# IMPORTANT: _ICP_PSYCHOGRAPHIC and _COPY_RULES_STATIC are kept as separate constants
# placed at the START of build_prompt's f-string so Groq's automatic prompt caching can
# match the static prefix across leads. Moving any interpolated `{var}` ahead of these
# blocks defeats the cache and doubles input cost — keep dynamic content below.
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

_COPY_RULES_STATIC = """
COPY RULES (apply to every word in every field):
- Viking English: state the actual thing. Not a category, not a feeling, not a description of a benefit. The real fact.
  BAD: "improve your front desk operations" | GOOD: "answer calls while your hiring gap is open"
  BAD: "scalable AI solution" | GOOD: "a voice agent that picks up, asks what they need, and books via Google Meet"
- If a sentence could describe any business in any industry without changing a word, rewrite it.
- No jargon: no "leverage", "synergy", "scalable", "pain points", "seamless", "AI-powered solution", "ROI", "value proposition".
- Short sentences. One idea per sentence. Sound like a real person.
"""

_SENDER_FRAMING_STATIC = """
SENDER: Shaurya — a 22-year-old developer who builds AI voice receptionists for small businesses.
PURPOSE: Shaurya is writing a cold outreach email TO a business owner, FROM himself. He is not the one hiring. He is not affiliated with the target company. He is pitching his AI receptionist product to them.
"""


def build_prompt(data: dict) -> str:
    company = data["company_name"]
    contact = data.get("poster_name") or "there"
    details = data.get("scraped_details") or data.get("job_description_text", "")
    services = data.get("services", "")
    location = data.get("location", "")
    industry = data.get("industry", "service business")
    person_hook = data.get("person_hook", "")
    company_hook = data.get("company_hook", "")

    hooks_section = ""
    if person_hook or company_hook:
        hooks_section = "\nPERSONALIZATION HOOKS (verified facts — use ONE of these as the email/DM opener if available):\n"
        if person_hook:
            hooks_section += f"Person hook: {person_hook}\n"
        if company_hook:
            hooks_section += f"Company hook: {company_hook}\n"
        hooks_section += (
            "MANDATORY RULE: The HOOK sentence (first line) of email_body_pain, email_body_curiosity, "
            "email_body_roi, AND the linkedin_msg opener MUST begin with this specific fact — verbatim or "
            "lightly paraphrased. Do NOT open with a generic observation when a hook is available. "
            "Reference it naturally — don't announce that you researched them.\n"
        )

    # Static blocks first (cacheable prefix). Dynamic interpolations come AFTER.
    return f"""
{_ICP_PSYCHOGRAPHIC}

{_COPY_RULES_STATIC}

{_SENDER_FRAMING_STATIC}

---

RECIPIENT: The owner or office manager at {company}, who posted a job listing for a human receptionist.

CRITICAL: Every word of the email_body and linkedin_msg must be written FROM Shaurya TO the recipient. The "I" in the email is Shaurya. The "you" is the business owner at {company}. Never flip this. The job listing context below is background research — do NOT echo it back as if Shaurya is the one hiring.

LEAD DATA (research context — do not parrot this back verbatim):
Company: {company}
Contact (use "there" if unknown): {contact}
Location: {location}
Industry/type: {industry}
Services/details: {services or details[:1500]}
{hooks_section}

---

Generate a JSON object with exactly these fields:

1. "vapi_prompt"
   Instructions written FOR an AI voice agent, telling it how to behave when it answers the phone at {company}.
   This is NOT a job description. This is NOT written by or about Shaurya. This is the agent's operating instructions.
   The agent speaks AS the receptionist for {company} — it picks up the phone on their behalf.

   Write it as: "You are the receptionist for {company}. When someone calls, [what to do]..."
   3–4 sentences. Must cover: greet by company name, find out what the caller needs, offer to book via Google Meet, take name and callback number if they prefer a callback.
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
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line. No single giant paragraph.

   HOOK (1 sentence): If PERSONALIZATION HOOKS are provided above, this sentence MUST open with that specific fact. Otherwise: a specific observable fact — the job posting, the gap duration, the city. Make it about them, not you.
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
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line.

   HOOK (1 sentence): If PERSONALIZATION HOOKS are provided above, this sentence MUST open with that specific fact. Otherwise: a question or observation that reveals a gap they haven't thought about. Specific to their niche and city. Should feel like something only someone who looked at their business would say.
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
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line.

   HOOK (1 sentence): If PERSONALIZATION HOOKS are provided above, this sentence MUST open with that specific fact. Otherwise: a concrete revenue number tied to their niche. Make it feel like something they should already know.
   Examples: "A {industry} business in {location} typically gets 40–80 inbound calls a week." / "The {industry} practice two blocks from {company} just stopped missing calls."

   AGITATE (2 sentences): If they're losing X calls a day at Y value per booking, that's Z per month. Competitor angle — similar businesses have already solved this.

   OUTCOME (2 sentences): What a fix would add back in revenue terms. Use conservative "~" estimates.

   PROOF (1 sentence only): OMIT if unavailable.

   CTA (1–2 sentences): Offer a quick clip or a specific estimate for their practice. One yes/no ask.

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 160–220 words. "I" = Shaurya.

6. "linkedin_msg"
   60–90 words. A human-first LinkedIn DM — NOT a pitch in disguise.

   HORMOZI RULE: The first DM is not about the content. It is about being human.

   Step 1 — OPEN HUMAN (1 sentence): If a person_hook is available, open with a specific observation
   about them that only someone who actually looked at their profile would make — a recent role change,
   a credential, a post they wrote, a piece of their background. Do NOT announce that you researched them.
   Just say the thing naturally. If no person_hook, open with a genuine observation about their business
   that shows you looked ("Saw you posted the receptionist role at {company} — hope the search is going well.")
   This line is ONLY about them. No product, no ask, no you.

   Step 2 — BRIDGE (1 sentence): What you built and exactly what it does. Short, specific, no jargon.
   Example: "I build voice agents for {industry} businesses — they answer calls, handle FAQs, and book via Google Meet."

   Step 3 — SOFT ASK (1 sentence): "Would it be okay if I sent a 2-min clip?" — nothing more.
   No "hop on a call", no "schedule a demo", no "let's connect", no "I'd love to".

   Tone: sounds typed at a desk, slightly casual. Under 90 words total.
   No "I hope", no "exciting opportunity", no buzzwords.

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

8. "email_body_outcome"
   Angle: OPERATIONAL OUTCOME (v3 default for India dental/medical/physio with high digital maturity).
   Sells the *result*, not the category. Never the word "AI" or "voice agent" — the OUTCOME.
   Same H-A-O-P-CTA structure, same hard rules, same word count.
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line.

   HOOK (1 sentence): A specific operational fact about their business. If PERSONALIZATION HOOKS provided, use that. Otherwise:
   Examples: "Most new-patient enquiries to {company} between 8pm and 10am go unanswered."
             "Sunday and lunch-hour calls are usually the ones that book somewhere else."
             "Front desk gaps during staff leave are when most {industry} businesses bleed bookings."

   AGITATE (2 sentences): Frame the operational loss in their words. "Patients", "enquiries", "bookings", "WhatsApp messages at 11pm".
   Do NOT mention technology. Make the missed-call cost concrete in their workflow.

   OUTCOME (2 sentences): A single concrete operational promise. Pick ONE:
     - "Never miss a new-patient call after 8pm."
     - "Front desk backup during staff leave, hiring gaps, and lunch hours."
     - "Bookings captured for Sunday and late-night enquiries."
     - "WhatsApp continuity for after-hours questions."
   Then quantify the typical impact in their niche/city using "~" estimates.

   PROOF (1 sentence only): OMIT if no real proof. Do NOT invent.

   CTA (1–2 sentences): Offer a 2-min clip of what handled calls would sound like in their language (English / Hinglish if applicable). One yes/no ask.

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 160–220 words. Never use "AI", "automation", "agent", "bot", "solution".
   "I" = Shaurya, "you" = business owner.

9. "email_body_question"
   Angle: HORMOZI QUESTION OPENER — the most Hormozi-aligned format. Brevity as respect.
   This is a 3-line cold email designed to get a reply, not to pitch.

   LINE 1 (QUESTION ONLY): "Are you still looking to [4-word outcome]?"
   The 4-word outcome must come from their world, not yours. Make it sound like something they would type.
   Examples: "Are you still looking to cover front-desk calls?" / "Are you still looking to fill the receptionist gap?" /
   "Are you still looking to stop missing calls?"
   Use their niche and location if it sounds natural. One sentence, ends with a question mark. Nothing else on this line.

   LINE 2 (BRIDGE, 1 sentence): What you built, what it does. Specific, no jargon, no AI buzzwords.
   Example: "I build voice agents for {industry} businesses — they pick up, answer FAQs, and book via Google Meet."

   LINE 3 (ULTRA-SOFT CTA): "Happy to send a 2-min clip if useful."

   Sign off: — Shaurya

   Hard rules: 3 lines + sign-off. Under 50 words. No paragraphs. No H-A-O-P structure.
   No bold, no bullets, no emojis. "I" = Shaurya. This respects the reader's time more than any other format.

10. "instagram_msg"
   A VERY informal Instagram DM — this is the opposite of the LinkedIn voice. Think one creator
   sliding into another small-business owner's DMs, NOT a salesperson.

   Tone: lowercase, casual, texting-not-emailing. Contractions, the odd filler ("ngl", "honestly",
   "btw") used sparingly and naturally. 1–2 tasteful emojis MAX (optional — skip if it feels forced).
   It should read like a real human typed it on their phone, not a marketer.

   Structure (keep it tiny — 2 to 4 short lines, under 45 words total):
   - line 1: a genuine, specific compliment or observation about their page/clinic ("yo your clinic page is clean,
     the {niche} reels are actually good").
   - line 2: one casual line on what you do, plainly ("i set up a lil thing that auto-replies to DMs + missed
     calls so you stop losing patients after hours").
   - line 3: a super low-pressure ask ("want me to send a 10-sec demo? no pressure").

   Hard rules: NO sign-off, NO "Dear", NO "I hope this finds you", NO corporate words ("solution",
   "leverage", "reach out", "opportunity"). NO links. If a person_hook/company_hook exists, use it
   naturally in line 1. Never write more than 4 lines.

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

    # Normalise email bodies: collapse \\n literals, ensure paragraph breaks exist.
    # Models sometimes emit literal \n instead of real newlines inside JSON strings.
    for field in ("email_body_pain", "email_body_curiosity", "email_body_roi",
                  "email_body_question", "email_body_outcome"):
        if field in data:
            body = data[field]
            # Unescape literal \n sequences the model may have emitted
            body = body.replace("\\n\\n", "\n\n").replace("\\n", "\n")
            # Ensure sign-off is on its own line
            body = body.replace(" — Shaurya", "\n\n— Shaurya")
            body = body.replace("\n— Shaurya", "\n\n— Shaurya")
            # Collapse triple+ newlines to double
            import re as _re
            body = _re.sub(r'\n{3,}', '\n\n', body).strip()
            data[field] = body

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
            max_tokens=5000,
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
_OUTCOME_NICHES = {"dental", "medical", "physio"}  # v3: India SMB healthcare core


def _is_india(data: dict) -> bool:
    loc = (data.get("location") or "").lower()
    return any(k in loc for k in (
        "india", "delhi", "mumbai", "bangalore", "bengaluru", "hyderabad",
        "pune", "jaipur", "chennai", "kolkata", "ahmedabad", "kochi",
    ))


def _select_variant(data: dict) -> tuple[str, str]:
    """
    Pick the best email body variant for this lead.
    Returns (email_body, message_variant_id).

    v3 selection order (first match wins):
      1. India + outcome-niche + adoption_score ≥ 5 → OUTCOME
         (high-digital-maturity Indian clinics respond to operational framing,
          not pain framing — they already know the pain)
      2. High-urgency + pain niche → PAIN
      3. trades/salon/hotel → ROI
      4. High priority + pain niche → PAIN
      5. No hooks + generic niche → QUESTION (Hormozi brevity opener)
      6. Default → CURIOSITY
    """
    niche = data.get("niche", "general")
    urgency = data.get("hiring_urgency", "medium")
    priority = data.get("lead_priority", "medium")
    adoption = data.get("adoption_score", 0)
    has_hooks = bool(data.get("person_hook") or data.get("company_hook"))

    # ── Self-improving: EXPLOIT the learned best variant for this niche ───────
    # learning.py only records a winner once it beats the field with statistical
    # significance. We exploit it (1 - EXPLORE_EPSILON) of the time; the rest of
    # the time we fall through to the fixed rules to keep exploring, so a variant
    # that decays can lose its crown.
    learned_variant = _learned().get("variant_by_niche", {}).get(niche)
    if learned_variant and random.random() > EXPLORE_EPSILON:
        body = data.get(f"email_body_{learned_variant}")
        if body:
            return body, learned_variant

    # v3: India + healthcare-outcome niche + above-median adoption → outcome framing
    if _is_india(data) and niche in _OUTCOME_NICHES and adoption >= 5:
        return data["email_body_outcome"], "outcome"

    if urgency == "high" and niche in _PAIN_NICHES:
        return data["email_body_pain"], "pain"
    if niche in _ROI_NICHES:
        return data["email_body_roi"], "roi"
    if priority == "high" and niche in _PAIN_NICHES:
        return data["email_body_pain"], "pain"
    if not has_hooks and niche not in _PAIN_NICHES and niche not in _ROI_NICHES:
        return data["email_body_question"], "question"
    return data["email_body_curiosity"], "curiosity"


def run(data: dict) -> dict:
    result = generate(data)
    email_body, variant_id = _select_variant(result)
    result["email_body"] = email_body
    result["message_variant_id"] = variant_id
    return result
