"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from jarvis import __version__
from jarvis.config import AppConfig, load_config
from jarvis.logging_setup import setup_logging

log = logging.getLogger("jarvis")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Room voice assistant: local wake word, then a realtime conversation.",
    )
    parser.add_argument("--config", help="Path to config.yaml. Defaults to ./config.yaml.")
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Listen for the wake word and talk to the model.")
    run.add_argument("--mock", action="store_true", help="Use the local mock provider instead of an API.")

    sub.add_parser("demo", help="Run a hardware-free demo of half-duplex and barge-in.")
    sub.add_parser("devices", help="List input and output devices.")
    sub.add_parser("check", help="Check config, the API key, and the chosen devices.")

    wake = sub.add_parser("wake-test", help="Print wake-word scores. No audio is uploaded.")
    wake.add_argument("--seconds", type=float, default=20, help="How long to listen (default 20).")

    sub.add_parser("chime", help="Play the wake chime on the configured output device.")

    tools = sub.add_parser("tools", help="Run the local clock tool, and Calendar if this is a Mac.")
    tools.add_argument("--reminder", help="Also add this reminder (macOS, and only when you pass this flag).")

    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"Could not load config: {exc}", file=sys.stderr)
        return 2
    if args.command == "run" and args.mock:
        config.provider.name = "mock"
    setup_logging(config)
    log.info("jarvis %s command=%s config=%s", __version__, args.command, config.config_path or "(defaults)")

    if args.command == "demo":
        from jarvis.demo import run_demo

        return run_demo(config)
    if args.command == "devices":
        return _devices()
    if args.command == "check":
        return _check(config)
    if args.command == "tools":
        return _tools(config, args.reminder)
    if args.command == "chime":
        return asyncio.run(_chime(config))
    if args.command == "wake-test":
        return asyncio.run(_wake_test(config, args.seconds))
    if args.command == "run":
        return asyncio.run(_run(config))
    parser.print_help()
    return 2


def _devices() -> int:
    from jarvis.audio.devices import AudioError, format_devices, list_devices

    try:
        print(format_devices(list_devices()))
    except AudioError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\nPut a short unique part of the name in config.yaml as input_device or output_device.")
    return 0


def _check(config: AppConfig) -> int:
    from jarvis.audio.devices import AudioError, format_devices, list_devices, resolve_device
    from jarvis.duplex import resolve_duplex

    problems: list[str] = []
    print(f"provider: {config.provider.name} ({config.provider.model}, voice {config.provider.voice})")
    if config.provider.name != "mock":
        key_name = config.provider.api_key_env
        present = bool(os.environ.get(key_name, "").strip())
        print(f"API key {key_name}: {'set' if present else 'MISSING'}")
        if not present:
            problems.append(f"set {key_name} in .env")
    else:
        print("API key: not used in mock mode")
    print(f"wake word: {config.wake.model.replace('_', ' ')} threshold {config.wake.threshold}")
    print(f"tools: {', '.join(config.tools_enabled) or '(none)'}")
    if config.mcp.enabled:
        command = " ".join(config.mcp.command) or "(empty)"
        print(f"mcp: enabled, command: {command}")
    try:
        devices = list_devices()
    except AudioError as exc:
        print(f"audio devices: {exc}")
        problems.append("audio devices are not available")
        devices = []
    else:
        print(format_devices(devices))
        try:
            incoming = resolve_device(config.audio.input_device, "input")
            outgoing = resolve_device(config.audio.output_device, "output")
        except AudioError as exc:
            print(f"selected devices: {exc}")
            problems.append(str(exc))
        else:
            decision = resolve_duplex(
                config.audio.half_duplex,
                config.audio.aec,
                incoming.name,
                outgoing.name,
            )
            print(
                f"selected input: {incoming.name}\n"
                f"selected output: {outgoing.name}\n"
                f"duplex: {decision.label} (barge-in {'on' if decision.barge_in else 'off'})\n"
                f"reason: {decision.reason}\n"
                f"half-duplex tail: {config.audio.half_duplex_tail_ms} ms"
            )
    if problems:
        print("\nFix before expecting a conversation:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nConfig looks usable. Next: python -m jarvis wake-test")
    return 0


def _tools(config: AppConfig, reminder: str | None) -> int:
    from jarvis.tools.registry import build_registry

    registry = build_registry(config)
    try:
        print("Clock")
        print(registry.call("get_current_datetime", {}))
        if "calendar_today" in registry.names():
            print("\nToday's calendar")
            print(registry.call("calendar_today", {}))
        if reminder:
            if "add_reminder" not in registry.names():
                print("add_reminder is not enabled in config.yaml", file=sys.stderr)
                return 1
            print("\nReminder")
            print(registry.call("add_reminder", {"title": reminder}))
        elif "add_reminder" in registry.names():
            print("\nReminders tool is available. Nothing was added.")
            print("To add one: python -m jarvis tools --reminder \"Try the door\"")
    finally:
        registry.close()
    return 0


async def _chime(config: AppConfig) -> int:
    from jarvis.audio.chime import render_chime
    from jarvis.audio.devices import AudioError
    from jarvis.audio.streams import Player

    player = Player(config.audio.output_device)
    try:
        device = player.open()
        tone = render_chime(config.audio.api_sample_rate)
        player.enqueue(tone, "chime", config.audio.api_sample_rate)
        await player.wait_until_idle(timeout=5)
        print(f"Played the chime on {device.name}.")
        return 0
    except AudioError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        player.close()


async def _wake_test(config: AppConfig, seconds: float) -> int:
    from jarvis.audio.devices import AudioError
    from jarvis.audio.signal import FrameAssembler, StreamResampler
    from jarvis.audio.streams import Microphone
    from jarvis.wake.detector import FRAME_SAMPLES, WakeWordDetector

    mic = Microphone(config.audio.input_device, config.audio.wake_sample_rate, config.audio.block_ms)
    detector = WakeWordDetector(
        config.wake.model,
        config.wake.model_path,
        config.wake.threshold,
        config.wake.vad_threshold,
    )
    try:
        device = mic.open()
        print(f"Loading the on-device wake model {config.wake.model!r}…")
        await asyncio.to_thread(detector.load)
        print(
            f"Listening on {device.name} for {seconds:.0f}s. "
            f"Say “{config.wake.model.replace('_', ' ')}”. Nothing is uploaded."
        )
        assembler = FrameAssembler(FRAME_SAMPLES)
        resampler = StreamResampler(mic.rate, config.audio.wake_sample_rate)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        hits = 0
        window = 0.0
        shown = loop.time()
        while loop.time() < deadline:
            chunk = await mic.read()
            if chunk is None:
                continue
            for frame in assembler.push(resampler.process(chunk)):
                detected, score, label = detector.process(frame)
                window = max(window, score)
                if detected:
                    hits += 1
                    print(f"\nDETECTED {label} {score:.2f}")
                    detector.reset()
            if loop.time() - shown >= 0.5:
                print(f"\rscore {window:.2f}   ", end="", flush=True)
                window = 0.0
                shown = loop.time()
        print(f"\nDone. Detections: {hits}")
        return 0
    except AudioError as exc:
        print(exc, file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        mic.close()


async def _run(config: AppConfig) -> int:
    from jarvis.assistant import run_assistant

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: stop.set())
    log.info("listening. Press Ctrl+C to stop.")
    try:
        await run_assistant(config, stop)
    except asyncio.CancelledError:
        return 0
    return 0
