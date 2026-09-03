import json
import logging
import os
import random
import re

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL
from modules import persona

log = logging.getLogger(__name__)

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
    # DM copy is REQUIRED, not optional. When instagram_msg was merely nice to
    # have, instagram.send quietly fell back to linkedin_msg and shipped
    # LinkedIn-register copy into a texting window. A missing DM field is now a
    # generation failure, which is loud, instead of a wrong-register send, which
    # is silent.
    "instagram_msg", "whatsapp_msg",
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
- The front desk is one person who also handles walk-ins, payments and files. When they step away, the phone rings out.
- They have probably already tried: voicemail (people hang up), call forwarding to personal phones (burnt out), asking existing staff to cover (disruptive).
- They distrust vendors who overpromise. The phrase "AI-powered solution" makes them roll their eyes.
- What gets through to them: specificity (their niche, their city, something only a person who looked would know), brevity, and no sense of being sold to.
- A student asking them to look at something is a category they have no defence against, because it is not a sale. Do not squander that by turning it into one.
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

# Sender identity, proof, and offer live in modules/persona.py so every channel
# tells the same story. Referenced (not re-declared) here to keep the static
# cacheable prefix intact.
_SENDER_FRAMING_STATIC = persona.SENDER_FRAMING + persona.FRAME_BREAKERS

_STRUCTURE_STATIC = f"""
THE SHAPE OF EVERY FIRST TOUCH (all channels — email, DM, WhatsApp):
1. WHO: "I'm {persona.SENDER_NAME}, I'm at {persona.SCHOOL_SHORT}" + "I'm doing a school project."
   This is line one. Not buried, not after a compliment about their business.
2. WHY THEM: one specific, verified observation about THIS business. The PERSONALIZATION HOOKS
   section below states exactly what you are and are not allowed to say here for this lead.
   Follow it literally. Never invent an observation.
3. WHAT: {persona.WHAT_IT_DOES}. Plain words, one sentence.
4. PROOF + DEMO: he can set a free demo up on their own details — and, when the PROOF SPEC below
   carries a number, that number too. Stated flatly, then move on. The demo is something he HAS,
   not what he is asking for. If the PROOF SPEC is empty, the demo sentence still appears alone.
5. ASK: {persona.TOUCH_1_ASK}, because {persona.FEEDBACK_FRAME}.
   "Can I talk to you about this sometime? Just want some feedback on it." is the shape.

Order matters. WHO before WHY THEM: the school line is what buys the next sentence, so it
cannot come second. PROOF before ASK: the number is what makes the conversation worth granting.

WHAT NEVER APPEARS ON THE FIRST TOUCH:
- a specific time, a duration ("15 minutes", "5 mins"), a calendar link, "hop on", "schedule",
  "book". The ask is willingness to talk sometime, not an appointment.
- a price, a discount, a trial, or the word "free" attached to anything except the demo
- more than one question. Exactly one, at the end.

── NAMED SPECS ──────────────────────────────────────────────────────────────
The message fields below refer to these by name instead of restating them. Where a
field says "OPENER (per spec)", it means exactly this and nothing else:

OPENER SPEC (1 sentence, always the FIRST sentence of every message):
  Who he is and why he is writing, in one breath. The school project is stated, not hinted.
  Vary the wording per field so five messages don't read as one template, but the two facts
  ({persona.SCHOOL_SHORT} + school project) are non-negotiable and always come first.
  Good: "I'm {persona.SENDER_NAME}, I'm in school at {persona.SCHOOL_SHORT}, and I built something for a school project."
  Good: "I'm at {persona.SCHOOL_SHORT} and this is for a school project, so I'll keep it short."
  Forbidden: "My name is X and I am reaching out", "I hope this finds you", any apology for writing.

HOOK SPEC (1 sentence, always the SECOND sentence):
  The WHY THEM line, built from PERSONALIZATION HOOKS when they exist and from the category
  and place when they don't. See the PERSONALIZATION HOOKS section below — its rule wins over
  any example written in an individual field spec.

PROOF SPEC (1 clause or 1 short sentence):
  "{persona.proof_line()}" — stated flatly, in his own words, then dropped. No elaboration,
  no names, no testimonials, no results, no "and counting". If the proof line above is empty,
  OMIT proof entirely and never substitute a vaguer claim.

CTA SPEC (always LAST — one yes/no question, plus the short feedback reason):
  Ask {persona.TOUCH_1_ASK}. The reason is that {persona.FEEDBACK_FRAME}, and that reason is
  mandatory: it is what makes the ask cheap to grant. Keep it to a handful of words.
  Good: "Can I talk to you about this sometime? Just want some feedback on it."
  Good: "Could I talk to you about it sometime? I mainly just want feedback from someone who'd know."
  Forbidden: a specific time, a duration, a calendar link, "book", "schedule", "hop on",
  "let me know if interested", or any second question beyond the one ask.
"""


