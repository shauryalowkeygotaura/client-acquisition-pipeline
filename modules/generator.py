import json
import logging
import os
import random
import re

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL
from modules import bait, names, persona

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
THE SHAPE OF EVERY FIRST TOUCH (email, DM, WhatsApp):
1. WHO: the school line. "I'm {persona.SENDER_NAME}, a student at {persona.SCHOOL_SHORT}." Line one, always.
2. WHY THEM: one short, true line about THIS clinic. Use the CALL CHECK if one is given (it is the
   strongest line you have). Else the PERSONALIZATION HOOKS. Else category + place. Never invent.
3. WHAT: {persona.WHAT_IT_DOES}. One short sentence. Plain words.
4. OFFER: {persona.OFFER}. Split it into 2 or 3 short sentences.
5. WHY FREE: {persona.OFFER_REASON}. One sentence.
6. ASK: {persona.TOUCH_1_ASK}. One yes/no question. Then one calm no-pressure line ("If not, no worries.").

SENTENCE RULES (hard):
- Most sentences under 10 words. None over 15.
- One idea per sentence. No semicolons. No em dashes. No "~" estimates. No percentages.
- No made-up numbers about their calls. The only number allowed is from the CALL CHECK.

WHAT NEVER APPEARS ON THE FIRST TOUCH:
- a link, a calendar, a meeting, a call request, a duration, "hop on", "schedule", "book a time"
- a price (the week is free, that is the only money word allowed)
- more than one question

