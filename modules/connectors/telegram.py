"""
modules/connectors/telegram.py

Telegram via the free Bot API — no paid tier, ever. One bot token gets you
posting to a channel, reading DMs/group messages, and replying in-thread.

Setup (all free):
  1. Talk to @BotFather → /newbot → copy the token.
  2. TELEGRAM_BOT_TOKEN=<token>
  3. TELEGRAM_CHANNEL_ID=@yourchannel   (for posting; add the bot as admin)
  Reading uses long-poll getUpdates with an offset persisted in social_state,
  so each message is delivered exactly once.

Dependency-free: uses urllib so it adds nothing to requirements.txt.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .base import Connector, InboundItem, PostResult

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramConnector(Connector):
    name = "telegram"

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.channel = os.getenv("TELEGRAM_CHANNEL_ID", "")

    def available(self) -> bool:
        return bool(self.token)

    # ── low-level HTTP ───────────────────────────────────────────────────────
    def _call(self, method: str, params: dict) -> dict:
        url = _API.format(token=self.token, method=method)
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    # ── verbs ────────────────────────────────────────────────────────────────
    def post(self, text: str, media: list[str] | None = None) -> PostResult:
        if not self.channel:
            return PostResult(ok=False, platform=self.name,
                              error="TELEGRAM_CHANNEL_ID not set")
        try:
            res = self._call("sendMessage", {"chat_id": self.channel, "text": text})
            mid = str(res.get("result", {}).get("message_id", ""))
            return PostResult(ok=res.get("ok", False), platform=self.name, item_id=mid)
        except Exception as e:
            return PostResult(ok=False, platform=self.name, error=str(e))

    def fetch_inbound(self, state) -> list[InboundItem]:
        offset = state.get_cursor(self.name) or 0
        try:
            res = self._call("getUpdates", {"offset": offset, "timeout": 0})
        except Exception as e:
            print(f"    [telegram] getUpdates failed: {e}")
            return []
        items: list[InboundItem] = []
        max_update = offset
        for upd in res.get("result", []):
            update_id = upd.get("update_id", 0)
            max_update = max(max_update, update_id + 1)  # next offset = last+1
            msg = upd.get("message") or upd.get("channel_post")
            if not msg or not msg.get("text"):
                continue
            item_id = f"{msg['chat']['id']}:{msg['message_id']}"
            if state.is_seen(self.name, item_id):
                continue
            frm = msg.get("from", {})
            items.append(InboundItem(
                platform=self.name,
                item_id=item_id,
                text=msg["text"],
                author_handle=frm.get("username", ""),
                author_name=(frm.get("first_name", "") + " " + frm.get("last_name", "")).strip(),
                kind="dm" if msg["chat"].get("type") == "private" else "message",
                reply_ref={"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]},
                raw=msg,
            ))
        # Advance the long-poll offset so these updates are not re-fetched.
        state.set_cursor(self.name, max_update)
        return items

    def reply(self, item: InboundItem, text: str) -> bool:
        ref = item.reply_ref
        if not ref.get("chat_id"):
            return False
        try:
            res = self._call("sendMessage", {
                "chat_id": ref["chat_id"],
                "text": text,
                "reply_to_message_id": ref.get("message_id"),
            })
            return res.get("ok", False)
        except Exception as e:
            print(f"    [telegram] reply failed: {e}")
            return False
