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
# The literal number of live deployments. This is a factual claim made to
# strangers; keep it honest. 2 = the clinics currently running the agent.
CLIENT_COUNT = int(os.getenv("PROOF_CLIENT_COUNT", "2"))
CLIENT_NOUN = os.getenv("PROOF_CLIENT_NOUN", "clinics")


def proof_line() -> str:
    """The proof clause, in the sender's own voice. Singular-safe."""
    if CLIENT_COUNT <= 0:
        return ""  # no clients yet — say nothing rather than something
    noun = CLIENT_NOUN if CLIENT_COUNT > 1 else CLIENT_NOUN.rstrip("s")
    return f"{CLIENT_COUNT} {noun} are already using it"


# ── What the product actually is, in plain words ────────────────────────────
# Deliberately concrete. "AI voice agent" is a category; this is the behaviour.
WHAT_IT_DOES = (
    "a voice agent that picks up the clinic phone when nobody can get to it, "
    "answers the usual questions, books the appointment, and texts you the details"
)

# ── The offer ───────────────────────────────────────────────────────────────
# The free demo is mentioned as something he has, not as the thing he wants.
OFFER = "a free demo he can set up on their own clinic's details"

# The one thing touch 1 asks for. Deliberately NOT the demo: asking to send
# something puts the burden on them to receive and judge it. Asking to talk
# costs them a yes and nothing else, and the feedback framing below is what
# makes the yes cheap — he is not asking them to buy, evaluate, or commit.
TOUCH_1_ASK = "a short conversation about it, sometime"

# Why he wants the conversation. This is the load-bearing half of the ask: a
# student asking for feedback is a request the recipient can grant for free and
# feel good about. A student asking for a sale is the thing they were bracing
# for. Never drop it — without it the ask reads as a disguised meeting request.
FEEDBACK_FRAME = "he just wants feedback on it from someone who'd actually know"

# What happens after they reply. The reply handler owns this half.
POST_REPLY_GOAL = "book a short meeting"


# ── The block that goes at the top of every generation prompt ───────────────
# Kept as one module-level constant (not an f-string built per lead) so Groq's
# automatic prompt caching can match it as a static prefix across every lead in
# a run. Anything lead-specific must be interpolated BELOW this block by the
# caller. See modules/generator.py.
SENDER_FRAMING = f"""
SENDER: {SENDER_NAME}, a school student at {SCHOOL} in {SENDER_CITY}. Not a company, not an agency, not a freelancer.
FRAME: He is reaching out about a school project. That is literally true and it is the first thing he says. He built {WHAT_IT_DOES}.
PROOF: {proof_line()}. This is a real number. Never inflate it, never say "many", never say "several businesses", never imply a client list.
OFFER: {OFFER}. Free means free — there is no trial, no card, no fee mentioned anywhere on the first touch. The demo is mentioned as something he HAS, never as the thing he is asking for.
THE ONLY ASK ON TOUCH 1: {TOUCH_1_ASK} — and the reason given is that {FEEDBACK_FRAME}.
  Phrase it loosely and low-stakes: "can I talk to you about this sometime?" is the shape. "Sometime" is
  doing real work — it asks for willingness, not a slot, so there is nothing to check a calendar for.
  NOT a specific time, NOT a duration ("15 minutes"), NOT a calendar link, NOT "hop on a call", NOT
  "schedule". Those are what make a stranger's ask feel expensive, and they come later anyway.
  The feedback reason is NOT optional. Without it this reads as a disguised sales meeting, which is
  the exact thing the student frame exists to avoid.

HOW THE STUDENT FRAME MUST SOUND:
- Say the school by name early. "{SCHOOL_SHORT}" or "{SCHOOL}" — both fine, whichever scans better in the sentence.
- Say "school project" plainly. Do not dress it up as "a research initiative", "a study", "a startup", or "a venture".
- Do not apologise for being a student and do not perform being a student. No "I know I'm just a kid", no "sorry to bother you", no cutesy tone.
- Do not claim a grade, a rank, a competition, a teacher, a mentor, or a deadline that was not given to you. If you weren't told it, it doesn't go in the message.
- Confidence without seniority: he built a working thing and is showing it to someone who'd know if it's any good.
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
