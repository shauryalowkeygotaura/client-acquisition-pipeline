"""Pattern-interrupt subject lines, each paired with a one-line apology.

Shaurya's call (2026-11-20): a clinic owner's inbox is a flood, and a polite
subject drowns in it. So the subject is deliberately odd or funny, and the
first line of the body owns it straight away ("Sorry for the subject line..."),
which turns the bait into a joke he is in on instead of a trick.

Hard limits, because the cost of a bad subject is a phishing report:
  * weird or funny, NEVER fake-alarming. No "complaint", "urgent", "payment",
    "invoice", "legal", "account", "refund", "verify", "scam", "free".
  * nothing untrue about the clinic or about Shaurya (no age, no teacher, no
    competition: persona.py truth rules still apply).
  * lowercase, no punctuation tricks, no "RE:" / "FWD:" fakes.
  * the apology always comes BEFORE the school line, as its own paragraph.

Call-check pairs are only used when the clinic really rang out after hours.
"""
from __future__ import annotations

import random
import re

# (subject, apology). {short} = the clinic's short name, lowercased in subjects.
GENERAL: list[tuple[str, str]] = [
    ("i may have built {short} a robot",
     "Sorry for the subject line. I needed you to open this, and it's mostly true."),
    ("this is either genius or homework",
     "Sorry for the subject. Honestly, it's a bit of both."),
    ("who picks up {short}'s phone at 9pm",
     "Sorry for the subject line. I genuinely wanted to know."),
    ("{short}'s phone has a secret",
     "Sorry for the dramatic subject. The secret is the calls that come in after you close."),
    ("a student, a phone, and {short}",
     "Sorry, that subject sounds like the start of a bad joke. It isn't one."),
    ("please read this before your receptionist does",
     "Sorry for the subject. Your receptionist is safe, I promise."),
    ("my school project wants to meet {short}",
     "Sorry for the weird subject. It's less creepy than it sounds."),
    ("the 9pm patient nobody talks about",
     "Sorry for the subject line. It was the only way to get past the other hundred emails."),
    ("i lost a bet to an answering machine",
     "Sorry for that subject. There was no bet, but there is an answering machine problem."),
    ("{short} but make it answer at midnight",
     "Sorry for the subject. I couldn't think of a normal way to say this."),
]

# Only when call_check says the clinic rang out after hours. True by construction.
CALL_CHECK: list[tuple[str, str]] = [
    ("i called {short} and the phone won",
     "Sorry for the subject. It's true though, let me explain."),
    ("{short} ghosted me (it's fine)",
     "Sorry for the subject line. You didn't really ghost me, I promise this makes sense."),
    ("i rang {short} at night and learned something",
     "Sorry for the subject. I'll keep the rest short."),
]

_BANNED = re.compile(
    r"complaint|urgent|payment|invoice|legal|account|refund|verify|scam|free|"
    r"winner|offer|discount|\bre:|\bfwd:", re.I)


def pick(short: str, rang_out: bool, rng: random.Random | None = None) -> tuple[str, str]:
    """Return (subject, apology_line) for one lead."""
    rng = rng or random
    pool = CALL_CHECK + GENERAL if rang_out else GENERAL
    # Call-check pairs are the strongest when they apply: prefer them 2:1.
    if rang_out and rng.random() < 0.67:
        pool = CALL_CHECK
    subject_t, apology = rng.choice(pool)
    name = short if short and short != "your clinic" else "your clinic"
    subject = subject_t.format(short=name.lower())
    return subject, apology


def all_pairs() -> list[tuple[str, str]]:
    return GENERAL + CALL_CHECK


def is_safe(subject: str) -> bool:
    return not _BANNED.search(subject or "")
