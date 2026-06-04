# pipeline.py
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Timezone → IANA zone name. Extend as you add new target markets.
_LOCATION_TZ: dict[str, str] = {
    "jaipur": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "pune": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "london": "Europe/London", "uk": "Europe/London",
    "new york": "America/New_York", "los angeles": "America/Los_Angeles",
    "toronto": "America/Toronto",
}
_SEND_HOUR_START = 9   # 9 AM local time
_SEND_HOUR_END   = 18  # 6 PM local time
_SEND_WEEKDAYS   = {0, 1, 2, 3, 4}  # Mon–Fri


def _in_send_window(location: str) -> bool:
    """Return True if it's currently office hours in the lead's timezone."""
    loc = (location or "").lower()
    tz_name = next((tz for key, tz in _LOCATION_TZ.items() if key in loc), "Asia/Kolkata")
    try:
        local_now = datetime.now(ZoneInfo(tz_name))
        return (local_now.weekday() in _SEND_WEEKDAYS
                and _SEND_HOUR_START <= local_now.hour < _SEND_HOUR_END)
    except Exception:
        return True  # unknown timezone — fail open


from config import CITIES
from modules import (
    scraper, researcher, enricher, scorer, personalizer,
    generator, sheets_writer, email_sender, linkedin, whatsapp,
    instagram, reply_handler, analytics, optimizer, apollo_scraper,
    maps_scraper, icebreaker, run_metrics,
)
from modules.security_utils import get_audit_log

audit = get_audit_log()

# Lead source: "indeed" (SerpAPI, default) or "apollo" (Obscura + cookies)
LEAD_SOURCE = os.getenv("LEAD_SOURCE", "indeed").lower()

# ── Free-tier SerpAPI budget guard ───────────────────────────────────────────
# SerpAPI free = 100 searches/month. Scraping all 6 cities (x N maps queries)
# every weekday burns that in ~2 runs, after which every run silently finds 0
# leads ("account has run out of searches"). To stay free AND keep producing
# leads, set MAX_CITIES_PER_RUN to a small number: we then scrape a ROTATING
# window of that many cities, advancing by day-of-year so every city is covered
# over a few days. Default 0 = no limit (original all-cities behavior, opt-in).
MAX_CITIES_PER_RUN = int(os.getenv("MAX_CITIES_PER_RUN", "0"))
SERPAPI_FREE_LIMIT = 100  # searches/month on the free plan (shown on dashboard)


def _select_cities():
    """Rotate a window of MAX_CITIES_PER_RUN cities, advancing daily, so the
    SerpAPI free quota lasts the month. 0 = all cities (unchanged behavior)."""
    if MAX_CITIES_PER_RUN <= 0 or MAX_CITIES_PER_RUN >= len(CITIES):
        return list(CITIES)
    start = datetime.now(timezone.utc).timetuple().tm_yday * MAX_CITIES_PER_RUN % len(CITIES)
    rotated = CITIES[start:] + CITIES[:start]
    return rotated[:MAX_CITIES_PER_RUN]

# ── Routing thresholds ───────────────────────────────────────────────────────
# high priority (score ≥ 7): full outreach (email + LinkedIn)
# medium priority (4–6):     email only
# low priority (<4):         skip entirely

SCORE_HIGH = 7
SCORE_LOW = 4

# Run analytics + optimizer every N leads saved
OPTIMIZER_INTERVAL = 50

# v3 region-aware channel ordering. First in the list = first to attempt.
# India SMB healthcare: WhatsApp + Instagram are where attention lives.
# Rest of world: original email-first hierarchy.
_INDIA_KEYS = (
    "india", "delhi", "mumbai", "bangalore", "bengaluru", "hyderabad",
    "pune", "jaipur", "chennai", "kolkata", "ahmedabad", "kochi",
)
CHANNEL_ORDER_INDIA = ("whatsapp", "instagram", "email", "linkedin")
CHANNEL_ORDER_DEFAULT = ("email", "linkedin", "whatsapp")


def _is_india_lead(data: dict) -> bool:
    loc = (data.get("location") or "").lower()
    return any(k in loc for k in _INDIA_KEYS)


def _channel_order(data: dict) -> tuple[str, ...]:
    return CHANNEL_ORDER_INDIA if _is_india_lead(data) else CHANNEL_ORDER_DEFAULT


