"""Platform connectors for the social agent. See base.py for the interface
and registry.py for how connectors are selected at runtime."""
from .base import Connector, InboundItem, PostResult
from .registry import enabled_platforms, load_connectors

__all__ = [
    "Connector",
    "InboundItem",
    "PostResult",
    "enabled_platforms",
    "load_connectors",
]
