"""
modules/analytics.py — Phase 6 upgrade

Reads the leads sheet, aggregates performance by niche, writes to niche_analytics tab.
Call run() from the pipeline every N leads to refresh the analytics.
"""
import logging
from collections import defaultdict

from modules import sheets_writer

log = logging.getLogger(__name__)


def _safe_div(num: int, den: int) -> float:
    return num / den if den > 0 else 0.0


def run() -> list[dict]:
    """
    Compute per-niche metrics from the leads sheet.
    Writes to niche_analytics tab. Returns ranked list (best booked_call_rate first).
    """
    try:
        leads = sheets_writer.get_all_leads()
    except Exception as e:
        log.error("analytics: failed to read leads: %s", e)
        return []

    stats: dict[str, dict] = defaultdict(lambda: {
        "total_sent": 0,
        "total_replies": 0,
        "total_booked_calls": 0,
        "total_clients": 0,
    })

    for lead in leads:
        niche = lead.get("niche") or "unknown"
        sent = lead.get("email_sent") == "TRUE" or lead.get("linkedin_sent") == "TRUE"
        if not sent:
            continue

        s = stats[niche]
        s["total_sent"] += 1

        reply_status = (lead.get("reply_status") or "").lower()
        if reply_status and reply_status not in ("", "none", "no_reply"):
            s["total_replies"] += 1

        if (lead.get("booked_call") or "").lower() == "yes":
            s["total_booked_calls"] += 1

        if (lead.get("closed_client") or "").lower() == "yes":
            s["total_clients"] += 1

    rows = []
    for niche, s in stats.items():
        rows.append({
            "niche": niche,
            **s,
            "reply_rate":       _safe_div(s["total_replies"],      s["total_sent"]),
            "booked_call_rate": _safe_div(s["total_booked_calls"],  s["total_sent"]),
            "conversion_rate":  _safe_div(s["total_clients"],       s["total_sent"]),
        })

    rows.sort(key=lambda r: r["booked_call_rate"], reverse=True)

    try:
        sheets_writer.upsert_niche_analytics(rows)
    except Exception as e:
        log.error("analytics: failed to write niche_analytics tab: %s", e)

    return rows


def top_niches(min_sent: int = 5) -> list[str]:
    """Return niches with enough data (min_sent leads sent) ranked by booked_call_rate."""
    rows = run()
    qualified = [r for r in rows if r["total_sent"] >= min_sent]
    return [r["niche"] for r in qualified]


def bottom_niches(min_sent: int = 5) -> list[str]:
    """Return the bottom 50% of niches by booked_call_rate (for deprioritization)."""
    rows = run()
    qualified = [r for r in rows if r["total_sent"] >= min_sent]
    if not qualified:
        return []
    half = max(1, len(qualified) // 2)
    return [r["niche"] for r in qualified[-half:]]
