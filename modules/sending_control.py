"""
modules/sending_control.py — one kill switch for all outbound email.

Borrowed from Harvey (`harvey sending pause`), 2026-10-01. Two ways to pull it:
  - by hand:  gh variable set SENDING_PAUSED --body 1   (CI reads it as env)
              gh variable set SENDING_PAUSED --body 0   to resume
  - by itself: reply_handler pauses when the bounce rate crosses
               BOUNCE_PAUSE_RATE, by committing runs/sending_paused.json.
               A machine-set pause only clears when that file is deleted
               (or resume() runs), so a bad week can't un-pause itself.

Why bounces get a hard stop: Gmail scores the SENDING account on bounces.
Past ~5% the account starts landing in spam for everyone, including the leads
who would have replied. Harvey trips at 5%; so does this.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FLAG = Path(__file__).resolve().parent.parent / "runs" / "sending_paused.json"
BOUNCE_PAUSE_RATE = float(os.getenv("BOUNCE_PAUSE_RATE", "0.05"))
BOUNCE_MIN_SENDS = max(1, int(os.getenv("BOUNCE_MIN_SENDS", "20") or 20))  # don't trip on 1 of 3


def status() -> dict:
    if os.getenv("SENDING_PAUSED", "").strip() == "1":
        return {"paused": True, "by": "manual", "reason": "SENDING_PAUSED=1"}
    try:
        data = json.loads(FLAG.read_text(encoding="utf-8"))
        if data.get("paused"):
            return {"paused": True, "by": "auto", **data}
    except (OSError, ValueError):
        pass
    return {"paused": False}


def is_paused() -> bool:
    return status()["paused"]


def pause(reason: str) -> None:
    try:
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.write_text(json.dumps({
            "paused": True, "reason": reason,
            "since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2), encoding="utf-8")
    except OSError as e:  # loud, because a pause that didn't stick is a lie
        print(f"[sending_control] FAILED to write pause flag: {e}")


def resume() -> None:
    FLAG.unlink(missing_ok=True)


def check_bounce_rate(sent: int, bounced: int) -> bool:
    """Pause if the rate is over the line. Returns True if it paused."""
    if sent >= BOUNCE_MIN_SENDS and bounced / sent >= BOUNCE_PAUSE_RATE:
        if not is_paused():
            pause(f"bounce rate {bounced}/{sent} = {bounced / sent:.0%} over 14 days "
                  f">= {BOUNCE_PAUSE_RATE:.0%}")
        return True
    return False
