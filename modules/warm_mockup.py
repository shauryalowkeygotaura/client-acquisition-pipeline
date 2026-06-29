"""
modules/warm_mockup.py - warm-only asset generator (feature b of the hub).

For ONE enthusiastic-yes lead this produces the three slug-keyed assets the
integration contract (assetLifecycle) describes, and writes the link back to the
Sheet:
  1. configs/<slug>.json  - the per-lead voice override (demo_builder)
  2. runs/video_pitches/<slug>.mp4 - the personalized pitch video (video_pitch)
  3. demo_url             - deterministic https://<host>/demo/<slug>, stored on
                            the Sheet via sheets_writer.update_field

GATE: this reuses video_pitch.is_qualified EXACTLY - the same enthusiastic-yes
gate (reply_status==interested OR booked_call==yes OR stage==booked). A cold
lead never reaches here, so cold leads never get a config/video/demo_url. There
is one shared gate, not two that can drift.

All three assets agree on slug because we resolve it once (demo_builder.resolve_slug,
which reuses video_pitch.slugify) and pin lead['slug'] before calling
video_pitch.make - so the config stem, the mp4 filename, and the demo_url slug
are identical.

Cost: $0. demo_builder is keyless/offline; video_pitch is Playwright + ffmpeg +
edge-tts (+ optional Groq free tier). Only the Sheet write-back needs creds, and
it is best-effort (a creds-less local run still produces the local assets).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from modules import demo_builder, video_pitch

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_assets(lead: dict, force: bool = False, write_sheet: bool = True) -> dict | None:
    """Build the warm assets for one qualified lead. Returns a summary dict, or
    None if the lead is not a qualified (enthusiastic-yes) client.

    write_sheet=False skips the Sheet write-back (useful for a creds-less local
    dry run that still wants the config + mp4 on disk).
    """
    name = lead.get("company_name") or lead.get("label") or "lead"

    # Same gate as video_pitch - reused, not re-implemented.
    if not video_pitch.is_qualified(lead):
        log.info("warm_mockup SKIP %s: not a qualified client "
                 "(needs reply_status=interested, booked_call=yes, or stage=booked)", name)
        return None

    # Resolve the slug ONCE and pin it so all three assets share it.
    slug = demo_builder.resolve_slug(lead)
    lead = {**lead, "slug": slug}

    # 1. Per-lead voice config (reuses the lead's vapi_prompt). A lead with no
    #    vapi_prompt raises DemoConfigError - we log and continue to the video,
    #    rather than crash the whole asset build.
    config_path = None
    try:
        config_path = demo_builder.write_config(lead)
        log.info("warm_mockup: wrote voice config %s", config_path)
    except demo_builder.DemoConfigError as e:
        log.warning("warm_mockup: no voice config for %s (%s)", slug, e)

    # 2. Personalized pitch video (unchanged gate/output - same is_qualified).
    mp4_path = None
    try:
        mp4_path = video_pitch.make(lead, force=force)
    except Exception as e:
        log.error("warm_mockup: video_pitch.make failed for %s: %s", slug, e)

    # 3. Deterministic demo_url + Sheet write-back.
    url = demo_builder.demo_url(slug)
    if write_sheet:
        try:
            from modules import sheets_writer
            sheets_writer.update_field(slug, "demo_url", url)
            sheets_writer.update_field(slug, "demo_generated_at", _now_iso())
            log.info("warm_mockup: stored demo_url for %s -> %s", slug, url)
        except Exception as e:
            # Never let a Sheets hiccup throw away the locally-built assets.
            log.error("warm_mockup: could not write demo_url for %s: %s", slug, e)

    return {
        "slug": slug,
        "config_path": str(config_path) if config_path else None,
        "mp4_path": str(mp4_path) if mp4_path else None,
        "demo_url": url,
    }


def make_batch(force: bool = False, limit: int | None = None, write_sheet: bool = True) -> list[dict]:
    """Build warm assets for every QUALIFIED lead in the Sheet.

    Reads the Sheet (same reason as video_pitch.make_batch - the qualification
    signals live there). Needs Sheets creds, so run via
    `doppler run -- python -m modules.warm_mockup batch`.
    """
    from modules import sheets_writer
    leads = sheets_writer.get_all_leads()
    qualified = [l for l in leads if video_pitch.is_qualified(l)]
    log.info("warm_mockup: %d/%d leads qualified - building assets for those only",
             len(qualified), len(leads))
    if limit:
        qualified = qualified[:limit]
    out = []
    for lead in qualified:
        try:
            res = make_assets(lead, force=force, write_sheet=write_sheet)
            if res:
                out.append(res)
        except Exception as e:
            log.error("warm_mockup: FAILED for %s: %s", lead.get("company_name", "?"), e)
    log.info("warm_mockup: done - %d leads got warm assets", len(out))
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        make_batch(force="--force" in sys.argv, limit=lim)
    else:
        # Offline demo: a qualified lead, no Sheet write. Builds config + mp4 locally.
        demo = {
            "company_name": "Smile Dental Studio",
            "niche": "dental",
            "city": "Jaipur",
            "location": "Jaipur, Rajasthan",
            "company_hook": "Your clinic's Google page shows you're closed Sundays.",
            "vapi_prompt": "You are the warm, efficient front desk for Smile Dental Studio in Jaipur.",
            "reply_status": "interested",
            "slug": "demo-smile-dental",
        }
        print(make_assets(demo, force=True, write_sheet=False))
