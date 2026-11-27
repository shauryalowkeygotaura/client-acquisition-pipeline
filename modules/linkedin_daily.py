"""Daily LinkedIn queue: 5 clinics, a profile link and a fun, personal message.

Shaurya sends these BY HAND from the Command Center DESK tab (LinkedIn view).
Nothing here touches LinkedIn: no login, no automation, no ban risk.

Who gets picked: the OSM pool is mostly clinics with no email, so email can
never reach them. LinkedIn can. Order of preference:
  1. never contacted at all (no email sent, no LinkedIn yet)
  2. emailed, no reply, no LinkedIn yet
Never: dead, opted out, bounced, replied, handed off, off-target.

Profile link: a best-guess linkedin.com/in/ URL from a DuckDuckGo search when
one is found (labelled "guess" so he checks the name before sending), else a
LinkedIn people-search link that always works.

Free LinkedIn accounts get only a handful of personalised connection notes a
month, so each item carries two texts: a <=280-char NOTE for the connection
request, and a longer MESSAGE to send once they accept.

Storage (no new sheet columns): linkedin_url = the link, linkedin_msg =
NOTE + "\n---\n" + MESSAGE, linkedin_sent = "QUEUED YYYY-MM-DD" (so a clinic is
never queued twice). modules/desk.py reads these into the encrypted payload.
"""
from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from modules import bait, lead_gate, names, persona

log = logging.getLogger(__name__)

DAILY_N = int(os.getenv("LINKEDIN_DAILY_N", "0"))  # 2026-11-27: off, people queue replaces it
NOTE_MAX = 280
SEP = "\n---\n"
_PROFILE = re.compile(r"^https://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]{3,100}/?$")
_DDG_LINK = re.compile(r'href="(?:https:)?//duckduckgo\.com/l/\?uddg=([^"&]+)')
_DEAD = {"dead", "booked", "closed", "handoff"}


def _eligible(lead: dict) -> bool:
    if not lead.get("slug") or (lead.get("linkedin_sent") or "").strip():
        return False
    if lead.get("opted_out") == "yes" or lead.get("status") in ("bounced", "off_target"):
        return False
    if lead.get("reply_status") or lead.get("conversation_stage") in _DEAD:
        return False
    return lead_gate.check(lead)[0]


