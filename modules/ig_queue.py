"""
modules/ig_queue.py — the day's A-tier Instagram send list.

WHY THIS IS SEPARATE FROM THE PIPELINE
The main pipeline fires Instagram inline, once, at the moment a lead is first
scraped, and only if that lead happens to clear SCORE_HIGH on the same run. That
is the wrong shape for Instagram specifically:

  * IG has a hard daily ceiling (20 DMs — see modules/instagram.py). A scrape run
    that turns up 60 good leads cannot send to them today, and the surplus was
    simply never revisited.
  * A lead scraped on Monday with no handle might get a handle on Tuesday, once
    its site is re-scraped or enriched. The inline send had already passed it by.
  * "A-tier" is a ranking question across the whole known pool, not a yes/no
    question about the one lead in front of us.

So this module treats the saved sheet as the pool and picks the best N sendable
leads each day. It is a SELECTOR, not a sender: it decides who, writes the list,
and leaves the sending to modules/instagram.py under its own rate limits.

ELIGIBILITY (all must hold)
  1. has an instagram_handle — from the business's own website, via
     researcher.extract_instagram_handle. No handle, no send: we do not guess
     handles, and without one there is no honest basis for a "saw your reels" line.
  2. has instagram_msg copy generated for it
  3. not already IG-messaged (instagram_sent), not opted out, not dead/booked
  4. lead_score >= IG_MIN_SCORE (default 7 = the pipeline's SCORE_HIGH)

Cities are deliberately NOT filtered. The pool is every city in config.CITIES;
Jaipur has no privilege here beyond being first in the rotation. Ranking is by
score, and ties break toward cities that have been sent to least, so the queue
spreads across markets on its own instead of draining the home city first.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# A-tier floor. Mirrors pipeline.SCORE_HIGH; override to widen or tighten.
IG_MIN_SCORE = int(os.getenv("IG_MIN_SCORE", "7"))

# How many to queue per day. Defaults to the IG daily DM cap so the queue never
# promises more sends than the channel is allowed to make.
IG_DAILY_TARGET = int(os.getenv("IG_DAILY_TARGET", os.getenv("INSTAGRAM_DAILY_DM_LIMIT", "20")))

_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
QUEUE_PATH = _RUNS_DIR / "ig_queue.json"

# Conversation states that mean "stop contacting this lead".
_CLOSED_STATES = {"dead", "booked", "closed"}


def _truthy(value) -> bool:
    """Sheets stores booleans as the strings TRUE/yes/1 — and sometimes blank."""
    return str(value or "").strip().lower() in {"true", "yes", "1", "y"}


def _score(lead: dict) -> int:
    try:
        return int(float(lead.get("lead_score") or 0))
    except (TypeError, ValueError):
        return 0


def _city(lead: dict) -> str:
    """Coarse market label used only for spreading the queue across cities."""
    loc = (lead.get("location") or "").strip()
    return loc.split(",")[0].strip().lower() if loc else "unknown"


def is_eligible(lead: dict) -> tuple[bool, str]:
    """(eligible, reason_if_not) — reason is for the run log, not the prospect."""
    if not (lead.get("instagram_handle") or "").strip():
        return False, "no_handle"
    if not (lead.get("instagram_msg") or "").strip():
        return False, "no_copy"
    if _truthy(lead.get("instagram_sent")):
        return False, "already_sent"
    if _truthy(lead.get("opted_out")):
        return False, "opted_out"
    if (lead.get("conversation_stage") or "").strip().lower() in _CLOSED_STATES:
        return False, "closed"
    if _score(lead) < IG_MIN_SCORE:
        return False, "below_a_tier"
    return True, ""


def select(leads: list[dict], target: int | None = None) -> list[dict]:
    """Rank eligible leads and return today's send list.

    Ordering: score descending, then fewest already-sent leads in that city, so
    a single dense market cannot monopolise the queue day after day.
    """
    target = IG_DAILY_TARGET if target is None else target

    # How much of each city we have ALREADY sent to historically. Cities we've
    # worked hardest sort last on ties.
    sent_by_city = Counter(
        _city(l) for l in leads if _truthy(l.get("instagram_sent"))
    )

    eligible = [l for l in leads if is_eligible(l)[0]]
    eligible.sort(key=lambda l: (-_score(l), sent_by_city[_city(l)], l.get("slug") or ""))
    return eligible[:target]


def build(leads: list[dict] | None = None, target: int | None = None) -> dict:
    """Select today's A-tier IG leads and write runs/ig_queue.json.

    Returns the queue payload (also written to disk for the Command Center).
    """
    if leads is None:
        from modules import sheets_writer
        leads = sheets_writer.get_all_leads()

    chosen = select(leads, target)

    # Count why the rest were skipped — this is what tells you whether the queue
    # is short because the pool is dry or because enrichment is failing.
    reasons: Counter = Counter()
    for lead in leads:
        ok, why = is_eligible(lead)
        if not ok:
            reasons[why] += 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_score": IG_MIN_SCORE,
        "target": IG_DAILY_TARGET if target is None else target,
        "count": len(chosen),
        "skipped": dict(reasons),
        "cities": sorted({_city(l) for l in chosen}),
        "leads": [
            {
                "slug": l.get("slug", ""),
                "company_name": l.get("company_name", ""),
                "handle": (l.get("instagram_handle") or "").lstrip("@"),
                "city": _city(l),
                "niche": l.get("niche", ""),
                "score": _score(l),
                "instagram_msg": l.get("instagram_msg", ""),
            }
            for l in chosen
        ],
    }

    try:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        QUEUE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        # The queue is still usable in-memory by the caller; losing the file
        # only costs the dashboard its copy.
        log.error("Could not write %s: %s", QUEUE_PATH, e)

    return payload


def run(target: int | None = None) -> dict:
    """Entry point for `python pipeline.py ig_queue`."""
    payload = build(target=target)
    print(f"IG queue: {payload['count']} A-tier leads (score >= {payload['min_score']}) "
          f"across {len(payload['cities'])} cities")
    for lead in payload["leads"]:
        print(f"  @{lead['handle']:<28} {lead['company_name'][:34]:<34} "
              f"{lead['city']:<12} score={lead['score']}")
    if payload["skipped"]:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(payload["skipped"].items()))
        print(f"  skipped: {breakdown}")
    return payload