def run():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Pipeline starting...")
    existing = sheets_writer.get_all_leads()
    total_saved = 0
    total_emailed = 0
    total_linkedin = 0
    total_whatsapp = 0
    total_skipped_low = 0

    _SOURCES = {"apollo": (apollo_scraper, "Apollo"),
                "maps": (maps_scraper, "Google Maps"),
                "indeed": (scraper, "Indeed")}
    source_module, source_label = _SOURCES.get(LEAD_SOURCE, _SOURCES["indeed"])

    cities = _select_cities()
    total_found = 0
    scraper_errors = 0
    print(f"  Scraping {len(cities)}/{len(CITIES)} cities this run "
          f"(MAX_CITIES_PER_RUN={MAX_CITIES_PER_RUN or 'all'}): {', '.join(cities)}")

    for city in cities:
        print(f"  Scraping {source_label}: {city}")
        try:
            jobs = source_module.run(city)
            total_found += len(jobs)
            print(f"    Found {len(jobs)} leads")
        except Exception as e:
            scraper_errors += 1
            print(f"    Scraper failed for {city}: {e}")
            continue

        for job in jobs:
            company = job["company_name"]
            try:
                if sheets_writer.domain_exists(job.get("domain"), existing):
                    print(f"    [SKIP] {company} (already in Sheets)")
                    audit.append("pipeline", "skip", company, ok=True,
                                 detail={"reason": "duplicate_domain", "domain": job.get("domain", "")})
                    continue

                print(f"    Processing: {company}")

                # ── Enrichment + Scoring ─────────────────────────────────
                data = researcher.run(job)
                data = enricher.run(data)
                data = scorer.run(data)

                priority = data.get("lead_priority", "medium")
                score = data.get("lead_score", 0)
                niche = data.get("niche", "?")
                print(f"      Niche: {niche} | Score: {score} | Priority: {priority}")

                # Drop low-priority leads entirely
                if score < SCORE_LOW:
                    total_skipped_low += 1
                    print(f"      [SKIP] Low score ({score}) — not worth outreach")
                    audit.append("pipeline", "skip", company, ok=True,
                                 detail={"reason": "low_score", "score": score, "niche": niche})
                    continue

                # ── Person-level personalization (only for qualified leads) ──
                data = personalizer.run(data)
                if data.get("person_hook") or data.get("company_hook"):
                    print(f"      [PERSONALIZED] hooks found for {company}")

                # ── Lead-specific icebreaker (Maps leads especially) ──────
                data = icebreaker.run(data)

                # ── Message generation ────────────────────────────────────
                data = generator.run(data)

                # ── Generate opt-out token before persisting ──────────────
                data["opt_out_token"] = email_sender.generate_opt_out_token()

                # ── Persist ───────────────────────────────────────────────
                saved = sheets_writer.save(data, existing)
                if saved:
                    total_saved += 1
                    existing.append({"domain": data.get("domain", "")})

                    # Trigger optimizer every N leads
                    if total_saved % OPTIMIZER_INTERVAL == 0:
                        print(f"\n  [OPTIMIZER] Running at {total_saved} leads saved...")
                        try:
                            optimizer.run()
                        except Exception as e:
                            print(f"  [OPTIMIZER] Failed: {e}")

                # ── Timezone window check ─────────────────────────────────
                location = data.get("location", "")
                if not _in_send_window(location):
                    print(f"      [TZ SKIP] {company} — outside office hours in {location or 'IST'}. "
                          f"Run pipeline between 9am–6pm local time.")
                    audit.append("pipeline", "skip", company, ok=True,
                                 detail={"reason": "tz_window", "location": location or "unknown"})
                    continue

                slug = data["slug"]

                # ── Send: high priority ───────────────────────────────────
                # v3 routing: India SMB healthcare uses whatsapp → instagram → email → linkedin.
                # Non-India keeps the original email-first hierarchy. Both branches preserve
                # all existing sheet updates, audit hooks, and counters.
                if score >= SCORE_HIGH:
                    if _is_india_lead(data):
                        # ── v3 INDIA channel order ──
                        first_sent = None  # first channel that successfully delivered
                        for ch in _channel_order(data):
                            if ch == "whatsapp":
                                wa_sent = whatsapp.send(data)
                                if wa_sent:
                                    total_whatsapp += 1
                                    if first_sent is None:
                                        first_sent = "whatsapp"
                                        sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                        sheets_writer.update_channel(slug, "whatsapp")
                                audit.append("whatsapp", "send", slug, ok=bool(wa_sent),
                                             detail={"channel": "whatsapp", "score": score,
                                                     "tier": "high", "region": "india"})
                            elif ch == "instagram":
                                ig_sent = instagram.send(data)
                                if ig_sent:
                                    sheets_writer.update_field(slug, "instagram_sent", "TRUE")
                                    if first_sent is None:
                                        first_sent = "instagram"
                                        sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                        sheets_writer.update_channel(slug, "instagram")
                                audit.append("instagram", "send", slug, ok=bool(ig_sent),
                                             detail={"channel": "instagram", "score": score,
                                                     "tier": "high", "region": "india"})
                            elif ch == "email":
                                if data.get("email"):
                                    print(f"      [EMAIL HIGH IN] → {data['email']}")
                                    ok, msg_id, sender = email_sender.send(data)
                                    if ok:
                                        sheets_writer.update_field(slug, "email_sent", "TRUE")
                                        sheets_writer.update_field(slug, "message_id", msg_id)
                                        sheets_writer.update_field(slug, "sender_account", sender)
                                        total_emailed += 1
                                        if first_sent is None:
                                            first_sent = "email"
                                            sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                            sheets_writer.update_channel(slug, "email")
                                        audit.append("email_sender", "send", slug, ok=True,
                                                     detail={"channel": "email", "score": score,
                                                             "to": data["email"], "message_id": msg_id,
                                                             "sender": sender, "tier": "high",
                                                             "region": "india"})
                                    else:
                                        print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                                        audit.append("email_sender", "send", slug, ok=False,
                                                     detail={"channel": "email", "tier": "high",
                                                             "region": "india"})
                            elif ch == "linkedin":
                                li_sent = linkedin.send(data)
                                if li_sent:
                                    sheets_writer.update_field(slug, "linkedin_sent", "TRUE")
                                    total_linkedin += 1
                                    if first_sent is None:
                                        first_sent = "linkedin"
                                        sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                        sheets_writer.update_channel(slug, "linkedin")
                                audit.append("linkedin", "send", slug, ok=bool(li_sent),
                                             detail={"channel": "linkedin", "score": score,
                                                     "tier": "high", "region": "india"})
                    else:
                        # ── ORIGINAL non-India flow (unchanged) ──
                        if data.get("email"):
                            print(f"      [EMAIL HIGH] → {data['email']}")
                            ok, msg_id, sender = email_sender.send(data)
                            if ok:
                                sheets_writer.update_field(slug, "email_sent", "TRUE")
                                sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                sheets_writer.update_field(slug, "message_id", msg_id)
                                sheets_writer.update_field(slug, "sender_account", sender)
                                sheets_writer.update_channel(slug, "email")
                                total_emailed += 1
                                audit.append("email_sender", "send", slug, ok=True,
                                             detail={"channel": "email", "score": score,
                                                     "to": data["email"], "message_id": msg_id,
                                                     "sender": sender, "tier": "high"})
                            else:
                                print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                                audit.append("email_sender", "send", slug, ok=False,
                                             detail={"channel": "email", "tier": "high"})
                        else:
                            print(f"      [NO EMAIL] {company} — no address found (LinkedIn only)")

                        li_sent = linkedin.send(data)
                        if li_sent:
                            sheets_writer.update_field(slug, "linkedin_sent", "TRUE")
                            if not data.get("email"):
                                sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                sheets_writer.update_channel(slug, "linkedin")
                            total_linkedin += 1
                        audit.append("linkedin", "send", slug, ok=bool(li_sent),
                                     detail={"channel": "linkedin", "score": score})

                        wa_sent = whatsapp.send(data)
                        if wa_sent:
                            total_whatsapp += 1
                        audit.append("whatsapp", "send", slug, ok=bool(wa_sent),
                                     detail={"channel": "whatsapp", "score": score})

                # ── Send: medium priority → email only ───────────────────
                elif score >= SCORE_LOW:
                    if data.get("email"):
                        print(f"      [EMAIL MED] → {data['email']}")
                        ok, msg_id, sender = email_sender.send(data)
                        if ok:
                            sheets_writer.update_field(slug, "email_sent", "TRUE")
                            sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                            sheets_writer.update_field(slug, "message_id", msg_id)
                            sheets_writer.update_field(slug, "sender_account", sender)
                            sheets_writer.update_channel(slug, "email")
                            total_emailed += 1
                            audit.append("email_sender", "send", slug, ok=True,
                                         detail={"channel": "email", "score": score,
                                                 "to": data["email"], "message_id": msg_id,
                                                 "sender": sender, "tier": "medium"})
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                            audit.append("email_sender", "send", slug, ok=False,
                                         detail={"channel": "email", "tier": "medium"})
                    else:
                        print(f"      [NO EMAIL] {company} — no address found, skipping")

            except Exception as e:
                print(f"    [ERROR] {company}: {e}")
                sheets_writer.log_error(company, str(e))
                continue

    print(
        f"\nDone. Found: {total_found} | Saved: {total_saved} | Emailed: {total_emailed} | "
        f"LinkedIn: {total_linkedin} | WhatsApp: {total_whatsapp} | "
        f"Skipped (low score): {total_skipped_low}"
    )

    # ── Emit run metrics for the Command Center dashboard ────────────────────
    # "degraded" = the run was clean but the upstream scraper produced nothing,
    # which on the free plan almost always means SerpAPI hit its monthly wall.
    quota_wall = total_found == 0 and total_saved == 0
    if quota_wall:
        status = "degraded"
        summary = (f"0 leads from {len(cities)} cities — scraper returned nothing "
                   f"(likely SerpAPI free quota exhausted or no new listings)")
    else:
        status = "ok"
        summary = (f"{total_found} found, {total_saved} new, {total_emailed} emailed, "
                   f"{total_whatsapp} WhatsApp, {total_linkedin} LinkedIn")
    run_metrics.write(
        mode="scrape",
        status=status,
        summary=summary,
        metrics={
            "found": total_found, "saved": total_saved, "emailed": total_emailed,
            "linkedin": total_linkedin, "whatsapp": total_whatsapp,
            "skipped_low": total_skipped_low, "scraper_errors": scraper_errors,
            "cities_scraped": len(cities), "lead_source": LEAD_SOURCE,
        },
        budgets={
            "serpapi": {
                "limit": SERPAPI_FREE_LIMIT,
                "note": ("exhausted" if quota_wall and LEAD_SOURCE in ("indeed", "maps")
                         else "monthly free plan"),
            },
        },
    )


