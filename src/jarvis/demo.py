"""A hardware-free demo of duplex policy and a scripted conversation."""

from __future__ import annotations

import asyncio

import numpy as np

from jarvis.audio.gate import MicGate
from jarvis.audio.memory import MemoryPlayer, QueueMic
from jarvis.config import AppConfig, load_config
from jarvis.duplex import resolve_duplex
from jarvis.providers.mock import ScriptedProvider
from jarvis.session import SessionOptions, run_session
from jarvis.tools.registry import build_registry


async def run_demo_async(config: AppConfig | None = None) -> dict:
    config = config or load_config(None)
    failures: list[str] = []
    examples = [
        ("USB PnP Audio", "Echo Dot-1A2", "auto", "auto", True),
        ("Anker PowerConf S330", "Anker PowerConf S330", "auto", "auto", False),
        ("USB PnP Audio", "Echo Dot-1A2", "off", "auto", False),
        ("Jabra Speak 750", "Mac mini Speakers", "auto", "auto", True),
        ("Jabra Speak 750", "Jabra Speak 750", "auto", "auto", False),
        ("USB Microphone", "USB Microphone", "auto", "auto", True),
    ]
    decisions = []
    for incoming, outgoing, half, aec, expect_half in examples:
        decision = resolve_duplex(half, aec, incoming, outgoing)
        decisions.append(
            {
                "input": incoming,
                "output": outgoing,
                "half_duplex_setting": half,
                "aec_setting": aec,
                "half_duplex": decision.half_duplex,
                "reason": decision.reason,
            }
        )
        if decision.half_duplex != expect_half:
            failures.append(f"{incoming} / {outgoing} resolved to {decision.label}, expected half={expect_half}")

    half = await _scripted_session(config, half_duplex=True, audio_ms=250, barge_guard_ms=0)
    full = await _scripted_session(config, half_duplex=False, audio_ms=4000, barge_guard_ms=0)

    if half["chunks_dropped"] < 1:
        failures.append("half-duplex session did not drop mic audio while the assistant was audible")
    if half["chunks_sent"] < 1:
        failures.append("half-duplex session forwarded no mic audio")
    if half["cleared"] != 0:
        failures.append("half-duplex session interrupted playback")
    if "get_current_datetime" not in half["tool_calls"]:
        failures.append("half-duplex session did not call the clock tool")
    if half["reason"] not in {"silence", "max_duration"}:
        failures.append(f"half-duplex session ended with {half['reason']}")
    if full["cleared"] < 1 or full["cancels"] < 1:
        failures.append("full-duplex session did not barge in when speech started during playback")
    if full["barge_ins"] < 1:
        failures.append("full-duplex session recorded no barge-in")

    return {
        "ok": not failures,
        "failures": failures,
        "decisions": decisions,
        "half": half,
        "full": full,
    }


async def _scripted_session(config: AppConfig, *, half_duplex: bool, audio_ms: int, barge_guard_ms: int) -> dict:
    rate = config.audio.api_sample_rate
    frame = np.full(rate // 50, 400, dtype=np.int16)  # 20 ms, quiet enough to avoid local barge-in
    mic = QueueMic([frame.copy() for _ in range(50)], delay=0.01)
    player = MemoryPlayer()
    gate = MicGate(half_duplex, tail_ms=120)
    provider = ScriptedProvider(audio_ms=audio_ms, sample_rate=rate, trigger_chunks=2)
    tools = build_registry(config)
    options = SessionOptions(
        half_duplex=half_duplex,
        tail_ms=120,
        silence_timeout_sec=0.35,
        max_duration_sec=4,
        local_barge_in=False,
        barge_in_rms=config.session.barge_in_rms,
        barge_in_guard_ms=barge_guard_ms,
        tool_flush_sec=0.02,
        api_sample_rate=rate,
        capture_sample_rate=rate,
    )
    try:
        await provider.connect()
        await provider.configure(sample_rate=rate, barge_in=not half_duplex, tools=tools.schemas())
        result = await asyncio.wait_for(
            run_session(provider=provider, player=player, mic=mic, tools=tools, gate=gate, options=options),
            timeout=6,
        )
    finally:
        tools.close()
        await provider.close()
    return {
        "reason": result.reason,
        "chunks_sent": result.chunks_sent,
        "chunks_dropped": result.chunks_dropped,
        "tool_calls": result.tool_calls,
        "cleared": player.cleared,
        "cancels": provider.cancels,
        "barge_ins": result.barge_ins,
        "tool_results": provider.tool_results,
    }


def format_report(report: dict) -> str:
    lines = ["Jarvis demo (no microphone and no API key)", ""]
    lines.append("Half-duplex decisions")
    for item in report["decisions"]:
        mode = "HALF-DUPLEX" if item["half_duplex"] else "FULL-DUPLEX"
        lines.append(f"  {item['input']}  ->  {item['output']}")
        lines.append(f"    {mode}: {item['reason']}")
    lines.append("")
    lines.append("Scripted half-duplex conversation (USB mic + Bluetooth speaker)")
    lines.extend(_session_lines(report["half"]))
    lines.append("Mic audio was dropped while the reply was still audible, then accepted again after the tail.")
    lines.append("")
    lines.append("Scripted full-duplex barge-in (speakerphone with echo cancellation)")
    lines.extend(_session_lines(report["full"]))
    lines.append("Playback was stopped when speech started over the assistant.")
    lines.append("")
    if report["ok"]:
        lines.append("Demo passed.")
    else:
        lines.append("Demo failed:")
        lines.extend(f"  - {failure}" for failure in report["failures"])
    return "\n".join(lines)


def _session_lines(summary: dict) -> list[str]:
    tool = ", ".join(summary["tool_calls"]) or "(none)"
    return [
        f"  ended because: {summary['reason']}",
        f"  mic chunks forwarded: {summary['chunks_sent']}",
        f"  mic chunks dropped: {summary['chunks_dropped']}",
        f"  tools: {tool}",
        f"  playback clears: {summary['cleared']}",
        f"  barge-ins: {summary['barge_ins']}",
    ]


def run_demo(config: AppConfig | None = None) -> int:
    report = asyncio.run(run_demo_async(config))
    print(format_report(report))
    return 0 if report["ok"] else 1
