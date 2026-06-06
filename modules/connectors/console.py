"""
modules/connectors/console.py

The zero-config connector. Always available, needs no credentials, costs
nothing — so the whole agent can run end to end on a fresh machine.

- post(): prints the content to stdout (and appends to runs/console_outbox.jsonl)
- fetch_inbound(): reads simulated inbound messages from runs/console_inbox.jsonl,
  one JSON object per line: {"id": "1", "text": "do you build this for clinics?",
  "author": "someguy"}. Lets you exercise qualify+reply for free, offline.
- reply(): prints the reply.

This is what makes the project demoable without signing up for anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import Connector, InboundItem, PostResult

_RUNS = Path(__file__).resolve().parent.parent.parent / "runs"
_INBOX = _RUNS / "console_inbox.jsonl"
_OUTBOX = _RUNS / "console_outbox.jsonl"


class ConsoleConnector(Connector):
    name = "console"

    def available(self) -> bool:
        return True

    def post(self, text: str, media: list[str] | None = None) -> PostResult:
        print("\n-------- [console post] --------")
        print(text)
        if media:
            print(f"(+{len(media)} media: {', '.join(media)})")
        print("--------------------------------")
        try:
            _RUNS.mkdir(parents=True, exist_ok=True)
            with _OUTBOX.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"text": text, "media": media or []}) + "\n")
        except Exception:
            pass
        return PostResult(ok=True, platform=self.name, url="(stdout)", item_id="console")

    def fetch_inbound(self, state) -> list[InboundItem]:
        if not _INBOX.exists():
            return []
        items: list[InboundItem] = []
        # utf-8-sig transparently strips a leading BOM (PowerShell's Out-File
        # writes one), which would otherwise break json.loads on the first line.
        for line in _INBOX.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = str(obj.get("id") or hash(line))
            if state.is_seen(self.name, item_id):
                continue
            items.append(InboundItem(
                platform=self.name,
                item_id=item_id,
                text=obj.get("text", ""),
                author_handle=obj.get("author", "anon"),
                author_name=obj.get("name", obj.get("author", "anon")),
                kind=obj.get("kind", "message"),
                raw=obj,
            ))
        return items

    def reply(self, item: InboundItem, text: str) -> bool:
        print(f"\n  -> [console reply to @{item.author_handle}]: {text}\n")
        return True
