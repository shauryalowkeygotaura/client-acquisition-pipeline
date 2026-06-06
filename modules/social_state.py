"""
modules/social_state.py

Tiny JSON-backed state store so the agent never posts twice in a burst, never
replies to the same message twice, and each connector can remember its own
polling cursor. Lives next to run_metrics output in `runs/social_state.json`
so it travels with the repo and can be committed by the same cron that commits
metrics.

Deliberately dependency-free and crash-tolerant: a corrupt/missing file just
starts empty rather than taking down a run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATE_PATH = Path(__file__).resolve().parent.parent / "runs" / "social_state.json"
# Keep the per-platform "seen" list from growing without bound.
_MAX_SEEN_PER_PLATFORM = 500


class SocialState:
    def __init__(self, path: Path | None = None):
        self.path = path or _STATE_PATH
        self._data: dict[str, Any] = self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"platforms": {}, "posts": []}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as e:  # pragma: no cover - best effort
            print(f"    [social_state] failed to save: {e}")

    def _platform(self, name: str) -> dict[str, Any]:
        return self._data.setdefault("platforms", {}).setdefault(
            name, {"seen": [], "cursor": None}
        )

    # ── dedupe ───────────────────────────────────────────────────────────────
    def is_seen(self, platform: str, item_id: str) -> bool:
        return item_id in self._platform(platform)["seen"]

    def mark_seen(self, platform: str, item_id: str) -> None:
        seen = self._platform(platform)["seen"]
        if item_id not in seen:
            seen.append(item_id)
            # Trim oldest, keep it bounded.
            if len(seen) > _MAX_SEEN_PER_PLATFORM:
                del seen[: len(seen) - _MAX_SEEN_PER_PLATFORM]

    # ── per-connector polling cursor (e.g. Telegram update offset) ───────────
    def get_cursor(self, platform: str) -> Any:
        return self._platform(platform)["cursor"]

    def set_cursor(self, platform: str, value: Any) -> None:
        self._platform(platform)["cursor"] = value

    # ── post log (used to throttle posting cadence) ──────────────────────────
    def record_post(self, platform: str, item_id: str, url: str) -> None:
        self._data.setdefault("posts", []).append({
            "platform": platform,
            "item_id": item_id,
            "url": url,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    def last_post_ts(self, platform: str) -> str | None:
        posts = [p for p in self._data.get("posts", []) if p["platform"] == platform]
        return posts[-1]["ts"] if posts else None