── NAMED SPECS (fields below refer to these) ─────────────────────────────────
OPENER SPEC: line 1, the school line. Vary wording per field, keep both facts ({persona.SCHOOL_SHORT}, school project).
HOOK SPEC: line 2, the WHY THEM line from step 2.
PROOF SPEC: "{persona.proof_line()}". If empty, say NOTHING about other users. Never substitute a vaguer claim.
CTA SPEC: the yes/no ask from step 6, plus the no-pressure line. Last.
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

    short = names.short_name(company)
    call_check = (data.get("call_check") or "").strip()
    if call_check:
        hooks_section += (
            "\nCALL CHECK (verified: Shaurya really called this clinic himself):\n"
            f"{call_check}\n"
            "MANDATORY RULE: this IS the WHY THEM line in every email body and DM. Use the exact day "
            "and time given. Say it flatly, no drama, no blame (e.g. 'I called at 8:52pm on Tuesday. "
            "It rang out.'). Never add a number of calls, a percentage, or a guess about lost patients.\n"
        )

    # Static blocks first (cacheable prefix). Dynamic interpolations come AFTER.
    return f"""
{_ICP_PSYCHOGRAPHIC}

{_COPY_RULES_STATIC}

{_SENDER_FRAMING_STATIC}

{_STRUCTURE_STATIC}

---

RECIPIENT: The owner or office manager at {company}.
SHORT NAME: "{short}". In every message, call the clinic "{short}" (or "your clinic"). Never paste the full listing name.

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
   2 to 4 words. Lowercase. No punctuation.
   MUST contain the short name "{short}" OR the call-check day/time if one is given.
   Shape examples (vary, never copy): "{short} after 8pm", "called {short} tuesday", "{short} front desk",
   "student at {persona.SCHOOL_SHORT}, {short}".
   Never: the bare phrase "school project" on its own, "AI", "demo", "free", "offer", "quick question",
   or anything a newsletter would use.

3. "email_body_pain"
   Angle: the missed patient. Shape: OPENER, HOOK, then one sentence on who calls after hours
   (a patient in pain, someone who picks the next clinic on the list). Then WHAT (one sentence) and STOP. Do NOT write the offer, the reason it is free, the question or a sign-off: code appends those, word for word.
   Put each sentence on its own line. Blank line after the school line.
   Hard rules: 25 to 50 words. Short sentences per the SENTENCE RULES. No bullets, bold or emojis.

4. "email_body_curiosity"
   Angle: the calls they never see. Shape: OPENER, HOOK, then one sentence: nobody tells you
   about a call that rang out. Then WHAT (one sentence) and STOP. Do NOT write the offer, the reason it is free, the question or a sign-off: code appends those, word for word.
   Same hard rules as email_body_pain.

5. "email_body_roi"
   Angle: one patient. Shape: OPENER, HOOK, then one sentence: one new patient is worth more
   than the whole setup, and the setup is free. Then WHAT (one sentence) and STOP. Do NOT write the offer, the reason it is free, the question or a sign-off: code appends those, word for word.
   Same hard rules as email_body_pain. No rupee figures.

6. "linkedin_msg"
   60–90 words. A human-first LinkedIn DM — NOT a pitch in disguise.

   HORMOZI RULE: The first DM is not about the content. It is about being human.

   Step 1 — OPENER (per spec): the school line. On LinkedIn this lands harder than anywhere else,
   because a student messaging a business owner here is unmistakably not an agency. Say it plainly.

   Step 2 — HOOK (per spec): the why-them sentence. This line is ONLY about them. No product, no ask.

   Step 3 — BRIDGE (1 sentence): {persona.WHAT_IT_DOES}. Then the PROOF clause (per spec).

   Step 4 — CTA (per spec): the yes/no ask plus the school-project reason, nothing more.
   No "hop on a call", no "schedule a demo", no "let's connect", no "I'd love to".

   Tone: sounds typed at a desk, slightly casual. Under 90 words total.
   No "I hope", no "exciting opportunity", no buzzwords.

7. "linkedin_post"
   A LinkedIn post Shaurya can publish from his own profile. 70–100 words. First-person. No list format.
   Based on what you observed about this type of business (use {industry} and {location} as context — do NOT name {company} specifically).

   Structure:
   - Hook line (1 sentence, no label): A sharp observation about something you noticed while doing research. Make it specific — a number, a pattern, a tension. Must make someone stop scrolling.
   - Story (3–4 sentences): What you found, what it means, what's actually happening in this niche right now.
   - Insight (2 sentences): The non-obvious thing most people miss about this problem or this type of business.
   - Soft close (1 sentence): A question to the reader or a quiet CTA. Not "DM me." Not "link in bio."

   Tone: thoughtful, direct, slightly contrarian. Sounds like a builder observing the world — not a marketer selling something.
   No emojis, no hashtags, no bullet lists.

8. "email_body_outcome"
   Angle: what changes after 8pm. Shape: OPENER, HOOK, then one sentence naming the result in
   their words ("A patient who calls at 9pm gets a slot for tomorrow."). Then WHAT (one sentence) and STOP. Do NOT write the offer, the reason it is free, the question or a sign-off: code appends those, word for word.
   Never the words "AI", "automation", "bot", "solution" in this field.
   Same hard rules as email_body_pain.

9. "email_body_question"
   Angle: brevity. The shortest version. 3 lines, under 30 words.
   LINE 1 OPENER. LINE 2 HOOK. LINE 3 WHAT. Then STOP (code appends the offer and question).

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
   - line 4 (CTA): the yes/no ask plus the school-project reason, typed casually.
     ("can i set it up for ur clinic this week? free, need a real clinic for my project. if not no worries")

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
   - the CTA (per spec): the yes/no ask plus the school-project reason
     ("can i set it up on ur clinic this week? free, its for my school project")

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
            # Groq free tier caps qwen3.8-27b at 1000 OUTPUT tokens/minute and
            # rejects any request whose max_tokens is above that outright (429
            # "Requested 1207"). 5000 meant every generation could fail.
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1000")),
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


# The offer, the reason and the question are facts, so code writes them, not
# the model. On 2026-11-20 a test generation turned the guarantee upside down
# ("If it misses a patient, I turn it off"). The model only writes the
# personal head (school line, hook, angle); one of these closes every email.
_OFFER_BLOCKS = [
    "I can run it free at {short} for one week.\n"
    "Only on calls after closing.\n"
    "Your staff dial one code to turn it on, and one to turn it off.\n"
    "If it doesn't catch a single patient, I switch it off. That's it.\n\n"
    "It's free because I need a real clinic to test it on for my school project.\n\n"
    "Can I set it up for {short} this week? If not, no worries.",
    "I'd set it up for {short} free, for a week.\n"
    "It only takes calls after you close.\n"
    "One code switches it on. One code switches it off.\n"
    "If it catches nobody, I turn it off and that's the end of it.\n\n"
    "I need a real clinic to test it on. It's for my school project.\n\n"
    "Should I set it up this week? A no is completely fine.",
    "Free for one week at {short}.\n"
    "After-hours calls only, so your day desk doesn't change.\n"
    "Your staff can switch it off in five seconds with one code.\n"
    "If it doesn't book a single patient, I take it down.\n\n"
    "Why free: I need a real clinic to test it on for my school project.\n\n"
    "Can I set it up for you this week? If not, no worries.",
]


def _strip_tail(head: str) -> str:
    """Drop any sign-off or question the model wrote despite the spec."""
    lines = [l.rstrip() for l in (head or "").strip().split("\n")]
    while lines and (not lines[-1].strip() or "shaurya" in lines[-1].lower()
                     or lines[-1].strip().endswith("?") or "no worries" in lines[-1].lower()):
        lines.pop()
    return "\n".join(lines).strip()


def compose_email(head: str, company: str) -> str:
    short = names.short_name(company)
    offer = random.choice(_OFFER_BLOCKS).format(short=short)
    return f"{_strip_tail(head)}\n\n{offer}\n\n{persona.SENDER_NAME}"


_GENERIC_SUBJECTS = {"school project", "school project question", "quick question",
                     "the front desk", "following up", "hello", "hi"}


def _guard_subject(result: dict) -> str:
    """Never ship a subject that is identical across leads.

    ~200 threads went out as the bare "school project". Identical subjects from
    one sender to many strangers are a bulk-mail fingerprint, so a generic one
    is replaced with a lead-specific one built from the short clinic name.
    """
    subject = (result.get("email_subject") or "").strip().lower().strip(".!?")
    short = names.short_name(result.get("company_name", "")).lower()
    if subject and subject not in _GENERIC_SUBJECTS and short != "your clinic":
        return subject
    if short == "your clinic":
        return subject if subject and subject not in _GENERIC_SUBJECTS else "your front desk after 8pm"
    return random.choice([f"{short} after 8pm", f"{short} front desk",
                          f"{short} after hours", f"student at {persona.SCHOOL_SHORT.lower()}, {short}"])


def run(data: dict) -> dict:
    result = generate(data)
    email_body, variant_id = _select_variant(result)
    body = compose_email(email_body, result.get("company_name", ""))

    # Pattern-interrupt subject + one-line apology as the very first line
    # (modules/bait.py). BAIT_SUBJECTS=0 falls back to the plain guarded subject.
    if os.getenv("BAIT_SUBJECTS", "1") == "1":
        short = names.short_name(result.get("company_name", ""))
        rang_out = "rang out" in (result.get("call_check") or "").lower()
        subject, apology = bait.pick(short, rang_out)
        result["email_subject"] = subject
        body = f"{apology}\n\n{body}"
    else:
        result["email_subject"] = _guard_subject(result)

    result["email_body"] = body
    result["message_variant_id"] = variant_id
    return result
