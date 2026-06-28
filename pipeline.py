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


from config import CITIES, MAPS_BACKFILL_MIN
from modules import (
    scraper, researcher, enricher, scorer, personalizer,
    generator, sheets_writer, email_sender, linkedin, whatsapp, instagram,
    reply_handler, analytics, optimizer, apollo_scraper, osm_scraper,
    maps_scraper, run_metrics, learning,
)
from modules.security_utils import get_audit_log

audit = get_audit_log()

# Lead source: "indeed" (SerpAPI, default) or "apollo" (Obscura + cookies)
LEAD_SOURCE = os.getenv("LEAD_SOURCE", "indeed").lower()

# ── Free-tier SerpAPI budget guard ───────────────────────────────────────────
# SerpAPI free = 100 searches/month. Scraping all cities every weekday burns
# that in ~2 runs, after which every run silently finds 0 leads ("account has
# run out of searches"). To stay free AND keep producing leads, set
# MAX_CITIES_PER_RUN to a small number: we then scrape a ROTATING window of that
# many cities, advancing by day-of-year so every city is covered over a few
# days. Default 0 = no limit (original all-cities behavior, opt-in).
MAX_CITIES_PER_RUN = int(os.getenv("MAX_CITIES_PER_RUN", "0"))
SERPAPI_FREE_LIMIT = 100  # searches/month on the free plan (shown on dashboard)


def _source_key(module) -> str:
    """Canonical source tag for a lead's origin, written to source_type."""
    if module is apollo_scraper:
        return "apollo"
    if module is osm_scraper:
        return "osm"
    if module is maps_scraper:
        return "maps"
    return "indeed"  # scraper.py (Indeed, with internal google_jobs fallback)


def _stamp_source(jobs: list[dict], key: str) -> None:
    """Tag each lead with where it came from, unless the scraper already did."""
    for job in jobs:
        job.setdefault("source_type", job.get("source") or key)


_INDIA_KEYS = (
    "india", "delhi", "mumbai", "bangalore", "bengaluru", "hyderabad",
    "pune", "jaipur", "chennai", "kolkata", "ahmedabad", "kochi",
)


def _lead_region(location: str) -> str:
    """india vs default — drives the learned channel send order."""
    loc = (location or "").lower()
    return "india" if any(k in loc for k in _INDIA_KEYS) else "default"


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


