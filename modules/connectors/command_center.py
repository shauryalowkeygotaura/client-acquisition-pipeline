"""
modules/connectors/command_center.py

The Command Center as a "platform" (replacing Telegram as the primary surface).
It is a publish target, not a messaging inbox: post() publishes content to the
dashboard feed, can_read() is False (a static dashboard has no inbound), and
reply() records the reply into the feed as a sent item for the record.

The actual qualify/reply drafts are pushed into the feed by social_agent via
modules.command_center directly; this connector handles the post() verb so the
unified posting loop "just works" with command_center in SOCIAL_PLATFORMS.
"""
from __future__ import annotations

from modules import command_center
from .base import Connector, InboundItem, PostResult


class CommandCenterConnector(Connector):
    name = "command_center"

    def available(self) -> bool:
        return True  # local file write, no credentials needed

    def can_read(self) -> bool:
        return False  # a static dashboard does not deliver inbound messages

    def post(self, text: str, media: list[str] | None = None) -> PostResult:
        try:
            command_center.add_post(text)
            return PostResult(ok=True, platform=self.name,
                              url="https://shauryalowkeygotaura.github.io/command-center")
        except Exception as e:
            return PostResult(ok=False, platform=self.name, error=str(e))

    def fetch_inbound(self, state) -> list[InboundItem]:
        return []

    def reply(self, item: InboundItem, text: str) -> bool:
        # Record the sent reply on the dashboard for the audit trail.
        command_center.add_draft(
            platform=item.platform, author=item.author_handle,
            category=item.kind, niche="", intent="", draft=text, sent=True,
        )
        return True
