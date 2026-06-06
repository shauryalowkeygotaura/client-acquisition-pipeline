"""
modules/connectors/discord.py

Discord, free in two halves:
  - POSTING needs only a channel Webhook URL (Server Settings → Integrations →
    Webhooks). No bot, no approval.   DISCORD_WEBHOOK_URL=...
  - READING + REPLYING needs a free bot token with the Message Content intent
    plus the channel id.   DISCORD_BOT_TOKEN=...  DISCORD_CHANNEL_ID=...

If only the webhook is set, the connector is post-only (can_read() == False)
and the orchestrator just won't poll it. Dependency-free (urllib).
"""
from __future__ import annotations

import json
import os
import urllib.request

from .base import Connector, InboundItem, PostResult

_API = "https://discord.com/api/v10"


class DiscordConnector(Connector):
    name = "discord"

    def __init__(self):
        self.webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        self.bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
        self.channel_id = os.getenv("DISCORD_CHANNEL_ID", "")

    def available(self) -> bool:
        # Available if it can do *anything* (post or read).
        return bool(self.webhook or (self.bot_token and self.channel_id))

    def can_read(self) -> bool:
        return bool(self.bot_token and self.channel_id)

    def _bot_request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{_API}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "social-agent (free, 1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}

    # ── verbs ────────────────────────────────────────────────────────────────
    def post(self, text: str, media: list[str] | None = None) -> PostResult:
        if self.webhook:
            try:
                req = urllib.request.Request(
                    self.webhook,
                    data=json.dumps({"content": text}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=30)
                return PostResult(ok=True, platform=self.name)
            except Exception as e:
                return PostResult(ok=False, platform=self.name, error=str(e))
        if self.can_read():
            try:
                res = self._bot_request("POST", f"/channels/{self.channel_id}/messages",
                                        {"content": text})
                return PostResult(ok=True, platform=self.name, item_id=str(res.get("id", "")))
            except Exception as e:
                return PostResult(ok=False, platform=self.name, error=str(e))
        return PostResult(ok=False, platform=self.name, error="no webhook or bot configured")

    def fetch_inbound(self, state) -> list[InboundItem]:
        if not self.can_read():
            return []
        after = state.get_cursor(self.name)
        path = f"/channels/{self.channel_id}/messages?limit=50"
        if after:
            path += f"&after={after}"
        try:
            msgs = self._bot_request("GET", path)
        except Exception as e:
            print(f"    [discord] fetch failed: {e}")
            return []
        items: list[InboundItem] = []
        newest = after
        # Discord returns newest-first; reverse so we process oldest-first.
        for msg in reversed(msgs if isinstance(msgs, list) else []):
            mid = str(msg.get("id", ""))
            newest = mid  # ids are snowflakes → monotonic; last seen = newest
            author = msg.get("author", {})
            if author.get("bot"):
                continue  # never react to bots (including ourselves)
            if state.is_seen(self.name, mid):
                continue
            items.append(InboundItem(
                platform=self.name,
                item_id=mid,
                text=msg.get("content", ""),
                author_handle=author.get("username", ""),
                author_name=author.get("global_name", author.get("username", "")),
                kind="message",
                reply_ref={"channel_id": self.channel_id, "message_id": mid},
                raw=msg,
            ))
        if newest:
            state.set_cursor(self.name, newest)
        return items

    def reply(self, item: InboundItem, text: str) -> bool:
        if not self.can_read():
            return False
        ref = item.reply_ref
        try:
            self._bot_request("POST", f"/channels/{ref['channel_id']}/messages", {
                "content": text,
                "message_reference": {"message_id": ref["message_id"]},
            })
            return True
        except Exception as e:
            print(f"    [discord] reply failed: {e}")
            return False
