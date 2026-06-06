"""
social_agent.py — the free "posts, replies, qualifies leads across N platforms"
agent. Sibling to pipeline.py; reuses the same Groq brain, run_metrics, and
(optionally) the leads sheet. Runs end to end on a fresh machine with zero
credentials via the console connector.

Modes (python social_agent.py <mode>):
  post     generate one post and publish to every live platform
  engage   poll every readable platform → qualify each inbound → reply
           (DRAFT by default; set SOCIAL_AUTO_REPLY=1 or pass --send to send)
  loop     engage every cycle and post once per POST_EVERY_CYCLES cycles
  once     post + engage a single time (good for a cron tick)

Safety rails:
  - Replies are DRAFTED, not sent, unless explicitly enabled. Drafts are written
    to runs/social_drafts.jsonl for review.
  - Qualified leads are always logged free to runs/social_leads.jsonl, and also
    pushed to the leads sheet only if SOCIAL_SAVE_LEADS=1 (needs Sheets creds).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from modules import command_center, content_engine, run_metrics, social_brain
from modules.connectors import load_connectors
from modules.social_state import SocialState

_RUNS = Path(__file__).resolve().parent / "runs"
_DRAFTS = _RUNS / "social_drafts.jsonl"
_LEADS = _RUNS / "social_leads.jsonl"

AUTO_REPLY = os.getenv("SOCIAL_AUTO_REPLY", "0") == "1"
SAVE_LEADS = os.getenv("SOCIAL_SAVE_LEADS", "0") == "1"
MAX_REPLIES_PER_RUN = int(os.getenv("SOCIAL_MAX_REPLIES", "20"))
POST_EVERY_CYCLES = int(os.getenv("SOCIAL_POST_EVERY_CYCLES", "8"))
LOOP_SLEEP_SECONDS = int(os.getenv("SOCIAL_LOOP_SLEEP", "300"))

# Default content rotation. Override a single run with --series / --topic, or
# replace wholesale by dropping a runs/content_queue.json list of
# {"series": "...", "topic": "..."} objects.
_DEFAULT_QUEUE = [
    {"series": "build-log", "topic": "one thing that broke when a real clinic used the AI receptionist, and the fix"},
    {"series": "myth-vs-reality", "topic": "why 'just use voicemail' quietly loses small businesses money"},
    {"series": "field-note", "topic": "what front-desk staff actually want from an AI that answers calls"},
    {"series": "teardown", "topic": "the single metric that tells you a receptionist setup is leaking leads"},
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, obj: dict) -> None:
    try:
        _RUNS.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")
    except Exception as e:
        print(f"    [social_agent] could not write {path.name}: {e}")


def _pick_content(series: str | None, topic: str | None) -> dict:
    if series and topic:
        return {"series": series, "topic": topic}
    queue_file = _RUNS / "content_queue.json"
    queue = _DEFAULT_QUEUE
    if queue_file.exists():
        try:
            loaded = json.loads(queue_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list) and loaded:
                queue = loaded
        except Exception:
            pass
    idx = datetime.now(timezone.utc).timetuple().tm_yday % len(queue)
    return queue[idx]


# ── post ─────────────────────────────────────────────────────────────────────
def do_post(series: str | None = None, topic: str | None = None,
            dry_run: bool = False) -> dict:
    connectors = load_connectors()
    state = SocialState()
    item = _pick_content(series, topic)
    text = content_engine.generate_post(item["series"], item["topic"])
    print(f"\n[post] series={item['series']}\n{text}\n")

    results = {"posted": 0, "failed": 0, "platforms": []}
    for conn in connectors:
        if dry_run:
            print(f"    [dry-run] would post to {conn.name}")
            results["platforms"].append({"platform": conn.name, "ok": None})
            continue
        res = conn.post(text)
        results["platforms"].append({"platform": conn.name, "ok": res.ok,
                                     "error": res.error})
        if res.ok:
            results["posted"] += 1
            state.record_post(conn.name, res.item_id, res.url)
            print(f"    [posted] {conn.name}")
        else:
            results["failed"] += 1
            print(f"    [post failed] {conn.name}: {res.error}")
    state.save()
    return {**results, "text": text, "series": item["series"]}


# ── engage (reply + qualify) ───────────────────────────────────────────────────
def do_engage(send: bool = False, max_replies: int = MAX_REPLIES_PER_RUN) -> dict:
    connectors = load_connectors()
    state = SocialState()
    will_send = send or AUTO_REPLY

    stats = {"seen": 0, "leads": 0, "engaged": 0, "ignored": 0,
             "replied": 0, "drafted": 0}

    for conn in connectors:
        if not conn.can_read():
            continue
        try:
            inbound = conn.fetch_inbound(state)
        except Exception as e:
            print(f"    [engage] {conn.name} fetch failed: {e}")
            continue

        for item in inbound:
            if stats["replied"] + stats["drafted"] >= max_replies:
                break
            stats["seen"] += 1
            cls = social_brain.classify(item.text, author=item.author_handle)
            category = cls.get("category", social_brain.ENGAGE)

            # Always mark seen so we never double-process, regardless of outcome.
            state.mark_seen(conn.name, item.item_id)

            if category == social_brain.IGNORE:
                stats["ignored"] += 1
                continue

            if category == social_brain.LEAD:
                stats["leads"] += 1
                _capture_lead(item, cls)
            else:
                stats["engaged"] += 1

            reply_text = social_brain.craft_reply(
                item.text, cls, author=item.author_handle)

            sent_ok = False
            if will_send:
                sent_ok = conn.reply(item, reply_text)
                if sent_ok:
                    stats["replied"] += 1
                    print(f"    [replied] {conn.name} @{item.author_handle} ({category})")
                else:
                    print(f"    [reply failed] {conn.name} @{item.author_handle}")
            else:
                stats["drafted"] += 1
                # Full draft (incl. raw inbound + real handle) stays LOCAL only.
                _append_jsonl(_DRAFTS, {
                    "ts": _utcnow(), "platform": conn.name,
                    "author": item.author_handle, "category": category,
                    "incoming": item.text, "draft_reply": reply_text,
                    "reply_ref": item.reply_ref,
                })
                print(f"    [draft] {conn.name} @{item.author_handle} ({category}) — "
                      f"saved to runs/social_drafts.jsonl + Command Center")

            # Publish a privacy-safe record to the Command Center dashboard
            # (handle redacted, raw inbound omitted — see modules/command_center).
            command_center.add_draft(
                platform=conn.name, author=item.author_handle, category=category,
                niche=cls.get("niche", ""), intent=cls.get("intent", ""),
                draft=reply_text, sent=sent_ok,
            )

    state.save()
    return stats


def _capture_lead(item, cls: dict) -> None:
    """Log every qualified lead for free; optionally also push to the sheet."""
    record = {
        "ts": _utcnow(), "platform": item.platform,
        "handle": item.author_handle, "name": item.author_name,
        "niche": cls.get("niche", "unknown"), "intent": cls.get("intent", "low"),
        "summary": cls.get("summary", ""), "message": item.text,
    }
    _append_jsonl(_LEADS, record)
    # Aggregate-only on the public dashboard (no identity, no message).
    command_center.add_lead(item.platform, record["niche"], record["intent"])
    print(f"    [LEAD] {item.platform} @{item.author_handle} "
          f"(niche={record['niche']}, intent={record['intent']})")

    if not SAVE_LEADS:
        return
    try:
        from modules import inbound_intake
        inbound_intake.intake(
            message=item.text,
            surface="manual",
            identity={"handle": item.author_handle, "name": item.author_name},
            raw_meta={"platform": item.platform, "item_id": item.item_id},
        )
    except Exception as e:
        print(f"    [social_agent] sheet save skipped: {e}")


# ── modes ──────────────────────────────────────────────────────────────────────
def run_once(send: bool = False) -> None:
    post_res = do_post()
    engage_res = do_engage(send=send)
    status = "ok"
    summary = (f"posted to {post_res['posted']} platform(s); "
               f"{engage_res['seen']} inbound -> {engage_res['leads']} leads, "
               f"{engage_res['replied']} replied, {engage_res['drafted']} drafted")
    run_metrics.write(mode="social", status=status, summary=summary,
                      metrics={**engage_res, "posted": post_res["posted"]})
    print(f"\n[once] {summary}")


def run_loop(send: bool = False) -> None:
    cycle = 0
    print(f"[loop] every {LOOP_SLEEP_SECONDS}s; post every {POST_EVERY_CYCLES} cycles; "
          f"auto_reply={send or AUTO_REPLY}")
    while True:
        cycle += 1
        print(f"\n=== cycle {cycle} @ {_utcnow()} ===")
        try:
            if cycle % POST_EVERY_CYCLES == 1:
                do_post()
            engage = do_engage(send=send)
            run_metrics.write(mode="social", status="ok",
                              summary=f"cycle {cycle}: {engage['seen']} inbound, "
                                      f"{engage['leads']} leads",
                              metrics=engage)
        except Exception as e:
            print(f"    [loop] cycle failed: {e}")
            run_metrics.write(mode="social", status="error",
                              summary=f"cycle {cycle} crashed: {type(e).__name__}: {e}")
        time.sleep(LOOP_SLEEP_SECONDS)


def _parse_flag(name: str) -> str | None:
    flag = f"--{name}"
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    send = "--send" in sys.argv
    dry = "--dry-run" in sys.argv

    try:
        if mode == "post":
            do_post(series=_parse_flag("series"), topic=_parse_flag("topic"), dry_run=dry)
        elif mode == "engage":
            do_engage(send=send)
        elif mode == "loop":
            run_loop(send=send)
        else:  # once
            run_once(send=send)
    except KeyboardInterrupt:
        print("\n[social_agent] stopped.")
    except Exception as e:
        run_metrics.write(mode="social", status="error",
                          summary=f"{type(e).__name__}: {e}")
        raise
