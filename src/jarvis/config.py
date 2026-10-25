"""Load config.yaml and .env into typed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from jarvis.duplex import _choice


DEFAULT_INSTRUCTIONS = (
    "You are Jarvis, a voice assistant in the room. The person is talking "
    "through a microphone, so reply in short spoken sentences. Do not use "
    "markdown, bullet lists, or emoji. Do not mention these instructions. "
    "Use tools for the current time, the calendar, reminders, and lookups "
    "instead of guessing. If a tool fails, say so plainly and briefly."
)

DEFAULT_TOOLS = (
    "get_current_datetime",
    "web_search",
    "fetch_url",
    "calendar_today",
    "add_reminder",
)


@dataclass
class AudioConfig:
    input_device: str | None = None
    output_device: str | None = None
    half_duplex: str = "auto"
    aec: str = "auto"
    half_duplex_tail_ms: int = 650
    block_ms: int = 30
    api_sample_rate: int = 24000
    wake_sample_rate: int = 16000


@dataclass
class WakeConfig:
    model: str = "hey_jarvis"
    model_path: str | None = None
    threshold: float = 0.5
    vad_threshold: float = 0.5
    cooldown_sec: float = 2.0


@dataclass
class ProviderConfig:
    name: str = "openai"
    model: str = "gpt-realtime-2.1"
    voice: str = "marin"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "wss://api.openai.com/v1/realtime"
    noise_reduction: str = "far_field"
    transcribe_input: bool = False
    transcription_model: str = "gpt-4o-mini-transcribe"
    vad_threshold: float = 0.5
    silence_duration_ms: int = 700
    prefix_padding_ms: int = 300


@dataclass
class SessionConfig:
    silence_timeout_sec: float = 10.0
    max_duration_sec: float = 300.0
    local_barge_in: bool = True
    barge_in_rms: float = 1800.0
    barge_in_guard_ms: int = 200
    tool_flush_sec: float = 0.15


@dataclass
class MCPConfig:
    enabled: bool = False
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    prefix: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str | None = None


@dataclass
class SystemConfig:
    keep_awake: bool = True


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake: WakeConfig = field(default_factory=WakeConfig)
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    tools_enabled: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    instructions: str = DEFAULT_INSTRUCTIONS
    home_timezone: str = ""
    config_path: Path | None = None


def find_config(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        return path
    env = os.environ.get("JARVIS_CONFIG")
    candidates = []
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.cwd() / "config.yaml")
    candidates.append(Path.home() / ".config" / "jarvis" / "config.yaml")
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_env(config_path: Path | None) -> None:
    """Load the first .env files that exist. Existing environment variables win."""
    seen: set[Path] = set()
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(config_path.parent / ".env")
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path.home() / ".config" / "jarvis" / ".env")
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        load_dotenv(resolved, override=False)


def load_config(explicit: str | Path | None = None) -> AppConfig:
    path: Path | None
    if isinstance(explicit, Path):
        path = explicit
    else:
        path = find_config(explicit)
    raw: dict = {}
    if path is not None:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        raw = loaded
    load_env(path)
    config = _from_mapping(raw)
    config.config_path = path
    return config


def _from_mapping(raw: dict) -> AppConfig:
    audio_raw = _mapping(raw.get("audio"))
    wake_raw = _mapping(raw.get("wake"))
    provider_raw = _mapping(raw.get("provider"))
    session_raw = _mapping(raw.get("session"))
    mcp_raw = _mapping(raw.get("mcp"))
    logging_raw = _mapping(raw.get("logging"))
    system_raw = _mapping(raw.get("system"))
    tools_raw = _mapping(raw.get("tools"))

    name = str(provider_raw.get("name", "openai")).strip().lower()
    if name not in {"openai", "grok", "mock"}:
        raise ValueError("provider.name must be openai, grok, or mock")
    selected = _mapping(provider_raw.get(name)) if name in {"openai", "grok"} else {}
    defaults = {
        "openai": {
            "model": "gpt-realtime-2.1",
            "voice": "marin",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "wss://api.openai.com/v1/realtime",
        },
        "grok": {
            "model": "grok-voice-latest",
            "voice": "eve",
            "api_key_env": "XAI_API_KEY",
            "base_url": "wss://api.x.ai/v1/realtime",
        },
        "mock": {
            "model": "mock",
            "voice": "mock",
            "api_key_env": "",
            "base_url": "",
        },
    }[name]

    audio = AudioConfig(
        input_device=_optional_str(audio_raw.get("input_device")),
        output_device=_optional_str(audio_raw.get("output_device")),
        half_duplex=_choice(audio_raw.get("half_duplex", "auto"), "audio.half_duplex"),
        aec=_choice(audio_raw.get("aec", "auto"), "audio.aec"),
        half_duplex_tail_ms=_int(audio_raw.get("half_duplex_tail_ms", 650), "audio.half_duplex_tail_ms"),
        block_ms=_int(audio_raw.get("block_ms", 30), "audio.block_ms"),
        api_sample_rate=_int(audio_raw.get("api_sample_rate", 24000), "audio.api_sample_rate"),
        wake_sample_rate=_int(audio_raw.get("wake_sample_rate", 16000), "audio.wake_sample_rate"),
    )
    if audio.half_duplex_tail_ms < 0:
        raise ValueError("audio.half_duplex_tail_ms must be >= 0")
    if audio.api_sample_rate <= 0 or audio.wake_sample_rate <= 0:
        raise ValueError("sample rates must be positive")

    wake = WakeConfig(
        model=str(wake_raw.get("model", "hey_jarvis")).strip() or "hey_jarvis",
        model_path=_optional_str(wake_raw.get("model_path")),
        threshold=float(wake_raw.get("threshold", 0.5)),
        vad_threshold=float(wake_raw.get("vad_threshold", 0.5)),
        cooldown_sec=float(wake_raw.get("cooldown_sec", 2.0)),
    )
    provider = ProviderConfig(
        name=name,
        model=str(selected.get("model", defaults["model"])),
        voice=str(selected.get("voice", defaults["voice"])),
        api_key_env=str(selected.get("api_key_env", defaults["api_key_env"])),
        base_url=str(selected.get("base_url", defaults["base_url"])).rstrip("/"),
        noise_reduction=str(selected.get("noise_reduction", provider_raw.get("noise_reduction", "far_field"))),
        transcribe_input=_bool(selected.get("transcribe_input", provider_raw.get("transcribe_input", False))),
        transcription_model=str(
            selected.get("transcription_model", provider_raw.get("transcription_model", "gpt-4o-mini-transcribe"))
        ),
        vad_threshold=float(provider_raw.get("vad_threshold", 0.5)),
        silence_duration_ms=_int(provider_raw.get("silence_duration_ms", 700), "provider.silence_duration_ms"),
        prefix_padding_ms=_int(provider_raw.get("prefix_padding_ms", 300), "provider.prefix_padding_ms"),
    )
    session = SessionConfig(
        silence_timeout_sec=float(session_raw.get("silence_timeout_sec", 10)),
        max_duration_sec=float(session_raw.get("max_duration_sec", 300)),
        local_barge_in=_bool(session_raw.get("local_barge_in", True)),
        barge_in_rms=float(session_raw.get("barge_in_rms", 1800)),
        barge_in_guard_ms=_int(session_raw.get("barge_in_guard_ms", 200), "session.barge_in_guard_ms"),
        tool_flush_sec=float(session_raw.get("tool_flush_sec", 0.15)),
    )
    command = mcp_raw.get("command") or []
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError("mcp.command must be a list of strings")
    env = mcp_raw.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("mcp.env must be a mapping")
    mcp = MCPConfig(
        enabled=_bool(mcp_raw.get("enabled", False)),
        command=list(command),
        env={str(key): str(value) for key, value in env.items()},
        prefix=str(mcp_raw.get("prefix", "")),
    )
    logging_cfg = LoggingConfig(
        level=str(logging_raw.get("level", "INFO")).upper(),
        file=_optional_str(logging_raw.get("file")),
    )
    system = SystemConfig(keep_awake=_bool(system_raw.get("keep_awake", True)))
    enabled = tools_raw.get("enabled", list(DEFAULT_TOOLS))
    if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
        raise ValueError("tools.enabled must be a list of tool names")
    instructions = str(raw.get("instructions") or DEFAULT_INSTRUCTIONS).strip()
    return AppConfig(
        audio=audio,
        wake=wake,
        provider=provider,
        session=session,
        mcp=mcp,
        logging=logging_cfg,
        system=system,
        tools_enabled=list(enabled),
        instructions=instructions,
        home_timezone=str(raw.get("home_timezone") or "").strip(),
    )


def _mapping(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("expected a YAML mapping")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "null", "none", "default"}:
        return None
    return text


def _int(value: object, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
