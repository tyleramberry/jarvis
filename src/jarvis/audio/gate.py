"""Mute the microphone while the assistant's audio is still in the room."""

from __future__ import annotations

import time


class MicGate:
    """Half-duplex mic policy, plus a short mute around the wake chime.

    When ``half_duplex`` is false the mic stays open during replies so the
    user can interrupt. The chime is still muted, because that sound is
    generated locally and should not become the first thing the model hears.
    """

    def __init__(self, half_duplex: bool, tail_ms: int):
        self.half_duplex = half_duplex
        self.tail_ms = max(0, int(tail_ms))
        self._speaking = False
        self._tail_until = 0.0
        self._chime_until = 0.0

    def begin_chime(self, seconds: float) -> None:
        extra = (self.tail_ms / 1000.0) if self.half_duplex else 0.2
        self._chime_until = max(self._chime_until, time.monotonic() + max(0.0, seconds) + extra)

    def begin_assistant_audio(self) -> None:
        if not self.half_duplex:
            return
        self._speaking = True
        self._tail_until = 0.0

    def end_assistant_audio(self) -> None:
        if not self.half_duplex or not self._speaking:
            return
        self._speaking = False
        self._tail_until = time.monotonic() + (self.tail_ms / 1000.0)

    @property
    def mic_open(self) -> bool:
        now = time.monotonic()
        if now < self._chime_until:
            return False
        if self.half_duplex and (self._speaking or now < self._tail_until):
            return False
        return True
