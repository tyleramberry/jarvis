"""Voice-provider factory. Add a new service by implementing the same methods."""

from __future__ import annotations

from jarvis.config import AppConfig
from jarvis.providers.mock import LiveMockProvider
from jarvis.providers.realtime import RealtimeClient


def build_provider(config: AppConfig):
    name = config.provider.name
    if name == "mock":
        return LiveMockProvider()
    if name in {"openai", "grok"}:
        return RealtimeClient(config.provider)
    raise ValueError(f"unknown provider {name}")
