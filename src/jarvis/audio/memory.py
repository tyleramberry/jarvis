"""In-memory audio stand-ins used by the demo and tests."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import numpy as np


class QueueMic:
    """Yields prepared frames, then returns None. ``delay`` is the pause between frames."""

    def __init__(self, frames: list[np.ndarray], delay: float = 0.0):
        self._frames = list(frames)
        self.delay = delay
        self.failed = False

    async def read(self) -> np.ndarray | None:
        if not self._frames:
            await asyncio.sleep(0.05)
            return None
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._frames.pop(0)

    def drain(self) -> None:
        self._frames.clear()


class MemoryPlayer:
    """Plays by advancing a clock. ``poll`` marks the buffer idle when its audio ends."""

    def __init__(self) -> None:
        self.on_active: Callable[[], None] | None = None
        self.on_idle: Callable[[], None] | None = None
        self.cleared = 0
        self.enqueued = 0
        self._idle = True
        self._idle_at = 0.0
        self._item_id: str | None = None
        self._played_ms = 0
        self._started = 0.0

    @property
    def is_idle(self) -> bool:
        return self._idle

    def enqueue(self, pcm: np.ndarray, item_id: str | None, api_rate: int) -> None:
        samples = np.asarray(pcm).reshape(-1)
        duration = (samples.size / api_rate) if api_rate else 0.0
        now = time.monotonic()
        self.enqueued += 1
        self._item_id = item_id
        if self._idle:
            self._idle = False
            self._started = now
            self._idle_at = now + duration
            if self.on_active:
                self.on_active()
        else:
            self._idle_at = max(self._idle_at, now) + duration

    def poll(self) -> None:
        if self._idle:
            return
        if time.monotonic() >= self._idle_at:
            self._played_ms = int((time.monotonic() - self._started) * 1000)
            self._idle = True
            if self.on_idle:
                self.on_idle()

    def clear(self) -> None:
        self.cleared += 1
        was_playing = not self._idle
        self._idle = True
        self._idle_at = 0.0
        if was_playing and self.on_idle:
            self.on_idle()

    def played_ms(self) -> int:
        if self._idle:
            return self._played_ms
        return int((time.monotonic() - self._started) * 1000)

    def current_item_id(self) -> str | None:
        return self._item_id

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        import asyncio

        deadline = time.monotonic() + timeout
        while not self._idle:
            self.poll()
            if self._idle:
                return
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(0.01)