def pick(leads: list[dict], n: int = DAILY_N, today: str | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pool = [l for l in leads if _eligible(l)]
    rng = random.Random(today)  # same picks if the run repeats the same day
    rng.shuffle(pool)
    pool.sort(key=lambda l: l.get("email_sent") == "TRUE")  # uncontacted first (stable)
    return pool[:n]


def _city(lead: dict) -> str:
    return (lead.get("location") or "").split(",")[0].strip()


def search_link(lead: dict) -> str:
    q = f"{names.short_name(lead.get('company_name', ''))} {_city(lead)}".strip()
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(q)


def find_profile(lead: dict, timeout: int = 15) -> str | None:
    """Best-guess profile URL via DuckDuckGo's HTML results. None on any doubt."""
    short = names.short_name(lead.get("company_name", ""))
    if short == "your clinic":
        return None
    q = f'site:linkedin.com/in "{short}" {_city(lead)}'
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (pipeline)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            page = r.read(400_000).decode("utf-8", "ignore")
    except Exception as e:
        log.info("profile search failed for %s: %s", short, e)
        return None
    for raw in _DDG_LINK.findall(page):
        cand = urllib.parse.unquote(html.unescape(raw)).split("?")[0]
        if _PROFILE.match(cand):  # whitelist: only a clean /in/ URL survives
            return cand
    return None


_PROMPT = """Write ONE opening for a LinkedIn message to the owner of {short} ({niche}, {city}).

It is two short sentences:
1. one odd, funny line about {short} or its phone that makes them stop scrolling
2. a few words apologising for that line

Like these (vary, never copy):
{examples}

LinkedIn has NO subject line: apologise for the odd opener, never mention a "subject".
Never fake-alarming (no "urgent", "complaint", "payment", "account"). Say nothing else:
no name, no school, no product, no offer (code adds those). No invented facts. No em dashes.
No emojis. Under 140 characters total. Normal capitalisation.

Return JSON: {{"opener": "..."}}
"""

_SUBJ = re.compile(r"\b(?:the |that )?subject(?: line)?\b", re.I)


def _no_subject(text: str) -> str:
    """The bait examples are written for email; LinkedIn has no subject."""
    return _SUBJ.sub("that opener", text)


def _fit(text: str, limit: int) -> str:
    """Cut at the last full sentence inside the limit."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    return head[:cut + 1] if cut > 0 else head.rsplit(" ", 1)[0]


_FALLBACK_OPENERS = [
    "Odd question: who picks up {short}'s phone at 9pm? Sorry, strange way to say hi.",
    "I may have built {short} a robot. Sorry, that's a weird first line.",
    "This is either genius or homework. Sorry, honestly it's a bit of both.",
]


def _opener(lead: dict, short: str) -> str:
    key = os.getenv("GROQ_API_KEY")
    fallback = random.choice(_FALLBACK_OPENERS).format(short=short)
    if not key:
        return fallback
    try:
        from openai import OpenAI
        from config import LLM_BASE_URL, LLM_MODEL
        examples = "\n".join(f'  "{s_.format(short=short.lower())}" + "{ap}"'
                             for s_, ap in random.sample(bait.GENERAL, 3))
        prompt = _PROMPT.format(short=short, niche=lead.get("niche") or "clinic",
                                city=_city(lead) or "India", examples=examples)
        r = OpenAI(api_key=key, base_url=LLM_BASE_URL).chat.completions.create(
            model=LLM_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.9, max_tokens=150, response_format={"type": "json_object"})
        text = (json.loads(r.choices[0].message.content or "{}").get("opener") or "").strip()
        text = _no_subject(text.replace("—", ",").replace("–", ","))
        if not text or len(text) > 180 or not bait.is_safe(text):
            raise ValueError(f"unusable opener: {text!r}")
        # Sentence-case it: the model drifts into all-lowercase texting style.
        text = re.sub(r"\bi\b", "I", text)
        text = re.sub(r"(^|[.?!]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
        return text
    except Exception as e:
        log.warning("linkedin opener failed for %s (%s); using a template", short, e)
        return fallback


def write_messages(lead: dict) -> tuple[str, str]:
    """(note, message). Only the funny opener is model-written; every fact is code.

    A test generation (2026-11-27) inverted the guarantee again ("if it misses a
    patient, I turn it off"), so the offer is never left to the model.
    """
    short = names.short_name(lead.get("company_name", ""))
    opener = _opener(lead, short)
    me = f"I'm {persona.SENDER_NAME}, a student at {persona.SCHOOL_SHORT}."
    note = _fit(f"{opener} {me} For a school project I built something that answers "
                f"clinic calls after hours. Can I send you the details?", NOTE_MAX)
    if len(note) > NOTE_MAX or "?" not in note:  # very long opener: drop it, keep the ask
        note = f"{me} I built something that answers clinic calls after hours. Can I send you the details?"
    message = (
        f"{opener}\n\n"
        f"{me} For a school project I built a voice agent that picks up the clinic phone "
        f"when nobody can, answers the usual questions and books the appointment.\n\n"
        f"I can run it free at {short} for one week, on after-hours calls only. "
        f"Your staff dial one code to turn it on and one to turn it off. "
        f"If it doesn't catch a single patient, I switch it off. That's it.\n\n"
        f"It's free because I need a real clinic to test it on.\n\n"
        f"Can I set it up for {short} this week? If not, no worries."
    )
    return note, message


def queue(leads: list[dict] | None = None) -> int:
    """Pick today's clinics, write their link + texts to the sheet. Returns count."""
    from modules import sheets_writer
    if leads is None:
        leads = sheets_writer.get_all_leads()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any((l.get("linkedin_sent") or "") == f"QUEUED {today}" for l in leads):
        return 0  # already queued today (second run of the day)
    done = 0
    for lead in pick(leads, today=today):
        slug = lead["slug"]
        profile = find_profile(lead)
        link = profile or search_link(lead)
        note, msg = write_messages(lead)
        try:
            sheets_writer.update_field(slug, "linkedin_url", link)
            sheets_writer.update_field(slug, "linkedin_msg", note + SEP + msg)
            sheets_writer.update_field(slug, "linkedin_sent", f"QUEUED {today}")
            lead.update(linkedin_url=link, linkedin_msg=note + SEP + msg,
                        linkedin_sent=f"QUEUED {today}")
            done += 1
        except Exception as e:
            log.error("could not queue %s for LinkedIn: %s", slug, e)
    return done


def desk_rows(leads: list[dict], days: int = 3) -> list[dict]:
    """Queued items from the last `days` days, newest first, for the DESK tab."""
    now = datetime.now(timezone.utc).date()
    rows = []
    for l in leads:
        tag = l.get("linkedin_sent") or ""
        if not tag.startswith("QUEUED "):
            continue
        try:
            day = datetime.strptime(tag[7:17], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (now - day).days > days:
            continue
        note, _, msg = (l.get("linkedin_msg") or "").partition(SEP)
        link = l.get("linkedin_url") or ""
        rows.append({
            "company": names.short_name(l.get("company_name", "")),
            "city": _city(l), "niche": l.get("niche") or "",
            "link": link, "link_type": "profile" if "/in/" in link else "search",
            "note": note, "message": msg, "queued": day.isoformat(),
            "emailed": l.get("email_sent") == "TRUE",
        })
    return sorted(rows, key=lambda r: r["queued"], reverse=True)
