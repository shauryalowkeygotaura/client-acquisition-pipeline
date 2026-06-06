"""
modules/connectors/base.py

The uniform interface every platform connector implements. The whole "15
platforms" promise reduces to three verbs — post / fetch_inbound / reply —
so the orchestrator and the Groq brain never need to know which platform
they are talking to. Adding a new platform = one new file that subclasses
`Connector`. A connector that lacks credentials reports `available() == False`
and is silently skipped; it can fail to load, but it can never break the loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundItem:
    """One incoming thing worth reacting to (a DM, mention, comment, reply)."""
    platform: str
    item_id: str                       # unique per platform — used for dedupe
    text: str
    author_handle: str = ""
    author_name: str = ""
    kind: str = "message"              # message | mention | comment | reply | dm
    reply_ref: dict[str, Any] = field(default_factory=dict)  # what reply() needs
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostResult:
    ok: bool
    platform: str
    url: str = ""
    item_id: str = ""
    error: str = ""


class Connector(ABC):
    """Base class for a single platform. Subclasses set `name` and read their
    own credentials from the environment in __init__."""

    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """True only if the connector has everything it needs to act (tokens
        etc.). Unavailable connectors are dropped by the registry."""
        raise NotImplementedError

    @abstractmethod
    def post(self, text: str, media: list[str] | None = None) -> PostResult:
        """Publish a piece of content. `media` is an optional list of local
        file paths; connectors that can't do media should post text only."""
        raise NotImplementedError

    @abstractmethod
    def fetch_inbound(self, state: "SocialState") -> list[InboundItem]:  # noqa: F821
        """Return new inbound items since the last poll. Implementations must
        use `state` to advance their own cursor/offset so the same item is
        never returned twice. Returning [] is always valid."""
        raise NotImplementedError

    @abstractmethod
    def reply(self, item: InboundItem, text: str) -> bool:
        """Reply to one inbound item. Return True on success."""
        raise NotImplementedError

    # Sensible default so post-only connectors (e.g. a webhook) don't have to
    # implement reading. Override when the platform supports it.
    def can_read(self) -> bool:
        return True
