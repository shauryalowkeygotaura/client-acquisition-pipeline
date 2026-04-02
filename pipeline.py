# pipeline.py
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from config import CITIES
from modules import (
    scraper, researcher, enricher, scorer,
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

                # ── Message generation ────────────────────────────────────
                data = generator.run(data)

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

                # ── Send: high priority → email + LinkedIn + WhatsApp ────
                if score >= SCORE_HIGH:
                    if data.get("email"):
                        print(f"      [EMAIL HIGH] → {data['email']}")
                        emailed = email_sender.send(data)
                        if emailed:
                            sheets_writer.update_field(data["slug"], "email_sent", "TRUE")
                            sheets_writer.update_field(
                                data["slug"], "sent_at",
                                datetime.now(timezone.utc).isoformat()
                            )
                            sheets_writer.update_channel(data["slug"], "email")
                            total_emailed += 1
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials")
                    else:
                        print(f"      [NO EMAIL] {company} — no address found (LinkedIn only)")

                    li_sent = linkedin.send(data)
                    if li_sent:
                        sheets_writer.update_field(data["slug"], "linkedin_sent", "TRUE")
                        if not data.get("email"):
                            sheets_writer.update_field(
                                data["slug"], "sent_at",
                                datetime.now(timezone.utc).isoformat()
                            )
                            sheets_writer.update_channel(data["slug"], "linkedin")
                        total_linkedin += 1

                    wa_sent = whatsapp.send(data)
                    if wa_sent:
                        total_whatsapp += 1

                # ── Send: medium priority → email only ───────────────────
                elif score >= SCORE_LOW:
                    if data.get("email"):
                        print(f"      [EMAIL MED] → {data['email']}")
                        emailed = email_sender.send(data)
                        if emailed:
                            sheets_writer.update_field(data["slug"], "email_sent", "TRUE")
                            sheets_writer.update_field(
                                data["slug"], "sent_at",
                                datetime.now(timezone.utc).isoformat()
                            )
                            sheets_writer.update_channel(data["slug"], "email")
                            total_emailed += 1
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials")
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