def run():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Pipeline starting...")
    existing = sheets_writer.get_all_leads()
    learned = learning.load()  # previous run's learned channel order (self-improving)
    total_saved = 0
    total_emailed = 0
    total_linkedin = 0
    total_whatsapp = 0
    total_instagram = 0
    total_skipped_low = 0
    lead_list: list[dict] = []  # published to runs/leads.json for the Command Center

    if LEAD_SOURCE == "apollo":
        source_module, source_label = apollo_scraper, "Apollo"
    elif LEAD_SOURCE == "osm":
        source_module, source_label = osm_scraper, "OSM"
    else:
        source_module, source_label = scraper, "Indeed"

    # ── Preflight: SerpAPI quota ─────────────────────────────────────────────
    # scraper.py runs on SerpAPI. If the free monthly
    # quota is spent, every search returns 0 and we'd silently save nothing.
    # Check remaining searches (free /account call) and auto-fall back to OSM
    # (Overpass: keyless, no quota, no cookies) so the funnel self-heals without
    # any manual intervention. OSM is preferred over Apollo here because Apollo
    # needs live session cookies that expire.
    serp_left: int | None = None
    if LEAD_SOURCE not in ("apollo", "osm"):
        serp_left = scraper.searches_left()
        if serp_left == 0:
            print("  [PREFLIGHT] SerpAPI quota exhausted (0 left this month, resets "
                  "on account anniversary). Falling back to OSM (free, keyless) source.")
            source_module = osm_scraper
            source_label = "OSM (auto-fallback)"

    cities = _select_cities()
    total_found = 0
    scraper_errors = 0
    osm_rescued = False
    maps_backfilled = False
    primary_is_osm = source_module is osm_scraper
    print(f"  Scraping {len(cities)}/{len(CITIES)} cities this run "
          f"(MAX_CITIES_PER_RUN={MAX_CITIES_PER_RUN or 'all'}): {', '.join(cities)}")

    for city in cities:
        print(f"  Scraping {source_label}: {city}")
        jobs: list[dict] = []
        try:
            jobs = source_module.run(city)
            print(f"    Found {len(jobs)} leads")
        except Exception as e:
            scraper_errors += 1
            print(f"    Scraper failed for {city}: {e}")
        _stamp_source(jobs, _source_key(source_module))

        # ── Maps backfill: last *paid* resort ────────────────────────────────
        # When the primary source comes up thin and SerpAPI quota remains, top up
        # from Google Maps (every local clinic/school in the city) before falling
        # to the keyless OSM floor. Maps is paid (SerpAPI), so it only fires while
        # quota is left; once quota is 0, OSM below is the real backstop.
        if (len(jobs) < MAPS_BACKFILL_MIN and not primary_is_osm
                and serp_left != 0):
            try:
                maps_jobs = maps_scraper.run(city)
                _stamp_source(maps_jobs, "maps")
                if maps_jobs:
                    maps_backfilled = True
                    print(f"    [MAPS BACKFILL] {source_label} thin ({len(jobs)}) — "
                          f"maps added {len(maps_jobs)} for {city}")
                    jobs.extend(maps_jobs)
            except Exception as e:
                print(f"    [MAPS BACKFILL] failed for {city}: {e}")

        # Per-city rescue: if everything above produced nothing (dead Apollo
        # cookies, no listings, spent quota, or a transient error) and the
        # primary wasn't already OSM, fall to the free keyless floor so the
        # funnel never sits silently at 0.
        if not jobs and not primary_is_osm:
            try:
                jobs = osm_scraper.run(city)
                _stamp_source(jobs, "osm")
                if jobs:
                    osm_rescued = True
                    print(f"    [OSM RESCUE] {source_label} gave 0 — OSM found {len(jobs)} for {city}")
            except Exception as e:
                print(f"    [OSM RESCUE] failed for {city}: {e}")

        total_found += len(jobs)
        if not jobs:
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

                # ── Message generation ────────────────────────────────────
                data = generator.run(data)

                # ── Generate opt-out token before persisting ──────────────
                data["opt_out_token"] = email_sender.generate_opt_out_token()

                # Carry the lead's origin source through to the Sheet + dashboard.
                data["source_type"] = job.get("source_type", _source_key(source_module))

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
                sent = {"email": False, "linkedin": False,
                        "whatsapp": False, "instagram": False}

                # ── Send: high priority → all channels, in the LEARNED order ──
                # Each channel is a closure with identical behavior to before;
                # they fire in the order learning.py found works best for this
                # lead's region (self-improving). Counters use nonlocal so the
                # closures update run()'s tallies.
                if score >= SCORE_HIGH:
                    def _send_email():
                        nonlocal total_emailed
                        if not data.get("email"):
                            print(f"      [NO EMAIL] {company} — no address found")
                            return
                        print(f"      [EMAIL HIGH] → {data['email']}")
                        ok, msg_id, sender = email_sender.send(data)
                        if ok:
                            sheets_writer.update_field(slug, "email_sent", "TRUE")
                            sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                            sheets_writer.update_field(slug, "message_id", msg_id)
                            sheets_writer.update_field(slug, "sender_account", sender)
                            sheets_writer.update_channel(slug, "email")
                            total_emailed += 1
                            sent["email"] = True
                            audit.append("email_sender", "send", slug, ok=True,
                                         detail={"channel": "email", "score": score,
                                                 "to": data["email"], "message_id": msg_id,
                                                 "sender": sender, "tier": "high"})
                        else:
                            print(f"      [EMAIL FAILED] check Gmail credentials / daily limit")
                            audit.append("email_sender", "send", slug, ok=False,
                                         detail={"channel": "email", "tier": "high"})

                    def _send_linkedin():
                        nonlocal total_linkedin
                        li_sent = linkedin.send(data)
                        if li_sent:
                            sheets_writer.update_field(slug, "linkedin_sent", "TRUE")
                            if not data.get("email"):
                                sheets_writer.update_field(slug, "sent_at", datetime.now(timezone.utc).isoformat())
                                sheets_writer.update_channel(slug, "linkedin")
                            total_linkedin += 1
                            sent["linkedin"] = True
                        audit.append("linkedin", "send", slug, ok=bool(li_sent),
                                     detail={"channel": "linkedin", "score": score})

                    def _send_whatsapp():
                        nonlocal total_whatsapp
                        wa_sent = whatsapp.send(data)
                        if wa_sent:
                            total_whatsapp += 1
                            sent["whatsapp"] = True
                        audit.append("whatsapp", "send", slug, ok=bool(wa_sent),
                                     detail={"channel": "whatsapp", "score": score})

                    def _send_instagram():
                        # Send-only; gated behind INSTAGRAM_ENABLED. Thread is
                        # hidden after send so only repliers resurface — see
                        # modules/instagram.py.
                        nonlocal total_instagram
                        ig_sent = instagram.send(data)
                        if ig_sent:
                            sheets_writer.update_field(slug, "instagram_sent", "TRUE")
                            total_instagram += 1
                            sent["instagram"] = True
                        audit.append("instagram", "send", slug, ok=bool(ig_sent),
                                     detail={"channel": "instagram", "score": score})

                    channel_fns = {
                        "email": _send_email, "linkedin": _send_linkedin,
                        "whatsapp": _send_whatsapp, "instagram": _send_instagram,
                    }
                    region = _lead_region(location)
                    order = (learned.get("channel_order_by_region", {}).get(region)
                             or learning.DEFAULT_CHANNEL_ORDER[region])
                    for ch in order:
                        fn = channel_fns.get(ch)
                        if fn:
                            fn()

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
                            sent["email"] = True
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

                # ── Record for the Command Center lead list (runs/leads.json) ──
                lead_list.append({
                    "label": company,
                    "source": data.get("source_type", ""),
                    "niche": niche,
                    "score": score,
                    "city": city,
                    "phone": data.get("phone", ""),
                    "whatsapp": data.get("whatsapp", "") or data.get("phone", ""),
                    "website": data.get("website", ""),
                    "channels": sent,  # which channels auto-fired for this lead
                })

            except Exception as e:
                print(f"    [ERROR] {company}: {e}")
                sheets_writer.log_error(company, str(e))
                continue

    print(
        f"\nDone. Found: {total_found} | Saved: {total_saved} | Emailed: {total_emailed} | "
        f"LinkedIn: {total_linkedin} | WhatsApp: {total_whatsapp} | "
        f"Instagram: {total_instagram} | Skipped (low score): {total_skipped_low}"
    )

    # Publish the lead list for the Command Center dashboard (runs/leads.json).
    run_metrics.write_leads(lead_list)

    # ── Emit run metrics for the Command Center dashboard ────────────────────
    # "degraded" = the run was clean but the upstream scraper produced nothing,
    # which on the free plan almost always means SerpAPI hit its monthly wall.
    quota_wall = total_found == 0 and total_saved == 0
    if quota_wall:
        status = "degraded"
        osm_in_use = source_label.startswith("OSM")
        apollo_in_use = source_label.startswith("Apollo")
        if serp_left == 0 and osm_in_use:
            # SerpAPI ran dry → auto-fell back to OSM, which still found nothing.
            cause = ("SerpAPI quota exhausted (0 left, resets monthly); auto-fell back to OSM "
                     "but it returned 0 for these cities/niches")
        elif osm_in_use:
            cause = "OSM returned 0 for these cities/niches (no matching OpenStreetMap listings)"
        elif apollo_in_use:
            # Apollo gave 0 (almost always expired cookies); the per-city OSM
            # rescue ran (not primary_is_osm) and also came back empty.
            cause = ("Apollo returned 0 — session cookies likely expired "
                     "(re-run scripts/save_apollo_cookies.py); OSM rescue also found 0")
        else:
            # serp_left==0 always routes to the OSM fallback above, so here the
            # SerpAPI source ran with quota remaining; the OSM rescue also found 0.
            cause = "primary source and OSM rescue both returned 0 (no new listings)"
        summary = f"0 leads from {len(cities)} cities — {cause}"
    else:
        status = "ok"
        prefix = "[OSM rescue] " if osm_rescued else "[+maps] " if maps_backfilled else ""
        summary = (f"{prefix}{total_found} found, {total_saved} new, {total_emailed} emailed, "
                   f"{total_whatsapp} WhatsApp, {total_instagram} IG, {total_linkedin} LinkedIn")
    run_metrics.write(
        mode="scrape",
        status=status,
        summary=summary,
        metrics={
            "found": total_found, "saved": total_saved, "emailed": total_emailed,
            "linkedin": total_linkedin, "whatsapp": total_whatsapp,
            "instagram": total_instagram,
            "skipped_low": total_skipped_low, "scraper_errors": scraper_errors,
            "cities_scraped": len(cities), "lead_source": LEAD_SOURCE,
            "osm_rescued": osm_rescued, "maps_backfilled": maps_backfilled,
            "effective_source": "osm" if (osm_rescued or source_label.startswith("OSM")) else LEAD_SOURCE,
        },
        budgets={
            "serpapi": {
                "limit": SERPAPI_FREE_LIMIT,
                "left": serp_left,
                "note": ("not checked (apollo source)" if serp_left is None
                         else "exhausted" if serp_left == 0
                         else "monthly free plan"),
            },
        },
    )

    # ── Self-improving loop ──────────────────────────────────────────────────
    # Recompute learned levers (best variant per niche, channel order, scoring
    # weights) from accumulated reply/booking outcomes and write runs/learned.json
    # for the NEXT run to read. Best-effort; never raises.
    learning.run()


