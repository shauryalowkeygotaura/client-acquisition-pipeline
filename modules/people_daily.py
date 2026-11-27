"""Daily LinkedIn queue of HIGH-NETWORK PEOPLE (the James Hush format).

Shaurya's call (2026-11-27): clinics won't answer LinkedIn; the channel is for
people who open doors: research mentors, internship hosts, potential clients,
partners. Format proven on 2026-10-30 (Projects/internship-hunt-2026/
linkedin-send-list-2026-10-30.md):

  joke about his REAL work in the first 5 words (the notification preview)
  + "Delhi high schooler" + one specific thing he can do for THEM + short ask
  e.g. "Pipecat, Groq and pure delusion. That's my whole $0/min phone agent and
        it WORKS. Delhi high schooler, ditched Vapi for you guys. Would love to
        contribute a Hindi example agent or help in any role. 60s demo?"

Data lives in a PRIVATE Google Sheet tab "people" (the repo is public):
  name | role | company | category | hook | profile | status | note | message | queued_at
category: mentor | internship | client | partner
status:   "" = ready, "QUEUED YYYY-MM-DD", "SENT ..." / "SKIP" = never queue.
Add people by adding rows (or scripts/seed_people.py). 5 a day, rotating
categories so every day mixes mentors, internships, clients and partners.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone

from modules import bait

log = logging.getLogger(__name__)

TAB = "people"
HEADERS = ["name", "role", "company", "category", "hook", "profile", "status",
           "note", "message", "queued_at"]
DAILY_N = int(os.getenv("PEOPLE_DAILY_N", "5"))
NOTE_MAX = 300
CATEGORIES = ("mentor", "internship", "client", "partner")

# The ONLY facts a note may use. Keep these true; update when they change.
FACTS = [
    "Delhi high schooler (DPS RK Puram)",
    "built a Hinglish phone agent in Python on Pipecat + Groq that costs $0 a minute",
    "moved his voice agent off Vapi to run it for $0 a minute",
    "3 merged PRs into Microsoft .NET Aspire, including a security fix",
    "built VayuVani, an air-pollution cause engine for Delhi validated against measured source data",
    "runs a small AI receptionist offer for dental clinics",
]

ASKS = {
    "mentor": "ask if he could help on one of their research problems, or get 10 minutes of advice",
    "internship": "ask about any intern, contributor or 'here's a hard problem, go' role",
    "client": "offer to build them a working Hindi/Hinglish voice agent demo on their use case",
    "partner": "propose one concrete thing to build together (an integration, an example, a pilot)",
}

FOLLOW_UPS = {
    "mentor": ("Thanks for accepting! Quick context: I'm Shaurya, in high school in Delhi. "
               "I build voice AI and data tools (a $0/min Hinglish phone agent, and VayuVani, an "
               "air-pollution cause engine). I'd love to learn how you'd approach {topic}. "
               "Could I send you a 1-page summary of what I've built and one question?"),
    "internship": ("Thanks for accepting! Quick context: I'm Shaurya, in high school in Delhi. "
                   "I built a Hinglish phone agent in Python (Pipecat + Groq) that costs $0 a minute. "
                   "Here's 60s of it taking a call: [demo link]\n\nLooking for an internship, "
                   "part-time or 'here's a hard problem, go' thing, remote. Happy to start with a "
                   "small task so you can see how I work."),
    "client": ("Thanks for accepting! I'm Shaurya, a high schooler in Delhi who builds Hindi/Hinglish "
               "voice agents ($0 a minute on Pipecat + Groq). If you tell me one call your team "
               "handles a lot, I'll build a working demo of an agent taking it, free. Want to see one?"),
    "partner": ("Thanks for accepting! I'm Shaurya, a high schooler in Delhi building Hinglish voice "
                "agents on Pipecat + Groq for $0 a minute. I think there's a small, real thing we "
                "could build together at {company}. Can I send a 5-line proposal?"),
}

_PROMPT = """Write ONE LinkedIn connection note from Shaurya to {name} ({role}, {company}).
Why them: {hook}

THE JOKE IS EVERYTHING. It is a tiny, funny SCENE FROM SHAURYA'S LIFE building voice agents:
a family member, an aunty/uncle, his bot misbehaving, Hinglish chaos, 3am debugging. Self-deprecating.
It is NEVER a comment or compliment about their company ("Wild climb", "Bold", "Impressive" = FAIL).

