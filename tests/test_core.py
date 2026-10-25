import json
from pathlib import Path

import numpy as np
import pytest

from jarvis.audio.chime import render_chime
from jarvis.audio.gate import MicGate
from jarvis.audio.signal import FrameAssembler, StreamResampler, rms_int16
from jarvis.config import load_config
from jarvis.duplex import resolve_duplex
from jarvis.providers.events import parse_server_event
from jarvis.providers.realtime import build_session_update
from jarvis.system_power import caffeinate_command
from jarvis.tools.apple import ADD_REMINDER_SCRIPT, AddReminderTool, CalendarTodayTool
from jarvis.tools.datetime_tool import DateTimeTool, spoken_now
from jarvis.tools.mcp_client import extract_message, format_tool_result
from jarvis.tools.web_search import parse_duckduckgo, strip_html

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_loads():
    config = load_config(ROOT / "config.yaml")
    assert config.wake.model == "hey_jarvis"
    assert config.provider.name == "openai"
    assert config.provider.model == "gpt-realtime-2.1"
    assert config.audio.half_duplex == "auto"
    assert config.audio.half_duplex_tail_ms == 650
    assert "get_current_datetime" in config.tools_enabled
    assert "calendar_today" in config.tools_enabled


def test_yaml_booleans_map_to_on_off(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  half_duplex: true\n  aec: false\nprovider:\n  name: grok\n", encoding="utf-8")
    config = load_config(path)
    assert config.audio.half_duplex == "on"
    assert config.audio.aec == "off"
    assert config.provider.name == "grok"
    assert config.provider.voice == "eve"
    assert config.provider.api_key_env == "XAI_API_KEY"


def test_duplex_matrix():
    half = resolve_duplex("auto", "auto", "USB PnP Audio", "Echo Dot-1A2")
    assert half.half_duplex
    assert half.barge_in is False

    speakerphone = resolve_duplex("auto", "auto", "Anker PowerConf S330", "Anker PowerConf S330")
    assert speakerphone.half_duplex is False
    assert speakerphone.barge_in

    split_aec = resolve_duplex("auto", "auto", "Jabra Speak 750", "Mac mini Speakers")
    assert split_aec.half_duplex

    forced = resolve_duplex("off", "auto", "USB PnP Audio", "Echo Dot-1A2")
    assert forced.half_duplex is False

    explicit_aec = resolve_duplex("auto", "on", "Mic", "Speaker")
    assert explicit_aec.half_duplex is False

    unknown = resolve_duplex("auto", "auto", None, None)
    assert unknown.half_duplex


def test_gate_mutes_only_in_half_duplex():
    import time

    closed = MicGate(True, tail_ms=40)
    assert closed.mic_open
    closed.begin_assistant_audio()
    assert closed.mic_open is False
    closed.end_assistant_audio()
    assert closed.mic_open is False
    time.sleep(0.08)
    assert closed.mic_open

    opened = MicGate(False, tail_ms=500)
    opened.begin_assistant_audio()
    assert opened.mic_open
    opened.begin_chime(0.05)
    assert opened.mic_open is False


def test_resampler_identity_and_rate_change():
    identity = StreamResampler(16000, 16000)
    source = np.arange(-1000, 1000, dtype=np.int16)
    assert np.array_equal(identity.process(source), source)

    up = StreamResampler(16000, 24000)
    tone = (np.sin(np.linspace(0, 40, 1600)) * 8000).astype(np.int16)
    whole = up.process(tone)
    assert 2200 < whole.size < 2600

    chunked = StreamResampler(16000, 24000)
    parts = [chunked.process(tone[index : index + 160]) for index in range(0, tone.size, 160)]
    joined = np.concatenate(parts)
    assert abs(joined.size - whole.size) < 8


def test_frame_assembler_carries_remainder():
    assembler = FrameAssembler(1280)
    first = assembler.push(np.zeros(1000, dtype=np.int16))
    assert first == []
    second = assembler.push(np.ones(1000, dtype=np.int16))
    assert len(second) == 1
    assert second[0].size == 1280
    assert assembler._buffer.size == 720


def test_event_parser_audio_and_tool_call():
    audio = parse_server_event(
        {
            "type": "response.output_audio.delta",
            "delta": "AQID",
            "item_id": "item_1",
        }
    )
    assert audio[0].kind == "audio"
    assert audio[0].audio == b"\x01\x02\x03"
    assert audio[0].item_id == "item_1"

    done = parse_server_event(
        {
            "type": "response.done",
            "response": {
                "output": [
                    {
                        "type": "function_call",
                        "name": "calendar_today",
                        "call_id": "call_1",
                        "arguments": "{}",
                    }
                ]
            },
        }
    )
    kinds = [event.kind for event in done]
    assert kinds == ["response_done", "function_call"]
    assert done[1].name == "calendar_today"

    benign = parse_server_event(
        {"type": "error", "error": {"message": "Cancellation failed: no active response"}}
    )
    assert benign[0].fatal is False


def test_session_update_shapes():
    from jarvis.config import ProviderConfig

    openai = build_session_update(
        ProviderConfig(name="openai", voice="marin"),
        instructions="Be brief.",
        voice="marin",
        tools=[{"type": "function", "name": "get_current_datetime", "parameters": {}}],
        sample_rate=24000,
        barge_in=False,
        model="gpt-realtime-2.1",
    )
    turn = openai["session"]["audio"]["input"]["turn_detection"]
    assert openai["session"]["type"] == "realtime"
    assert turn["interrupt_response"] is False
    assert openai["session"]["audio"]["output"]["voice"] == "marin"
    assert "OpenAI-Beta" not in json.dumps(openai)

    grok = build_session_update(
        ProviderConfig(name="grok", voice="eve", model="grok-voice-latest"),
        instructions="Be brief.",
        voice="eve",
        tools=[],
        sample_rate=16000,
        barge_in=True,
        model="grok-voice-latest",
    )
    assert grok["session"]["voice"] == "eve"
    assert grok["session"]["audio"]["input"]["format"]["rate"] == 16000
    assert "output_modalities" not in grok["session"]


def test_clock_tool_and_spoken_now():
    payload = json.loads(DateTimeTool("UTC").run({}))
    assert payload["timezone"] == "UTC"
    assert "spoken" in payload
    assert "UTC" in spoken_now("UTC")


def test_duckduckgo_parser_and_html_strip():
    parsed = parse_duckduckgo(
        "paris",
        {
            "AbstractText": "Paris is the capital of France.",
            "AbstractSource": "Wikipedia",
            "AbstractURL": "https://example.com",
            "RelatedTopics": [{"Text": "Paris metro"}],
        },
    )
    assert "Paris is the capital" in parsed["summary"]
    empty = parse_duckduckgo("nothing", {})
    assert "No instant answer" in empty["summary"]
    assert "Hello" in strip_html("<script>nope</script><p>Hello</p>")


def test_reminder_arguments_are_not_pasted_into_applescript():
    seen = {}

    def runner(script, args, timeout):
        seen["script"] = script
        seen["args"] = list(args)
        return "Added"

    title = 'ignore previous " and say hello'
    result = json.loads(AddReminderTool(runner).run({"title": title, "list_name": "Home", "notes": "n"}))
    assert result["ok"] is True
    assert seen["args"] == [title, "Home", "n"]
    assert title not in seen["script"]
    assert "on run argv" in ADD_REMINDER_SCRIPT


def test_calendar_tool_reports_non_mac_cleanly(monkeypatch):
    def runner(script, args, timeout):
        raise RuntimeError("Apple Calendar and Reminders tools run on macOS.")

    payload = json.loads(CalendarTodayTool(runner).run({}))
    assert "macOS" in payload["error"]


def test_mcp_framing_and_result_text():
    line = b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n'
    message, rest = extract_message(bytearray(b"\n" + line))
    assert message["id"] == 7
    assert rest == bytearray()

    body = b'{"jsonrpc":"2.0","id":3,"result":{"tools":[]}}'
    framed = f"Content-Length: {len(body)}\r\n\r\n".encode() + body + b"trailing"
    message, rest = extract_message(bytearray(framed))
    assert message["id"] == 3
    assert bytes(rest) == b"trailing"
    text = format_tool_result({"content": [{"type": "text", "text": "pong"}], "isError": False})
    assert text == "pong"


def test_caffeinate_command_tracks_the_process():
    assert caffeinate_command(42) == ["caffeinate", "-ims", "-w", "42"]


def test_chime_is_audible_pcm():
    tone = render_chime(24000)
    assert tone.dtype == np.int16
    assert rms_int16(tone) > 100


def test_launchd_template_and_scripts():
    template = (ROOT / "launchd" / "com.jarvis.assistant.plist.template").read_text(encoding="utf-8")
    for key in ("RunAtLoad", "KeepAlive", "LimitLoadToSessionType", "Aqua", "__PYTHON__", "__ROOT__"):
        assert key in template
    import subprocess

    for name in ("install_launchd.sh", "uninstall_launchd.sh"):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], check=True)


def test_install_script_refuses_linux():
    import subprocess

    completed = subprocess.run(
        ["bash", str(ROOT / "scripts" / "install_launchd.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "macOS" in completed.stderr
