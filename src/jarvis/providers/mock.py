"""Providers that never open a network socket.

ScriptedProvider is the demo. LiveMockProvider is ``jarvis run --mock``:
it listens to the real microphone, calls the local clock tool, and plays
a tone (or the macOS ``say`` command) so you can test the room without
an API key.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from jarvis.audio.chime import render_chime
from jarvis.audio.signal import rms_int16
from jarvis.providers.events import ProviderEvent

log = logging.getLogger(__name__)


class ScriptedProvider:
    """Emit a fixed reply the first time the session sends audio."""

    def __init__(
        self,
        *,
        half_duplex_style: bool = True,
        audio_ms: int = 200,
        sample_rate: int = 24000,
        trigger_chunks: int = 2,
    ):
        self.half_duplex_style = half_duplex_style
        self.audio_ms = audio_ms
        self.sample_rate = sample_rate
        self.trigger_chunks = trigger_chunks
        self.sent_audio: list[bytes] = []
        self.tool_results: list[tuple[str, str]] = []
        self.cancels = 0
        self.truncates: list[tuple[str, int]] = []
        self.configured = False
        self._queue: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()
        self._replied = False

    async def connect(self) -> None:
        return None

    async def configure(self, **kwargs) -> None:
        self.configured = True
        self.sample_rate = int(kwargs.get("sample_rate") or self.sample_rate)

    async def send_audio(self, pcm16: bytes) -> None:
        self.sent_audio.append(pcm16)
        if self._replied or len(self.sent_audio) < self.trigger_chunks:
            return
        self._replied = True
        samples = int(self.sample_rate * (self.audio_ms / 1000))
        tone = np.full(samples, 1200, dtype="<i2").tobytes()
        await self._queue.put(ProviderEvent(kind="response_started"))
        await self._queue.put(ProviderEvent(kind="audio", audio=tone, item_id="mock-item"))
        # speech_started is a barge-in when the mic stays open, and a no-op
        # interruption when half-duplex is muting playback. speech_stopped
        # releases the silence timer afterwards.
        await self._queue.put(ProviderEvent(kind="speech_started"))
        await self._queue.put(ProviderEvent(kind="speech_stopped"))
        await self._queue.put(
            ProviderEvent(
                kind="function_call",
                name="get_current_datetime",
                call_id="mock-call",
                arguments="{}",
            )
        )
        await self._queue.put(ProviderEvent(kind="response_done"))

    async def cancel_response(self) -> None:
        self.cancels += 1

    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        self.truncates.append((item_id, audio_end_ms))

    async def send_tool_result(self, call_id: str, output: str) -> None:
        self.tool_results.append((call_id, output))

    async def request_response(self) -> None:
        await self._queue.put(ProviderEvent(kind="assistant_transcript", text="mock reply"))
        await self._queue.put(ProviderEvent(kind="response_started"))
        await self._queue.put(ProviderEvent(kind="response_done"))

    async def events(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        await self._queue.put(None)


class LiveMockProvider:
    """Local stand-in for a realtime model. Useful before an API key is set."""

    def __init__(self, rms_threshold: float = 800, min_speech_ms: float = 250, end_silence_ms: float = 600):
        self.rms_threshold = rms_threshold
        self.min_speech_ms = min_speech_ms
        self.end_silence_ms = end_silence_ms
        self.sample_rate = 24000
        self._queue: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._heard = False
        self._last_output = ""

    async def connect(self) -> None:
        return None

    async def configure(self, **kwargs) -> None:
        self.sample_rate = int(kwargs.get("sample_rate") or self.sample_rate)

    async def send_audio(self, pcm16: bytes) -> None:
        samples = np.frombuffer(pcm16, dtype="<i2")
        duration_ms = (samples.size / self.sample_rate) * 1000 if self.sample_rate else 0
        if rms_int16(samples) >= self.rms_threshold:
            self._speech_ms += duration_ms
            self._silence_ms = 0.0
            self._heard = True
            return
        if not self._heard:
            return
        self._silence_ms += duration_ms
        if self._speech_ms >= self.min_speech_ms and self._silence_ms >= self.end_silence_ms:
            self._heard = False
            self._speech_ms = 0.0
            self._silence_ms = 0.0
            await self._queue.put(ProviderEvent(kind="speech_stopped"))
            await self._queue.put(
                ProviderEvent(
                    kind="function_call",
                    name="get_current_datetime",
                    call_id="mock-clock",
                    arguments="{}",
                )
            )
            await self._queue.put(ProviderEvent(kind="response_done"))

    async def cancel_response(self) -> None:
        return None

    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        return None

    async def send_tool_result(self, call_id: str, output: str) -> None:
        self._last_output = output

    async def request_response(self) -> None:
        text = self._last_output or "I don't have a reply yet."
        pcm = await asyncio.to_thread(synthesize_reply, text, self.sample_rate)
        await self._queue.put(ProviderEvent(kind="response_started"))
        await self._queue.put(ProviderEvent(kind="audio", audio=pcm.tobytes(), item_id="mock-say"))
        await self._queue.put(ProviderEvent(kind="assistant_transcript", text=text))
        await self._queue.put(ProviderEvent(kind="response_done"))

    async def events(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        await self._queue.put(None)


def synthesize_reply(text: str, sample_rate: int) -> np.ndarray:
    """Speak ``text`` with macOS ``say`` when it is available, else a chime."""
    if sys.platform == "darwin" and shutil.which("say") and shutil.which("afconvert"):
        try:
            return _say_to_pcm(text, sample_rate)
        except Exception:
            log.warning("macOS say failed; playing a tone instead", exc_info=True)
    return render_chime(sample_rate)


def _say_to_pcm(text: str, sample_rate: int) -> np.ndarray:
    spoken = " ".join(text.split())[:400] or "Okay."
    with tempfile.TemporaryDirectory(prefix="jarvis-say-") as directory:
        aiff = Path(directory) / "reply.aiff"
        wav = Path(directory) / "reply.wav"
        subprocess.run(["say", "-o", str(aiff), spoken], check=True, timeout=30)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{sample_rate}", str(aiff), str(wav)],
            check=True,
            timeout=30,
        )
        with wave.open(str(wav), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            channels = handle.getnchannels()
        pcm = np.frombuffer(frames, dtype="<i2")
        if channels > 1:
            pcm = pcm.reshape(-1, channels)[:, 0]
        return pcm.copy()