def run_reply_handler():
    """
    Check inbox for replies and send follow-ups.
    Run this separately (e.g. daily cron, different from the main scrape loop).
    """
    print(f"[{datetime.now(timezone.utc).isoformat()}] Checking replies...")

    # Each external call is isolated so one failure still records the others'
    # work. An unhandled crash would otherwise lose all metrics for the run.
    reply_stats: dict = {}
    followups_sent = 0
    wa_sent = 0
    errors: list[str] = []

    try:
        reply_stats = reply_handler.run(since_days=7) or {}
    except Exception as e:
        errors.append(f"replies:{type(e).__name__}")
        print(f"    [replies] inbox check failed: {e}")

    try:
        followups_sent = reply_handler.send_follow_ups(max_per_run=20) or 0
    except Exception as e:
        errors.append(f"followups:{type(e).__name__}")
        print(f"    [followups] send failed: {e}")

    try:
        wa_sent = whatsapp.process_whatsapp_replies(max_per_run=20) or 0
    except Exception as e:
        errors.append(f"whatsapp:{type(e).__name__}")
        print(f"    [whatsapp] reply processing failed: {e}")

    print("Reply handling complete.")

    metrics = {
        "inbox_msgs": reply_stats.get("inbox_msgs", 0),
        "replies_handled": reply_stats.get("replies_handled", 0),
        "optouts": reply_stats.get("optouts", 0),
        "followups_sent": followups_sent,
        "whatsapp_followups_sent": wa_sent,
    }
    did_work = any(metrics.values())
    if errors:
        status = "error"
        summary = "Partial failure: " + ", ".join(errors)
    elif did_work:
        status = "ok"
        summary = (f"{metrics['replies_handled']} replies, {metrics['followups_sent']} "
                   f"email follow-ups, {metrics['optouts']} opt-outs")
    else:
        status = "ok"
        summary = "Checked inbox + follow-up queue, nothing due"
    run_metrics.write(mode="replies", status=status, summary=summary, metrics=metrics)


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