These are the real ones that worked. Match this energy exactly:
- "Aunty hung up on my bot. Said 'haan ji' with the wrong vibe and I took it personally. High schooler, $0/min Hinglish agent in Python. Would love to build with Bolna this winter. Can I show you?"
- "Mom thinks I'm dating 'Silero'. I've just been fighting turn detection. Delhi student shipping realtime Hinglish voice agents, want to PR a Hindi example into livekit/agents. Do contributors ever become interns?"
- "Dadi asked if Samvaad can call the sabziwala. Can it?? Delhi high schooler with a $0/min Hinglish agent, want to try it on Sarvam voices and share what breaks. Open to an intern role too!"
- "My bot out-argued a Delhi uncle. In Hinglish, on Pipecat, for $0 a minute. Congrats on 1.0! High schooler here, would love to help build Indian-language examples. Can I send the demo?"
- "My n8n canvas needs therapy. More spaghetti than a nonna's kitchen, and it still books meetings. Delhi high schooler, would love to build community nodes + tutorials people finish. Any community/intern role?"
- "'Nau nine double three??' haunts my dreams. I debug Hinglish phone numbers at 3am so nobody else has to. Delhi high schooler, $0/min agent on Pipecat + Groq. Can I help Bolna's eng team?"
- "Grandparents think my AI is a scam call. I'm building voice AI for people without smartphones. Delhi high schooler, would love to build it on Sarvam. Can I show you?"
Invent a NEW scene in this style (do not reuse these), ideally one that connects to what {company} does.

RULES:
- The joke scene is the FIRST sentence and lands in the first 5 words (that is all the notification shows).
- Second sentence twists or explains the joke, like the examples.
- Then "Delhi high schooler" (or "High schooler"), then ONE fact from this list, nothing else is true:
{facts}
- Then: {ask}. Tie it to {company} specifically.
- End with a short question (e.g. "60s demo?", "Can I show you?").
- Under 280 characters. No em dashes. No emojis. No hashtags. No links. Never invent a fact.

