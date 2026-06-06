"""
modules/content_engine.py

The "post" half of the agent. Turns a (series, topic) into one platform-ready
post using Groq — free. The brand voice is configurable via env but defaults to
Shaurya's @revengine positioning ("survives contact with reality", not
teen-builder hype).

Groq prompt-caching: the long, static SYSTEM block is sent first and never
changes, so the cache prefix matches across every call; only the short user
message (the topic) varies. That roughly halves input cost.
"""
from __future__ import annotations

import logging
import os

from config import LLM_BASE_URL, LLM_MODEL

log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Brand voice — override any field with env vars without touching code.
BRAND = {
    "name": os.getenv("BRAND_NAME", "Revengine"),
    "handle": os.getenv("BRAND_HANDLE", "@revengine"),
    "who": os.getenv(
        "BRAND_WHO",
        "a builder shipping AI voice receptionists that survive contact with "
        "real clinics and small businesses",
    ),
    "audience": os.getenv("BRAND_AUDIENCE", "clinic owners and small-business operators"),
}

# Static system block — keep FIRST and unchanging for cache hits.
_SYSTEM = """
You write short social posts for a personal/product brand. Rules, always:
- Lead with one concrete, specific idea. No throat-clearing, no "in today's world".
- Plain language. No buzzwords, no hashtags unless asked, no emoji spam (1 max).
- Sound like a sharp human who actually builds things, not a marketer.
- Never use em dashes. Use commas, colons, or parentheses instead.
- 40-90 words unless the platform clearly wants shorter.
- Every post must carry one real, falsifiable detail (a number, a result, a
  specific failure, a concrete step). No vague inspiration.
- End with a light, genuine hook that invites a reply, not a hard CTA.
Banned patterns (stop-slop): "not X, it's Y" and "not only X but Y" contrasts
(state the point directly); "isn't just" anything; "Here's the thing" and any
"here's what/why" opener; "Let that sink in" / "Full stop" / "game-changer";
ending on a quotable one-liner that sounds like a pull-quote; adverb crutches
(honestly, literally, truly, actually); inanimate subjects doing human verbs
("the lesson emerges"): name who did what instead.
Return ONLY the post text. No preamble, no quotes around it, no explanation.
""".strip()

_USER_TMPL = """
Brand: {name} ({handle}) — {who}
Audience: {audience}
Series: {series}
Topic for this post: {topic}

Write one post.
""".strip()


def _client():
    from openai import OpenAI
    return OpenAI(api_key=GROQ_API_KEY, base_url=LLM_BASE_URL)


def generate_post(series: str, topic: str, client=None) -> str:
    """Generate one post for the given series + topic. Returns text (never
    raises): on any failure it returns a safe, honest fallback so the run
    continues."""
    fallback = f"{topic.strip().rstrip('.')}. More on how this actually held up in practice soon."
    if not GROQ_API_KEY and client is None:
        log.warning("GROQ_API_KEY not set — using fallback post")
        return fallback

    client = client or _client()
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _USER_TMPL.format(
                    name=BRAND["name"], handle=BRAND["handle"], who=BRAND["who"],
                    audience=BRAND["audience"], series=series, topic=topic,
                )},
            ],
            temperature=0.7,
            max_tokens=300,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or fallback
    except Exception as e:
        log.error("generate_post failed: %s — using fallback", e)
        return fallback
