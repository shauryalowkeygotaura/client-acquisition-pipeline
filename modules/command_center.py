"""
modules/command_center.py

Publishes the social agent's activity to the Command Center dashboard
(github.com/shauryalowkeygotaura/command-center, static GitHub Pages). The
dashboard has no backend, so — exactly like run_metrics — we drop a JSON file
into runs/ that CI commits and the dashboard fetches over raw.githubusercontent.

PRIVACY: the Command Center is PUBLIC. So this file follows the same rule as the
CALL LIST (numbers stay local, only summaries go public):
  - posts            -> published in full (they get posted publicly anyway)
  - draft replies    -> published (agent-authored, not third-party content)
  - inbound authors  -> REDACTED ("drsharma" -> "d••••a")
  - raw inbound text  -> NEVER written here (stays in the local gitignored
                        runs/social_drafts.jsonl for your eyes only)
  - leads            -> aggregate counts by niche/intent only, no identities

This is the "use the Command Center instead of Telegram" surface: posts land
here, drafts queue here for review, lead stats show here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FEED = Path(__file__).resolve().parent.parent / "runs" / "social_feed.json"

_MAX_POSTS = 10
_MAX_DRAFTS = 15


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_handle(handle: str) -> str:
    """drsharma -> d••••a ; keep first+last char so you can still recognise it
    without publishing the full identity."""
    h = (handle or "").strip().lstrip("@")
    if not h:
        return "anon"
    if len(h) <= 2:
        return h[0] + "•"
    return f"{h[0]}{'•' * (len(h) - 2)}{h[-1]}"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_FEED.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _blank() -> dict[str, Any]:
    return {
        "pipeline": "social-agent",
        "ts": _utcnow(),
        "stats": {"posts": 0, "drafts_pending": 0, "leads": 0},
        "latest_post": None,
        "posts": [],
        "drafts": [],
        "leads": {"total": 0, "by_niche": {}, "by_intent": {}},
    }


def _save(data: dict[str, Any]) -> None:
    data["ts"] = _utcnow()
    # drafts_pending = unsent drafts currently in the window
    data["stats"]["drafts_pending"] = sum(1 for d in data.get("drafts", []) if not d.get("sent"))
    try:
        _FEED.parent.mkdir(parents=True, exist_ok=True)
        _FEED.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:  # pragma: no cover - best effort
        print(f"    [command_center] failed to write feed: {e}")


# ── public API (called by social_agent / the connector) ───────────────────────
def add_post(text: str, series: str = "") -> None:
    data = _load() or _blank()
    entry = {"ts": _utcnow(), "series": series, "text": text}
    data.setdefault("posts", []).insert(0, entry)
    data["posts"] = data["posts"][:_MAX_POSTS]
    data["latest_post"] = entry
    data.setdefault("stats", {}).setdefault("posts", 0)
    data["stats"]["posts"] += 1
    _save(data)


def add_draft(platform: str, author: str, category: str, niche: str,
              intent: str, draft: str, sent: bool = False) -> None:
    data = _load() or _blank()
    data.setdefault("drafts", []).insert(0, {
        "ts": _utcnow(),
        "platform": platform,
        "who": redact_handle(author),
        "category": category,
        "niche": niche,
        "intent": intent,
        "draft": draft,
        "sent": sent,
    })
    data["drafts"] = data["drafts"][:_MAX_DRAFTS]
    _save(data)


def add_lead(platform: str, niche: str, intent: str) -> None:
    data = _load() or _blank()
    leads = data.setdefault("leads", {"total": 0, "by_niche": {}, "by_intent": {}})
    leads["total"] = leads.get("total", 0) + 1
    leads["by_niche"][niche] = leads["by_niche"].get(niche, 0) + 1
    leads["by_intent"][intent] = leads["by_intent"].get(intent, 0) + 1
    data.setdefault("stats", {})["leads"] = leads["total"]
    _save(data)