Return JSON: {{"note": "..."}}
"""


def _sheet():
    from modules import sheets_writer
    return sheets_writer.get_sheet(TAB)


def load() -> tuple[object, list[dict]]:
    ws = _sheet()
    rows = ws.get_all_values()
    if not rows:
        return ws, []
    head = [h.strip().lower() for h in rows[0]]
    people = []
    for i, r in enumerate(rows[1:], start=2):
        p = {h: (r[j].strip() if j < len(r) else "") for j, h in enumerate(head)}
        p["_row"] = i
        people.append(p)
    return ws, people


def pick(people: list[dict], n: int = DAILY_N, today: str | None = None) -> list[dict]:
    """n ready people, round-robin over categories, deterministic per day."""
    rng = random.Random(today or "x")
    ready = [p for p in people if p.get("name") and not p.get("status")]
    by_cat: dict[str, list[dict]] = {}
    for p in ready:
        by_cat.setdefault(p.get("category") or "partner", []).append(p)
    for lst in by_cat.values():
        rng.shuffle(lst)
    cats = [c for c in CATEGORIES if c in by_cat] + [c for c in by_cat if c not in CATEGORIES]
    rng.shuffle(cats)
    out: list[dict] = []
    while len(out) < n and any(by_cat.values()):
        for c in cats:
            if by_cat.get(c) and len(out) < n:
                out.append(by_cat[c].pop())
    return out


def profile_link(p: dict) -> str:
    prof = p.get("profile") or ""
    if re.match(r"^https://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]{3,100}/?$", prof):
        return prof
    q = f"{p.get('name', '')} {p.get('company', '')}".strip()
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(q)


_SCENE = re.compile(
    r"\b(my|i|i'm|i've|me|mom|mum|dad|papa|mummy|dadi|nani|nana|dada|aunty|auntie|uncle|"
    r"grandparents?|grandma|bhai|didi|sister|brother|neighbou?r|teacher|bot|phone|agent)\b", re.I)
_PRAISE = re.compile(r"^(wild|bold|impressive|amazing|huge|big|congrats|wow|love)\b", re.I)


def _has_joke(note: str) -> bool:
    """First sentence must be a life scene (him / family / his bot), not praise of them."""
    first = re.split(r"(?<=[.!?])\s", note.strip(), maxsplit=1)[0]
    return len(first.split()) >= 4 and bool(_SCENE.search(first)) and not _PRAISE.match(first)


# Jokes already used on 2026-10-30: a recycled one reads as a template.
_USED = re.compile(r"sabziwala|silero|haan ji|out-argued|therapy|nau nine|scam call|"
                   r"pure delusion|call cent", re.I)


def _valid(note: str) -> bool:
    return (bool(note) and len(note) <= NOTE_MAX and "?" in note[-60:] and _has_joke(note)
            and not _USED.search(note)
            and "high school" in note.lower() and bait.is_safe(note)
            and "—" not in note and "http" not in note)


def write_note(p: dict) -> str:
    cat = p.get("category") or "partner"
    company = p.get("company") or "your team"
    fallback = random.choice([
        f"My bot speaks better Hinglish than me. Nani noticed first. Delhi high schooler, built it on "
        f"Pipecat + Groq for $0 a minute. Would love to help {company} in any way I can. Can I show you?",
        f"Papa answered my AI's call and said 'galat number'. Twice. Delhi high schooler, $0/min "
        f"Hinglish phone agent on Pipecat + Groq. Would love to build something with {company}. 60s demo?",
        f"Aunty asked my bot for its biodata. It almost gave it. Delhi high schooler shipping $0/min "
        f"Hinglish voice agents. Would love to help {company}, any role. Can I show you?",
    ])
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return fallback
    try:
        from openai import OpenAI
        from config import LLM_BASE_URL, LLM_MODEL
        client = OpenAI(api_key=key, base_url=LLM_BASE_URL)
        prompt = _PROMPT.format(name=p.get("name", ""), role=p.get("role", ""), company=company,
                                hook=p.get("hook") or "works in voice AI / AI in India",
                                facts="\n".join(f"  * {f}" for f in FACTS),
                                ask=ASKS.get(cat, ASKS["partner"]))
        for _ in range(2):  # one retry if the first draft breaks a rule
            r = client.chat.completions.create(
                model=LLM_MODEL, messages=[{"role": "user", "content": prompt}],
                temperature=0.9, max_tokens=250, response_format={"type": "json_object"})
            note = (json.loads(r.choices[0].message.content or "{}").get("note") or "").strip()
            note = " ".join(note.replace("–", ",").split())  # one line, single spaces
            if _valid(note):
                return note
            log.info("people note rejected for %s: %r", p.get("name"), note)
    except Exception as e:
        log.warning("people note generation failed for %s: %s", p.get("name"), e)
    return fallback


def follow_up(p: dict) -> str:
    cat = p.get("category") or "partner"
    topic = p.get("hook") or "Indian-language voice AI"
    return FOLLOW_UPS.get(cat, FOLLOW_UPS["partner"]).format(
        company=p.get("company") or "your company", topic=topic)


def queue() -> int:
    """Pick today's people, write note + follow-up + status back. Returns count."""
    ws, people = load()
    if not people:
        print("    [PEOPLE] tab empty: run scripts/seed_people.py")
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if any(p.get("status") == f"QUEUED {today}" for p in people):
        return 0
    head = [h.strip().lower() for h in ws.row_values(1)]
    col = {h: i + 1 for i, h in enumerate(head)}
    done = 0
    for p in pick(people, today=today):
        try:
            note, msg = write_note(p), follow_up(p)
            for field, val in (("note", note), ("message", msg), ("queued_at", today),
                               ("status", f"QUEUED {today}")):
                if field in col:
                    ws.update_cell(p["_row"], col[field], val)
            done += 1
        except Exception as e:
            log.error("could not queue %s: %s", p.get("name"), e)
    return done


def desk_rows(days: int = 3) -> list[dict]:
    try:
        _, people = load()
    except Exception as e:
        print(f"    [desk] people tab unreadable: {e}")
        return []
    now = datetime.now(timezone.utc).date()
    out = []
    for p in people:
        st = p.get("status") or ""
        if not st.startswith("QUEUED "):
            continue
        try:
            day = datetime.strptime(st[7:17], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (now - day).days > days:
            continue
        link = profile_link(p)
        out.append({"name": p.get("name", ""), "role": p.get("role", ""),
                    "company": p.get("company", ""), "category": p.get("category", ""),
                    "link": link, "link_type": "profile" if "/in/" in link else "search",
                    "note": p.get("note", ""), "message": p.get("message", ""),
                    "queued": day.isoformat()})
    return sorted(out, key=lambda r: r["queued"], reverse=True)
