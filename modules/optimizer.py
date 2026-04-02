"""
modules/optimizer.py — Phase 7 upgrade

Feedback loop: every 50 leads sent, analyze performance and adjust scoring weights.
Writes a human-readable recommendation to the errors sheet tab "optimizer_log".
Does NOT auto-modify config.py — outputs recommendations that you review and apply.

Trigger: called from pipeline.py when total_sent % 50 == 0.
"""
import logging
from datetime import datetime, timezone

from modules import analytics, sheets_writer
from modules.scorer import WEIGHTS

log = logging.getLogger(__name__)

# Minimum leads sent per niche before we treat the data as actionable
MIN_SAMPLE = 10


def run():
    """Analyze niche + variant performance. Log recommendations."""
    rows = analytics.run()
    if not rows:
        log.info("optimizer: no analytics data yet")
        return

    qualified = [r for r in rows if r["total_sent"] >= MIN_SAMPLE]
    if not qualified:
        log.info("optimizer: not enough data per niche yet (need %d+ per niche)", MIN_SAMPLE)
        return

    # Rank niches by booked_call_rate
    qualified.sort(key=lambda r: r["booked_call_rate"], reverse=True)
    top_half = qualified[:max(1, len(qualified) // 2)]
    bottom_half = qualified[max(1, len(qualified) // 2):]

    top_niches = [r["niche"] for r in top_half]
    bottom_niches = [r["niche"] for r in bottom_half]

    # Analyze message variant performance from lead data
    leads = sheets_writer.get_all_leads()
    variant_stats: dict[str, dict] = {}
    for lead in leads:
        v = lead.get("message_variant_id", "")
        if not v:
            continue
        if v not in variant_stats:
            variant_stats[v] = {"sent": 0, "booked": 0}
        sent = lead.get("email_sent") == "TRUE" or lead.get("linkedin_sent") == "TRUE"
        if sent:
            variant_stats[v]["sent"] += 1
        if (lead.get("booked_call") or "").lower() == "yes":
            variant_stats[v]["booked"] += 1

    best_variant = max(
        variant_stats,
        key=lambda v: variant_stats[v]["booked"] / max(1, variant_stats[v]["sent"]),
        default=None,
    ) if variant_stats else None

    # Build recommendation log
    lines = [
        f"=== Optimizer Run: {datetime.now(timezone.utc).isoformat()} ===",
        "",
        "TOP NICHES (scale up — prioritize in scoring):",
        *[f"  {r['niche']}: {r['booked_call_rate']:.1%} booked call rate ({r['total_sent']} sent)" for r in top_half],
        "",
        "BOTTOM NICHES (reduce or cut):",
        *[f"  {r['niche']}: {r['booked_call_rate']:.1%} booked call rate ({r['total_sent']} sent)" for r in bottom_half],
        "",
        f"BEST MESSAGE VARIANT: {best_variant or 'insufficient data'}",
    ]

    if variant_stats:
        for v, s in variant_stats.items():
            rate = s["booked"] / max(1, s["sent"])
            lines.append(f"  {v}: {rate:.1%} ({s['booked']}/{s['sent']} booked)")

    lines += [
        "",
        "SUGGESTED SCORING CHANGES (apply manually in scorer.py WEIGHTS):",
        *[f"  INCREASE niche_bonus['{n}'] by +1" for n in top_niches if WEIGHTS["niche_bonus"].get(n, 0) < 3],
        *[f"  DECREASE niche_bonus['{n}'] by -1" for n in bottom_niches if WEIGHTS["niche_bonus"].get(n, 0) > -2],
        "",
        "SUGGESTED MESSAGING CHANGES:",
        f"  Use '{best_variant}' variant as default for all niches" if best_variant else "  No variant winner yet",
        "",
        "ACTION: Review above. Update scorer.py WEIGHTS and generator._select_variant() manually.",
        "=" * 60,
    ]

    recommendation = "\n".join(lines)
    log.info("\n%s", recommendation)

    # Write to optimizer_log sheet tab
    try:
        sheet = sheets_writer.get_sheet("optimizer_log")
        sheet.append_row([datetime.now(timezone.utc).isoformat(), recommendation])
    except Exception as e:
        log.warning("optimizer: could not write to sheet: %s", e)
