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
    reply_handler, analytics, optimizer,
)

# ── Routing thresholds ───────────────────────────────────────────────────────
# high priority (score ≥ 7): full outreach (email + LinkedIn)
# medium priority (4–6):     email only
# low priority (<4):         skip entirely

SCORE_HIGH = 7
SCORE_LOW = 4

# Run analytics + optimizer every N leads saved
OPTIMIZER_INTERVAL = 50


def run():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Pipeline starting...")
    existing = sheets_writer.get_all_leads()
    total_saved = 0
    total_emailed = 0
    total_linkedin = 0
    total_whatsapp = 0
    total_skipped_low = 0

    for city in CITIES:
        print(f"  Scraping Indeed: {city}")
        try:
            jobs = scraper.run(city)
            print(f"    Found {len(jobs)} jobs")
        except Exception as e:
            print(f"    Scraper failed for {city}: {e}")
            continue

        for job in jobs:
            company = job["company_name"]
            try:
                if sheets_writer.domain_exists(job.get("domain"), existing):
                    print(f"    [SKIP] {company} (already in Sheets)")
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
                    continue

                # ── Person-level personalization (only for qualified leads) ──
                data = personalizer.run(data)
                if data.get("person_hook") or data.get("company_hook"):
                    print(f"      [PERSONALIZED] hooks found for {company}")

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
                    continue

                slug = data["slug"]

                # ── Send: high priority → email + LinkedIn + WhatsApp ────
                if score >= SCORE_HIGH:
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
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                    else:
                        print(f"      [NO EMAIL] {company} — no address found (LinkedIn only)")

                    li_sent = linkedin.send(data)
                    if li_sent:
                        sheets_writer.update_field(slug, "linkedin_sent", "TRUE")
                        if not data.get("email"):
                            sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                            sheets_writer.update_channel(slug, "linkedin")
                        total_linkedin += 1

                    wa_sent = whatsapp.send(data)
                    if wa_sent:
                        total_whatsapp += 1

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
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                    else:
                        print(f"      [NO EMAIL] {company} — no address found, skipping")

            except Exception as e:
                print(f"    [ERROR] {company}: {e}")
                sheets_writer.log_error(company, str(e))
                continue

    print(
        f"\nDone. Saved: {total_saved} | Emailed: {total_emailed} | "
        f"LinkedIn: {total_linkedin} | WhatsApp: {total_whatsapp} | "
        f"Skipped (low score): {total_skipped_low}"
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


def run_analytics():
    """Refresh the niche_analytics tab manually."""
    rows = analytics.run()
    print(f"Analytics updated. {len(rows)} niches tracked.")
    for r in rows:
        print(f"  {r['niche']}: {r['booked_call_rate']:.1%} booked ({r['total_sent']} sent)")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "pipeline"

    if mode == "replies":
        run_reply_handler()
    elif mode == "analytics":
        run_analytics()
    else:
        run()
