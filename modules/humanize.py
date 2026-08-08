"""
Make short-form outreach read like a person typed it on a phone.

WHY THIS EXISTS
---------------
Email can survive sounding written. WhatsApp and Instagram cannot. A DM that
arrives with an em dash, a balanced three-item list and a "Let me know if you
have any questions!" sign-off is read as a blast in about a second, and the
thread is dead before the offer is even parsed.

Two problems were producing exactly that:

  1. `whatsapp.send_pitch` sent ONE hardcoded, grammatically perfect sentence
     to every lead: "I build voice agents for {niche} businesses. They answer
     calls and handle bookings automatically. Want me to send a 2-min clip?"
     Three clauses, no name, no context, identical across every prospect, and
     fired the instant their reply hit the webhook. Nobody types like that, and
     nobody types it in under a second.
  2. `instagram.send` fell back to `linkedin_msg` whenever `instagram_msg` was
     missing, so a chunk of IG DMs were LinkedIn-register copy in a texting
     window. `instagram_msg` was also absent from REQUIRED_FIELDS, so it went
     missing silently.

This module is the deterministic last pass before anything sends. It is NOT a
model call: the LLM writes the copy, this strips the tells the LLM cannot help
adding, and it does so identically every run so the behaviour is testable.

Use `humanize()` for the text and `bubbles()` for the send shape. `tells()`
reports what fired, for logging and for the tests.
"""

from __future__ import annotations

import random
import re

__all__ = ["humanize", "bubbles", "tells", "typing_delay_seconds", "reply_delay_seconds"]


# ── 1. Vocabulary that marks a message as written-by-a-marketer ─────────────
# Whitelist thinking does not apply here: this is a rewrite pass over our OWN
# copy, not validation of untrusted input. Replacements are plain-English, and
# each is a phrase a real person would actually type instead.
_PHRASE_SWAPS: list[tuple[str, str]] = [
    (r"\bI hope this (?:email |message )?finds you well\b[.,!]?\s*", ""),
    (r"\bI wanted to reach out\b", "thought I'd message"),
    (r"\bjust wanted to reach out\b", "thought I'd message"),
    (r"\breach out\b", "message"),
    (r"\breaching out\b", "messaging"),
    (r"\bcircle back\b", "follow up"),
    (r"\btouch base\b", "check in"),
    (r"\bleverage\b", "use"),
    (r"\butili[sz]e\b", "use"),
    (r"\bstreamline\b", "speed up"),
    (r"\bseamless(?:ly)?\b", "smooth"),
    (r"\brobust\b", "solid"),
    (r"\bcutting[- ]edge\b", "new"),
    (r"\bstate[- ]of[- ]the[- ]art\b", "new"),
    (r"\bsynerg(?:y|ies|istic)\b", "overlap"),
    (r"\brevolutioni[sz]e\b", "change"),
    (r"\bempower\b", "let"),
    (r"\bdelve into\b", "look at"),
    (r"\bin today's fast[- ]paced\b", "in a busy"),
    (r"\bAI[- ]powered solution\b", "setup"),
    (r"\bsolution\b", "setup"),
    (r"\boptimi[sz]e\b", "improve"),
    (r"\bunlock\b", "get"),
    (r"\belevate\b", "lift"),
    (r"\bgame[- ]changer\b", "big deal"),
    (r"\bat your earliest convenience\b", "whenever"),
    (r"\bplease do not hesitate to\b", "feel free to"),
    (r"\bkindly\b", "please"),
]

# ── 2. Closings and openers that belong in email, never in a DM ─────────────
_DEAD_CLOSERS = [
    r"\blet me know if you have any questions[.!]?",
    r"\blooking forward to (?:hearing from you|your response)[.!]?",
    r"\bthanks in advance[.!]?",
    r"\bbest regards[,.]?",
    r"\bwarm regards[,.]?",
    r"\bkind regards[,.]?",
    r"\bregards[,.]?",
    r"\bsincerely[,.]?",
    r"\bcheers[,.]?\s*$",
    r"^\s*[-–—]\s*Shaurya\s*$",
]

_DEAD_OPENERS = [
    r"^\s*dear\s+[^,\n]{0,40},?\s*",
    r"^\s*to whom it may concern,?\s*",
    r"^\s*greetings,?\s*",
]

# Negative parallelism: "not just X, it's Y" / "not only X, but also Y".
# These read as generated more reliably than any single word does.
_PARALLELISM = re.compile(
    r"\b(?:it'?s\s+)?not (?:just|only)\b[^.!?]*?[,;]\s*(?:but(?: also)?|it'?s)\b",
    re.IGNORECASE,
)


# ── 2b. URLs are data, not prose ────────────────────────────────────────────
# Hyphens are regex word boundaries, so a `\bsolution\b` swap would happily
# rewrite the middle of https://…/demo/bright-smile-dental-solutions and send
# the prospect a 404. Every rewrite runs with URLs masked out and restored
# afterwards, so a link is byte-identical on the way in and on the way out.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# NUL cannot appear in generated copy, so the placeholder can never collide
# with real text, and it contains no word characters for a swap to bite on.
_URL_SLOT = "\x00u{}\x00"


def _mask_urls(text: str) -> tuple[str, list[str]]:
    found: list[str] = []

    def grab(match: re.Match[str]) -> str:
        found.append(match.group(0))
        return _URL_SLOT.format(len(found) - 1)

    return _URL_RE.sub(grab, text), found


