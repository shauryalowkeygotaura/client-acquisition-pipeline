"""
modules/persona.py — who the outreach is FROM, in one place.

Every channel (email, Instagram DM, WhatsApp, LinkedIn, reply handler) used to
carry its own copy of the sender framing. They drifted: the email prompt said
"22-year-old developer", the WhatsApp fallback said nothing at all, and the
reply handler opened with a third variant. A prospect who got two channels saw
two different people.

This module is the single source of truth. Change the story here and it changes
everywhere.

── The student frame (2026-08) ───────────────────────────────────────────────
The pitch is no longer "vendor sells software to business". It is "school
student built something and wants a real business to try it". That is a
different psychological transaction:

  * a vendor asking for time owes you a reason; a student asking for feedback
    is granted one for free
  * a 16-year-old with a working product is interesting; a 22-year-old
    freelancer with the same product is a cold email
  * "would you look at my school project" has no commercial threat in it, so
    it does not trip the "here comes a pitch" reflex on line one

The proof and the offer still do the closing work. The frame only buys the
read.

TRUTH RULES — these are claims about the real world and they are enforced here,
not left to the model:
  * CLIENT_COUNT is the number of businesses ACTUALLY running the agent today.
    If that number changes, change it here (or set PROOF_CLIENT_COUNT). Never
    let the copy round it up.
  * The school is real and checkable. Do not let generated copy embellish it
    (no "top of my class", no "for my board exams", no invented competition).
  * "I've seen your reels" is only ever said when a real Instagram handle was
    found on the lead's own website. See modules/personalizer.py — the hook is
    built from evidence, and when there is no evidence the line does not exist.
"""
from __future__ import annotations

import os

# ── Identity ────────────────────────────────────────────────────────────────
SCHOOL = os.getenv("PERSONA_SCHOOL", "DPS RK Puram")
SCHOOL_SHORT = os.getenv("PERSONA_SCHOOL_SHORT", "DPS RKP")
SENDER_NAME = os.getenv("PERSONA_NAME", "Shaurya")
SENDER_CITY = os.getenv("PERSONA_CITY", "Delhi")

# ── Proof ───────────────────────────────────────────────────────────────────
# The literal number of businesses running the agent TODAY. This is a factual
# claim made to strangers. Default is 0 (2026-11-20): a warm lead asked "which
# 2 clinics?" and there was no name to give, which cost more than the claim
# ever earned. Raise it only when a clinic would confirm it if called.
CLIENT_COUNT = int(os.getenv("PROOF_CLIENT_COUNT", "0"))
CLIENT_NOUN = os.getenv("PROOF_CLIENT_NOUN", "clinics")


def proof_line() -> str:
    """The proof clause, in the sender's own voice. Singular-safe."""
    if CLIENT_COUNT <= 0:
        return ""  # no clients yet: say nothing rather than something
    noun = CLIENT_NOUN if CLIENT_COUNT > 1 else CLIENT_NOUN.rstrip("s")
    verb = "are" if CLIENT_COUNT > 1 else "is"
    return f"{CLIENT_COUNT} {noun} {verb} already using it"


# ── What the product actually is, in plain words ────────────────────────────
# Deliberately concrete. "AI voice agent" is a category; this is the behaviour.
WHAT_IT_DOES = (
    "a voice agent that picks up the clinic phone when nobody can, "
    "answers the usual questions, and books the appointment"
)

# ── The offer (Hormozi: nothing to lose, no reason to say no) ───────────────
# Every risk the owner could raise is removed inside the offer itself:
#   cost      -> free for the week
#   effort    -> he sets it up; staff dial one code to switch it on
#   risk      -> after-hours calls only, so the day desk is untouched
#   exit      -> one code switches it off, no call to anyone needed
#   outcome   -> if it catches nobody, he turns it off and that's the end
# And the student frame is the honest REASON it is free: he needs a real
# clinic to test it on for his school project. That reason is what stops a
# free offer reading as a trap. All of this is true of the locked 7-day trial
# (Projects/dental-receptionist, Rs.0 trial, ##21# kill switch).
OFFER = (
    "he sets it up on their clinic's details for free, for one week, on after-hours "
    "calls only. Their staff dial one code to switch it on and one code to switch it off. "
    "If it doesn't catch a single patient they'd have missed, he turns it off and that's it"
)
OFFER_REASON = "he needs a real clinic to test it on for his school project"

# The one thing touch 1 asks for: a yes/no about running the free week.
TOUCH_1_ASK = "whether he can set it up for their clinic this week"

# Kept for callers that still read it; now the honest reason behind the offer.
FEEDBACK_FRAME = OFFER_REASON

# What happens after they reply. A human (Shaurya) owns this half.
POST_REPLY_GOAL = "set up the free week"


# ── The block that goes at the top of every generation prompt ───────────────
# Kept as one module-level constant (not an f-string built per lead) so Groq's
# automatic prompt caching can match it as a static prefix across every lead in
# a run. Anything lead-specific must be interpolated BELOW this block by the
# caller. See modules/generator.py.
SENDER_FRAMING = f"""
SENDER: {SENDER_NAME}, a school student at {SCHOOL} in {SENDER_CITY}. Not a company, not an agency, not a freelancer.
FRAME: For a school project, he built {WHAT_IT_DOES}. That is literally true and it comes first.
PROOF: {proof_line() or "NONE. Make no claim about anyone using it. Do not hint at users, clinics, or results."}
OFFER: {OFFER}.
WHY IT IS FREE: {OFFER_REASON}. Always say this reason. It is what makes a free offer believable.
THE ONLY ASK: {TOUCH_1_ASK}. A yes/no question. A "yes" is the whole reply he needs.
  NOT a call, NOT a meeting, NOT a time, NOT a calendar link, NOT "hop on". He does the work, not them.

HOW IT MUST SOUND:
- Short sentences. Most under 10 words. Never over 15. One idea per sentence.
- Say the school by name early. "{SCHOOL_SHORT}" or "{SCHOOL}".
- Say "school project" plainly. No "research initiative", "startup", "venture".
- No apologising, no cutesy tone, no "sorry to bother you".
- No pressure. Saying no must feel as easy as saying yes. One calm line like "If not, no worries." is enough.
- Never claim a grade, rank, competition, teacher or deadline you were not given.
"""

# ── Guard rails shared by every channel ─────────────────────────────────────
# Things the model reaches for that break the frame instantly.
FRAME_BREAKERS = """
NEVER WRITE THESE (they break the student frame and read as an agency):
- "our team", "we built", "we help", "we work with" — there is no we. It is one person. Always "I".
- "clients", "customers", "portfolio", "case study", "onboarding", "packages", "pricing"
- "I'd love to", "I wanted to reach out", "I hope this finds you", "just following up"
- "revolutionise", "transform", "streamline", "leverage", "solution", "AI-powered", "cutting-edge"
- any promise of revenue, growth, or ROI percentages
- any urgency device: "limited slots", "this week only", "before I close applications"
"""
