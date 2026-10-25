"""Wake-word loop, then one realtime conversation, then back to listening.

The provider is constructed only after a wake detection. Until then the
microphone audio is scored on this machine and is not written to a socket.
"""

from __future__ import annotations

import asyncio
import logging
import time

from jarvis.audio.chime import render_chime, render_error_tone
from jarvis.audio.devices import AudioError, DeviceNotFound
from jarvis.audio.gate import MicGate
from jarvis.audio.signal import FrameAssembler, StreamResampler
from jarvis.audio.streams import Microphone, Player
from jarvis.config import AppConfig
from jarvis.duplex import resolve_duplex
from jarvis.providers import build_provider
from jarvis.providers.realtime import MissingAPIKey
from jarvis.session import SessionOptions, run_session
from jarvis.system_power import start_keep_awake
from jarvis.tools.datetime_tool import spoken_now
from jarvis.tools.registry import build_registry
from jarvis.wake.detector import FRAME_SAMPLES, WakeWordDetector

log = logging.getLogger(__name__)


class DeviceLost(RuntimeError):
    pass


async def run_assistant(config: AppConfig, stop: asyncio.Event) -> None:
    start_keep_awake(config.system.keep_awake)
    _warn_if_key_missing(config)
    detector = WakeWordDetector(
        model=config.wake.model,
        model_path=config.wake.model_path,
        threshold=config.wake.threshold,
        vad_threshold=config.wake.vad_threshold,
    )
    backoff = 1.0
    loaded = False
    while not stop.is_set():
        mic = Microphone(
            config.audio.input_device,
            config.audio.wake_sample_rate,
            config.audio.block_ms,
        )
        player = Player(config.audio.output_device)
        try:
            if not loaded:
                await asyncio.to_thread(detector.load)
                loaded = True
                log.info(
                    "wake word ready: %s (threshold %.2f). Audio stays on this Mac until it is heard.",
                    config.wake.model.replace("_", " "),
                    config.wake.threshold,
                )
            mic.open()
            player.open()
            decision = resolve_duplex(
                config.audio.half_duplex,
                config.audio.aec,
                mic.device_name,
                player.device_name,
            )
            log.info(
                "audio input=%r output=%r duplex=%s barge_in=%s tail_ms=%s (%s)",
                decision.input_name,
                decision.output_name,
                decision.label,
                "on" if decision.barge_in else "off",
                config.audio.half_duplex_tail_ms,
                decision.reason,
            )
            gate = MicGate(decision.half_duplex, config.audio.half_duplex_tail_ms)
            player.on_active = gate.begin_assistant_audio
            player.on_idle = gate.end_assistant_audio
            reason = await _listen(config, mic, player, gate, detector, decision.half_duplex, stop)
            backoff = 1.0
            if reason == "stop":
                return
            log.warning("audio device dropped; reopening")
        except DeviceNotFound as exc:
            log.warning("audio device unavailable (%s). Retrying.", exc)
        except AudioError as exc:
            log.warning("audio error: %s", exc)
        except Exception:
            log.exception("assistant loop error")
        finally:
            mic.close()
            player.close()
        if stop.is_set():
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
            return
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 15)


async def _listen(config, mic, player, gate, detector, half_duplex: bool, stop: asyncio.Event) -> str:
    assembler = FrameAssembler(FRAME_SAMPLES)
    to_wake = StreamResampler(mic.rate, config.audio.wake_sample_rate)
    cooldown_until = 0.0
    phrase = config.wake.model.replace("_", " ")
    while not stop.is_set():
        if not mic.healthy or not player.healthy:
            return "device"
        chunk = await mic.read()
        if chunk is None:
            continue
        now = time.monotonic()
        for frame in assembler.push(to_wake.process(chunk)):
            detected, score, label = detector.process(frame)
            if score >= config.wake.threshold * 0.6:
                log.debug("wake score %.2f (%s)", score, label)
            if not detected or now < cooldown_until:
                continue
            log.info("wake word heard: %s (%.2f)", label or phrase, score)
            try:
                await _conversation(config, mic, player, gate, half_duplex)
            except DeviceLost:
                return "device"
            except Exception:
                log.exception("conversation failed")
                await _play(player, render_error_tone(config.audio.api_sample_rate), config.audio.api_sample_rate)
            cooldown_until = time.monotonic() + config.wake.cooldown_sec
            assembler.reset()
            to_wake.reset()
            detector.reset()
            mic.drain()
            break
    return "stop"


async def _conversation(config, mic, player, gate: MicGate, half_duplex: bool) -> None:
    chime = render_chime(config.audio.api_sample_rate)
    gate.begin_chime(chime.size / config.audio.api_sample_rate)
    await _play(player, chime, config.audio.api_sample_rate)
    mic.drain()
    provider = build_provider(config)
    registry = None
    try:
        await provider.connect()
        registry = build_registry(config)
        instructions = _instructions(config)
        await provider.configure(
            instructions=instructions,
            voice=config.provider.voice,
            tools=registry.schemas(),
            sample_rate=config.audio.api_sample_rate,
            barge_in=not half_duplex,
            model=config.provider.model,
        )
        options = SessionOptions(
            half_duplex=half_duplex,
            tail_ms=config.audio.half_duplex_tail_ms,
            silence_timeout_sec=config.session.silence_timeout_sec,
            max_duration_sec=config.session.max_duration_sec,
            local_barge_in=config.session.local_barge_in and not half_duplex,
            barge_in_rms=config.session.barge_in_rms,
            barge_in_guard_ms=config.session.barge_in_guard_ms,
            tool_flush_sec=config.session.tool_flush_sec,
            api_sample_rate=config.audio.api_sample_rate,
            capture_sample_rate=mic.rate,
        )
        result = await run_session(
            provider=provider,
            player=player,
            mic=mic,
            tools=registry,
            gate=gate,
            options=options,
        )
        if result.reason == "device_lost":
            raise DeviceLost(result.reason)
        if result.reason == "error":
            await _play(player, render_error_tone(config.audio.api_sample_rate), config.audio.api_sample_rate)
    except MissingAPIKey as exc:
        log.error("%s", exc)
        await _play(player, render_error_tone(config.audio.api_sample_rate), config.audio.api_sample_rate)
    except DeviceLost:
        raise
    finally:
        if registry is not None:
            registry.close()
        await provider.close()


async def _play(player, pcm, sample_rate: int) -> None:
    try:
        player.enqueue(pcm, "local-tone", sample_rate)
        await player.wait_until_idle()
    except Exception:
        log.debug("could not play a local tone", exc_info=True)


def _instructions(config: AppConfig) -> str:
    try:
        clock = spoken_now(config.home_timezone)
    except Exception:
        log.debug("could not read the local clock", exc_info=True)
        clock = "unknown"
    return f"Current local time: {clock}.\n\n{config.instructions}"


def _warn_if_key_missing(config: AppConfig) -> None:
    import os

    if config.provider.name == "mock":
        log.info("provider is mock; no API key is used and replies are local")
        return
    env_name = config.provider.api_key_env
    if env_name and not os.environ.get(env_name, "").strip():
        log.error(
            "%s is not set. The wake word will still be detected locally, but a conversation cannot start. "
            "Add the key to .env and restart. See the README.",
            env_name,
        )