def _restore_urls(text: str, urls: list[str]) -> str:
    for i, url in enumerate(urls):
        text = text.replace(_URL_SLOT.format(i), url)
    return text


def tells(text: str) -> list[str]:
    """
    Report which generated-text markers are present, without changing anything.

    Used by the tests and logged before each send, so a regression in the
    prompt shows up as a named tell rather than a vague drop in reply rate.
    """
    found: list[str] = []
    # Same masking as humanize(): a slug like /demo/dental-solutions is not the
    # copy sounding corporate, and reporting it as such trains you to ignore
    # this list.
    text, _ = _mask_urls(text)
    if re.search(r"[—–]", text):
        found.append("em-dash")
    if _PARALLELISM.search(text):
        found.append("negative-parallelism")
    if text.count("!") > 1:
        found.append("multiple-exclamations")
    for pattern, _ in _PHRASE_SWAPS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append("corporate-vocab")
            break
    for pattern in _DEAD_CLOSERS:
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            found.append("email-closer")
            break
    for pattern in _DEAD_OPENERS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append("email-opener")
            break
    if re.search(r"\b\w+, \w+,? and \w+\b", text):
        found.append("rule-of-three")
    if "  " in text:
        found.append("double-space")
    return found


def humanize(text: str, *, lowercase_opener: bool = False) -> str:
    """
    Rewrite `text` into something that reads as typed, not composed.

    `lowercase_opener` drops the capital on the first word. Instagram DMs read
    more natural that way; WhatsApp to a 45-year-old clinic owner does not, so
    it stays off by default and the caller opts in.
    """
    if not text:
        return ""

    # Links come out FIRST and go back in LAST. Nothing between these two lines
    # can touch a URL, which is the only way to guarantee the /demo/<slug> link
    # a prospect receives is the one we generated.
    out, urls = _mask_urls(text)

    # Em and en dashes never survive. An em dash in a DM is the single most
    # reliable generated-text tell, and it is a standing rule on this project.
    out = re.sub(r"\s*[—–]\s*", ", ", out)

    for pattern in _DEAD_OPENERS:
        out = re.sub(pattern, "", out, flags=re.IGNORECASE)
    for pattern, replacement in _PHRASE_SWAPS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    for pattern in _DEAD_CLOSERS:
        out = re.sub(pattern, "", out, flags=re.IGNORECASE | re.MULTILINE)

    out = _restore_urls(out, urls)
    # Belt and braces: a placeholder that somehow survived would ship a NUL to
    # the Meta API. Never let one leave this function.
    out = out.replace("\x00", "")

    # Keep at most one exclamation mark in the whole message; a second one is
    # where enthusiasm starts reading as a template.
    if out.count("!") > 1:
        first = out.find("!")
        out = out[: first + 1] + out[first + 1 :].replace("!", ".")

    # Real texting has single spaces and no trailing whitespace per line.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = "\n".join(line.rstrip() for line in out.splitlines())
    out = re.sub(r"\s+([,.?!])", r"\1", out)
    out = out.strip().strip(",").strip()

    if lowercase_opener and out:
        # Only the very first character, and only if the word is not a name or
        # an acronym: lowercasing "AI" or "Rimmi" reads as a typo, not as casual.
        first_word = out.split(" ", 1)[0].strip(",.?!")
        if first_word and not first_word.isupper() and first_word[1:].islower():
            out = out[0].lower() + out[1:]

    return out


def bubbles(text: str, *, max_chars: int = 220, max_parts: int = 3) -> list[str]:
    """
    Split a message into the 2-3 short sends a person actually types.

    One 60-word paragraph landing in a single notification is a broadcast. The
    same words arriving as "hey, saw X" then "I do Y" then "want a clip?" is a
    conversation. Splits happen on sentence boundaries only, so no bubble ever
    ends mid-thought.

    DELIBERATE ASYMMETRY: `max_chars` binds every bubble EXCEPT the last, which
    absorbs whatever is left over. Overlong input is a copy problem (the
    generator is told to stay under 40 words) and the fix for it is upstream.
    Truncating here would drop the closing ask, and a pitch that arrives without
    its question is worse than a pitch whose final bubble runs long.
    """
    cleaned = humanize(text)
    if not cleaned:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", cleaned) if s.strip()]
    if not sentences:
        return [cleaned]

    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        # Start a new bubble when this one is full, unless we are already at the
        # last allowed part — the tail always stays together rather than being
        # truncated away.
        if current and len(candidate) > max_chars and len(parts) < max_parts - 1:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)

    return parts[:max_parts]


# ── 3. Timing: the tell that no amount of copy editing fixes ────────────────
# A reply that lands 400ms after the prospect's message is a bot, whatever it
# says. These return SECONDS and are deliberately jittered, so two leads never
# see the same cadence and no interval lands on a round number.

def typing_delay_seconds(text: str) -> float:
    """
    Plausible time to thumb-type `text`, at roughly 25 words per minute.

    Jitter is applied BEFORE the clamp, not after: clamping first and then
    multiplying by up to 1.3 puts the result back outside the bounds the
    function claims to guarantee.
    """
    words = max(1, len(text.split()))
    base = words / 25 * 60 * random.uniform(0.75, 1.3)
    return round(min(22.0, max(1.8, base)), 1)


def reply_delay_seconds(*, low: int = 45, high: int = 210) -> int:
    """
    How long to sit on an inbound reply before answering it.

    Nobody replies instantly and nobody replies in exactly two minutes. The
    default window is under four minutes, so the thread still feels attentive.
    """
    return random.randint(low, high)
