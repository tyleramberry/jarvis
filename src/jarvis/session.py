"""One conversation, from just after the wake word until silence."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from jarvis.audio.gate import MicGate
from jarvis.audio.signal import StreamResampler, rms_int16
from jarvis.providers.events import ProviderEvent
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class SessionOptions:
    half_duplex: bool
    tail_ms: int
    silence_timeout_sec: float
    max_duration_sec: float
    local_barge_in: bool
    barge_in_rms: float
    barge_in_guard_ms: int
    tool_flush_sec: float
    api_sample_rate: int
    capture_sample_rate: int


@dataclass
class SessionResult:
    reason: str
    chunks_sent: int = 0
    chunks_dropped: int = 0
    tool_calls: list[str] = field(default_factory=list)
    assistant_transcript: str = ""
    user_transcript: str = ""
    barge_ins: int = 0


async def run_session(
    *,
    provider,
    player,
    mic,
    tools: ToolRegistry,
    gate: MicGate,
    options: SessionOptions,
) -> SessionResult:
    """Stream the mic until silence, a device failure, or the provider drops."""
    result = SessionResult(reason="silence")
    stop = asyncio.Event()
    resampler = StreamResampler(options.capture_sample_rate, options.api_sample_rate)
    state = _TurnState(options)

    player.on_active = gate.begin_assistant_audio
    player.on_idle = gate.end_assistant_audio

    async def send_loop() -> None:
        while not stop.is_set():
            chunk = await mic.read()
            if chunk is None:
                if getattr(mic, "failed", False):
                    result.reason = "device_lost"
                    stop.set()
                    return
                continue
            player.poll()
            if not gate.mic_open:
                result.chunks_dropped += 1
                continue
            pcm = resampler.process(np.asarray(chunk, dtype=np.int16))
            if pcm.size == 0:
                continue
            if _should_barge_locally(pcm, player, gate, state, options):
                await _barge_in(provider, player, state, result, "local energy")
            try:
                await provider.send_audio(pcm.astype("<i2").tobytes())
            except Exception as exc:
                log.warning("sending audio failed: %s", exc)
                result.reason = "error"
                stop.set()
                return
            result.chunks_sent += 1

    async def recv_loop() -> None:
        try:
            async for event in provider.events():
                if stop.is_set():
                    return
                await _handle_event(event, provider, player, tools, gate, state, result, options)
            if not stop.is_set() and result.reason == "silence":
                result.reason = "remote_close"
                stop.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("realtime receive failed: %s", exc)
            result.reason = "error"
            stop.set()

    async def watch_loop() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.05)
            player.poll()
            if getattr(mic, "failed", False) or not _player_healthy(player):
                result.reason = "device_lost"
                stop.set()
                return
            if state.failed:
                result.reason = "error"
                stop.set()
                return
            now = time.monotonic()
            if now - state.started >= options.max_duration_sec:
                result.reason = "max_duration"
                stop.set()
                return
            if state.busy(player, gate):
                state.last_idle = now
                continue
            if now - state.last_idle >= options.silence_timeout_sec:
                result.reason = "silence"
                stop.set()
                return

    sender = asyncio.create_task(send_loop(), name="session-send")
    receiver = asyncio.create_task(recv_loop(), name="session-recv")
    watcher = asyncio.create_task(watch_loop(), name="session-watch")
    try:
        await stop.wait()
    finally:
        stop.set()
        for task in (sender, receiver, watcher, state.flush_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(sender, receiver, watcher, return_exceptions=True)
        if state.flush_task is not None:
            await asyncio.gather(state.flush_task, return_exceptions=True)
    log.info(
        "session ended (%s); sent=%s dropped=%s tools=%s barge_ins=%s",
        result.reason,
        result.chunks_sent,
        result.chunks_dropped,
        ",".join(result.tool_calls) or "-",
        result.barge_ins,
    )
    return result


class _TurnState:
    def __init__(self, options: SessionOptions):
        self.options = options
        self.started = time.monotonic()
        self.last_idle = time.monotonic()
        self.user_speaking = False
        self.response_active = False
        self.tools_running = False
        self.pending: list[ProviderEvent] = []
        self.seen_calls: set[str] = set()
        self.flush_task: asyncio.Task | None = None
        self.flushing = False
        self.playback_started = 0.0
        self.failed = False
        self.barge_until = 0.0

    def busy(self, player, gate: MicGate) -> bool:
        return (
            self.user_speaking
            or self.response_active
            or self.tools_running
            or bool(self.pending)
            or not player.is_idle
            or not gate.mic_open
        )


def _player_healthy(player) -> bool:
    healthy = getattr(player, "healthy", None)
    if healthy is None:
        return True
    return bool(healthy)


async def _handle_event(event, provider, player, tools, gate, state, result, options) -> None:
    player.poll()
    if event.kind == "audio" and event.audio:
        pcm = np.frombuffer(event.audio, dtype="<i2")
        player.enqueue(pcm, event.item_id, options.api_sample_rate)
        state.playback_started = time.monotonic()
        state.response_active = True
    elif event.kind == "speech_started":
        # Track that the person is talking so a long sentence is not cut off
        # by the silence timer. In half-duplex this does not stop playback:
        # the mic is already muted while the assistant is audible.
        state.user_speaking = True
        if gate.half_duplex:
            log.debug("half-duplex: noted user speech without interrupting playback")
        else:
            await _barge_in(provider, player, state, result, "server vad")
    elif event.kind == "speech_stopped":
        state.user_speaking = False
    elif event.kind == "response_started":
        state.response_active = True
    elif event.kind == "response_done":
        state.response_active = False
        _arm_flush(provider, tools, state, result, delay=0)
    elif event.kind == "function_call":
        _note_function_call(provider, tools, state, result, event)
    elif event.kind == "assistant_transcript" and event.text:
        result.assistant_transcript = (result.assistant_transcript + " " + event.text).strip()
        log.info("assistant: %s", event.text)
    elif event.kind == "user_transcript" and event.text:
        result.user_transcript = (result.user_transcript + " " + event.text).strip()
        log.info("you: %s", event.text)
    elif event.kind == "clear_playback":
        player.clear()
    elif event.kind == "error":
        log.warning("provider error: %s", event.text)
        if event.fatal:
            result.reason = "error"
            raise RuntimeError(event.text or "provider error")
    _touch_idle(state, player, gate)


def _touch_idle(state, player, gate) -> None:
    player.poll()
    if state.busy(player, gate):
        state.last_idle = time.monotonic()


def _note_function_call(provider, tools, state, result, event: ProviderEvent) -> None:
    if not event.call_id or event.call_id in state.seen_calls:
        return
    if not event.name:
        log.warning("function call %s had no name", event.call_id)
        return
    state.seen_calls.add(event.call_id)
    state.pending.append(event)
    _arm_flush(provider, tools, state, result, delay=state.options.tool_flush_sec)


def _arm_flush(provider, tools, state, result, delay: float) -> None:
    if not state.pending and delay > 0:
        return
    if state.flush_task is not None and not state.flush_task.done():
        state.flush_task.cancel()

    async def _later() -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            await _flush(provider, tools, state, result)
        except asyncio.CancelledError:
            return

    state.flush_task = asyncio.create_task(_later(), name="tool-flush")


async def _flush(provider, tools, state, result) -> None:
    if state.flushing:
        return
    state.flushing = True
    try:
        while state.pending:
            calls = state.pending
            state.pending = []
            state.tools_running = True
            async def _run(call: ProviderEvent) -> str:
                log.info("tool %s %s", call.name, call.arguments)
                output = await asyncio.to_thread(tools.call, call.name or "", call.arguments or "{}")
                log.info("tool %s result: %s", call.name, output[:300])
                return output

            outputs = await asyncio.gather(*[_run(call) for call in calls])
            for call, output in zip(calls, outputs):
                result.tool_calls.append(call.name or "")
                await provider.send_tool_result(call.call_id, output)
            await provider.request_response()
    except Exception:
        log.exception("tool call failed")
        state.failed = True
        result.reason = "error"
    finally:
        state.tools_running = False
        state.flushing = False
        if state.pending:
            _arm_flush(provider, tools, state, result, delay=0)


def _should_barge_locally(pcm, player, gate, state, options: SessionOptions) -> bool:
    if gate.half_duplex or not options.local_barge_in:
        return False
    if player.is_idle and not state.response_active:
        return False
    if time.monotonic() - state.playback_started < (options.barge_in_guard_ms / 1000):
        return False
    if time.monotonic() < state.barge_until:
        return False
    return rms_int16(pcm) >= options.barge_in_rms


async def _barge_in(provider, player, state, result, source: str) -> None:
    if state.options.half_duplex:
        return
    if player.is_idle and not state.response_active:
        return
    if time.monotonic() - state.playback_started < (state.options.barge_in_guard_ms / 1000):
        return
    if time.monotonic() < state.barge_until:
        return
    state.barge_until = time.monotonic() + 0.4
    result.barge_ins += 1
    item_id = player.current_item_id()
    played = player.played_ms()
    log.info("barge-in (%s) at %sms", source, played)
    player.clear()
    state.response_active = False
    try:
        await provider.cancel_response()
    except Exception:
        log.debug("response.cancel failed", exc_info=True)
    if item_id:
        try:
            await provider.truncate(item_id, played)
        except Exception:
            log.debug("truncate failed", exc_info=True)