def build_prompt(data: dict) -> str:
    company = data["company_name"]
    contact = data.get("poster_name") or "there"
    details = data.get("scraped_details") or data.get("job_description_text", "")
    # `or ""` not `.get(k, "")`: scraper/JSON rows routinely carry an explicit
    # None for these, which .get() happily returns and every downstream string
    # op then trips over.
    services = data.get("services") or ""
    location = data.get("location") or ""
    industry = data.get("industry") or "service business"
    person_hook = data.get("person_hook", "")
    company_hook = data.get("company_hook", "")

    # "dental businesses in Jaipur" when we know both; degrades to just the
    # category when the lead carries no usable location, so the fallback can
    # never emit a dangling "... in ".
    where = f" in {location}" if location.strip() else ""
    category_phrase = f"{industry} businesses{where}"

    if person_hook or company_hook:
        hooks_section = "\nPERSONALIZATION HOOKS (verified facts — this is the WHY THEM sentence, step 2):\n"
        if person_hook:
            hooks_section += f"Person hook: {person_hook}\n"
        if company_hook:
            hooks_section += f"Company hook: {company_hook}\n"
        hooks_section += (
            "MANDATORY RULE: In every email body, the linkedin_msg, the instagram_msg and the "
            "whatsapp_msg, the sentence IMMEDIATELY AFTER the school-project line MUST be built from "
            "this fact — verbatim or lightly paraphrased. It never moves to line one: the school line "
            "goes first, the observation second. Reference it naturally — don't announce that you "
            "researched them, and never say the words 'I noticed' or 'I came across'.\n"
        )
    else:
        # No hook survived enrichment. The model must NOT fill the gap with a
        # guess — an invented observation ("saw your reels") aimed at a clinic
        # with no Instagram is the fastest way to look like a blast.
        hooks_section = (
            "\nPERSONALIZATION HOOKS: none available for this lead.\n"
            "MANDATORY RULE: Do NOT invent an observation. Do not claim to have seen their reels, "
            "their page, their posts, their website, their reviews, or their photos. Nobody looked. "
            "The WHY THEM sentence must instead be about the category and the place — "
            f"'{category_phrase}' — and nothing more specific than that.\n"
        )

    # Static blocks first (cacheable prefix). Dynamic interpolations come AFTER.
    return f"""
{_ICP_PSYCHOGRAPHIC}

{_COPY_RULES_STATIC}

{_SENDER_FRAMING_STATIC}

{_STRUCTURE_STATIC}

---

RECIPIENT: The owner or office manager at {company}.

CRITICAL: Every word of every message must be written FROM Shaurya TO the recipient. The "I" is Shaurya, the student. The "you" is the business owner at {company}. Never flip this. The lead data below is background research — it is what Shaurya knows, not what he repeats back at them.

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

   The school frame is the strongest subject line available and it is TRUE, so lead with it.
   Strong examples (use these patterns, not these exact words):
   - "school project"
   - "school project question"
   - "student from {persona.SCHOOL_SHORT}"
   - "quick question"
   - "the front desk"

   Never use: any phrase with "AI", "receptionist solution", "revenue", "grow", "demo", "free",
   or anything that implies a benefit or a pitch. The subject sells the read, not the product.

3. "email_body_pain"
   Angle: COST OF INACTION. Every day without coverage = bookings they'll never recover.
   Follow H-A-O-P-CTA. Do NOT label sections. Write as flowing prose.
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line. No single giant paragraph.

   OPENER (per spec) → HOOK (per spec) → then the angle below.

   AGITATE (2 sentences): Surface the business impact of calls going unanswered. Use "~" for estimates. Never invent exact figures.

   OUTCOME (2 sentences): What it does for them, concretely. Use "would" or "could". Never say "AI", "software", "solution".

   WHAT IT IS (1 sentence): {persona.WHAT_IT_DOES}. Plain words.

   PROOF (per spec)

   CTA (per spec)

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 120–170 words. "I" = Shaurya, "you" = business owner.

4. "email_body_curiosity"
   Angle: CURIOSITY GAP. Ask a question they can't answer without engaging.
   Same H-A-O-P-CTA structure, same hard rules, same word count.
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line.

   OPENER (per spec) → HOOK (per spec) → then the angle below.

   AGITATE (2 sentences): Expand on the unknown — the calls they never find out about. Make the invisible visible with a plausible "~" number.

   OUTCOME (2 sentences): What knowing (and fixing) it would look like.

   WHAT IT IS (1 sentence): {persona.WHAT_IT_DOES}. Plain words.

   PROOF (per spec)

   CTA (per spec)

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 120–170 words. "I" = Shaurya.

5. "email_body_roi"
   Angle: ROI / COMPETITOR COMPARISON. Show them the number, compare to what competitors do.
   Same H-A-O-P-CTA structure, same hard rules, same word count.
   FORMATTING: Separate each section with a blank line (\n\n). Sign-off on its own line.

   OPENER (per spec) → HOOK (per spec) → then the angle below.

   AGITATE (2 sentences): If they're missing X calls a day at Y value per booking, that's Z per month. Conservative "~" estimates only.

   OUTCOME (2 sentences): What a fix would add back. Conservative "~" estimates.

   WHAT IT IS (1 sentence): {persona.WHAT_IT_DOES}. Plain words.

   PROOF (per spec)

   CTA (per spec)

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 120–170 words. "I" = Shaurya.

6. "linkedin_msg"
   60–90 words. A human-first LinkedIn DM — NOT a pitch in disguise.

   HORMOZI RULE: The first DM is not about the content. It is about being human.

   Step 1 — OPENER (per spec): the school line. On LinkedIn this lands harder than anywhere else,
   because a student messaging a business owner here is unmistakably not an agency. Say it plainly.

   Step 2 — HOOK (per spec): the why-them sentence. This line is ONLY about them. No product, no ask.

   Step 3 — BRIDGE (1 sentence): {persona.WHAT_IT_DOES}. Then the PROOF clause (per spec).

   Step 4 — CTA (per spec): the conversation ask plus the feedback reason, nothing more.
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

   OPENER (per spec) → HOOK (per spec) → then the angle below.

   AGITATE (2 sentences): Frame the operational loss in their words. "Patients", "enquiries", "bookings", "WhatsApp messages at 11pm".
   Do NOT mention technology. Make the missed-call cost concrete in their workflow.

   OUTCOME (2 sentences): A single concrete operational promise. Pick ONE:
     - "Never miss a new-patient call after 8pm."
     - "Front desk backup during staff leave and lunch hours."
     - "Bookings captured for Sunday and late-night enquiries."
     - "WhatsApp continuity for after-hours questions."
   Then quantify the typical impact in their niche/city using "~" estimates.

   PROOF (per spec)

   CTA (per spec) — offer the demo in their language (English / Hinglish if applicable).

   Sign off: — Shaurya
   Hard rules: no bullets, no bold, no emojis. 120–170 words. In THIS field only, never use the words
   "AI", "automation", "agent", "bot", "solution" — describe the behaviour instead. The OPENER and CTA
   specs still apply in full.
   "I" = Shaurya, "you" = business owner.

9. "email_body_question"
   Angle: BREVITY AS RESPECT. The shortest version of the frame. Four lines, no argument, no agitation.
   This is designed to get a reply, not to pitch.

   LINE 1 (OPENER, per spec): the school line. Short.

   LINE 2 (HOOK, per spec): the why-them sentence. One line.

   LINE 3 (WHAT + PROOF): what it does and the proof clause, in one sentence if it fits.
   Example shape: "I built {persona.WHAT_IT_DOES} — {persona.proof_line()}."

   LINE 4 (CTA, per spec): the conversation ask plus the feedback reason.

   Sign off: — Shaurya

   Hard rules: 4 lines + sign-off. Under 70 words. No paragraphs. No agitation section.
   No bold, no bullets, no emojis. "I" = Shaurya. This respects the reader's time more than any other format.

10. "instagram_msg"
   A VERY informal Instagram DM — this is the opposite of the LinkedIn voice. Think one creator
   sliding into another small-business owner's DMs, NOT a salesperson.

   Tone: lowercase, casual, texting-not-emailing. Contractions, the odd filler ("ngl", "honestly",
   "btw") used sparingly and naturally. 1–2 tasteful emojis MAX (optional — skip if it feels forced).
   It should read like a real human typed it on their phone, not a marketer.

   Structure (keep it tiny — 4 short lines, under 60 words total). The OPENER/HOOK/PROOF/CTA specs
   all still apply here; they just get said the way a 16-year-old types, not the way an email reads:
   - line 1 (OPENER): the school line, casually. "hey, i'm {persona.SENDER_NAME}, i'm at {persona.SCHOOL_SHORT}
     and i'm doing a school project"
   - line 2 (HOOK): the why-them line. THIS IS THE ONE THAT MATTERS ON INSTAGRAM. If the hooks section
     gives you something about their page, their reels or their posts, say it here like a person would
     ("ur reels are actually clean, whoever edits them knows what they're doing"). If the hooks section
     says none are available, do NOT say anything about their page — you have not seen it. Use the
     category-and-place line instead.
   - line 3 (WHAT + PROOF): what it does, plainly, then the proof clause. ("i built a thing that picks up
     the clinic phone when no one can + books the appointment. {persona.proof_line()}")
   - line 4 (CTA): the conversation ask plus the feedback reason, typed casually.
     ("could i talk to u about it sometime? jus want some feedback on it tbh")

   Hard rules: NO sign-off, NO "Dear", NO "I hope this finds you", NO corporate words ("solution",
   "leverage", "reach out", "opportunity"). NO links. Never write more than 4 lines. Never claim to have
   seen anything the hooks section did not give you.

11. "whatsapp_msg"
   The SECOND WhatsApp touch: what Shaurya types back after the prospect replies
   to the opening template. This is a reply inside a live chat, not a broadcast.

   Assume the whole context is: touch 1 already told them he is a {persona.SCHOOL_SHORT}
   student doing a school project and asked if he could show them something, and
   they answered something short like "yes" or "what is this".

   Because touch 1 already carried the OPENER, do NOT repeat the school line here.
   This message is WHAT + PROOF + CTA only.

   Tone: a person on their phone. Contractions. Plain words. Sentences a builder
   would type, not sentences a marketer would write. It is fine to start with a
   lowercase word. It is fine for one sentence to be three words long.

   Structure (2 to 3 short sentences, under 45 words TOTAL):
   - what he actually does, in their vocabulary, tied to {industry} and {company}
     if it fits naturally ("i set up the phone line so it picks up when nobody
     can get to it and books the slot")
   - the PROOF clause (per spec), said flatly, then that he can set a free demo up
     on their details too ("can set one up on ur clinic's details for free")
   - the CTA (per spec): the conversation ask plus the feedback reason
     ("can i talk to u about it sometime? jus want some feedback on it")

   Hard rules: NO greeting (the chat is already open), NO sign-off, NO name at
   the end, NO links, NO bullet points, NO emojis, no "AI", no "automation", no
   "solution", no "reach out". Never use an em dash. Never write three items in
   a comma list. Do not restate their reply back to them. Do not ask for a call
   or a meeting here — that is the next message, not this one.

   The test: if you read it out loud and it sounds like it was written, rewrite it.

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
            import re as _re
            # Strip trailing spaces the model leaves before each break. Without
            # this a "line" is "text \n", which reads as ragged whitespace in
            # Gmail and defeats the newline collapsing below (the run is
            # " \n \n", not "\n\n").
            body = "\n".join(line.rstrip() for line in body.split("\n"))
            # Collapse triple+ newlines to double
            body = _re.sub(r'\n{3,}', '\n\n', body).strip()
            data[field] = body

    # DM copy gets the deterministic humanize pass here, at the point of
    # generation, so every downstream sender (WhatsApp, Instagram, the manual
    # review sheet) sees the same cleaned text. The model writes the substance;
    # this strips the em dashes, the corporate vocabulary and the email closers
    # it cannot stop reaching for. See modules/humanize.py.
    # Defensive like _learned(): a fault in the cleanup pass must never destroy
    # an otherwise-valid generation we have already paid for.
    try:
        from modules import humanize as _humanize

        for field, lowercase in (("instagram_msg", True), ("whatsapp_msg", False)):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                found = _humanize.tells(value)
                if found:
                    log.debug("%s carried generated-text tells %s, cleaning", field, found)
                data[field] = _humanize.humanize(value, lowercase_opener=lowercase)
    except Exception as e:
        log.warning("humanize pass skipped, keeping raw DM copy: %s", e)

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