def run_reply_handler():
    """
    Check inbox for replies and send follow-ups.
    Run this separately (e.g. daily cron, different from the main scrape loop).
    """
    print(f"[{datetime.now(timezone.utc).isoformat()}] Checking replies...")
    reply_handler.run(since_days=7)
    reply_handler.send_follow_ups(max_per_run=20)
    whatsapp.process_whatsapp_replies(max_per_run=20)
    print("Reply handling complete.")
    run_metrics.write(mode="replies", status="ok",
                      summary="Checked inbox + sent due follow-ups")


def run_analytics():
    """Refresh the niche_analytics tab manually."""
    rows = analytics.run()
    print(f"Analytics updated. {len(rows)} niches tracked.")
    for r in rows:
        print(f"  {r['niche']}: {r['booked_call_rate']:.1%} booked ({r['total_sent']} sent)")


def run_audit_verify(tail_n: int = 20):
    """Verify the audit chain and print the most recent records."""
    result = audit.verify_chain()
    print(f"Audit log: {audit.path}")
    print(f"Chain check: {result}")
    recent = audit.tail(tail_n)
    print(f"\nLast {len(recent)} record(s):")
    for r in recent:
        print(f"  {r['ts']:.0f}  {r['actor']:<14}  {r['action']:<6}  "
              f"{'OK ' if r['ok'] else 'ERR'}  {r['target']}  {r.get('detail', {})}")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "pipeline"

    try:
        if mode == "replies":
            run_reply_handler()
        elif mode == "analytics":
            run_analytics()
        elif mode == "audit":
            run_audit_verify()
        else:
            run()
    except Exception as e:
        # Record the failure for the dashboard, then re-raise so CI still
        # surfaces a red run and the stack trace lands in the Actions log.
        run_metrics.write(mode=mode, status="error", summary=f"{type(e).__name__}: {e}")
        raise
