"""List and resolve Core Audio / PortAudio devices.

sounddevice is imported lazily so ``jarvis demo`` works on a machine
without a microphone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """PortAudio is missing or a stream could not be opened."""


class DeviceNotFound(AudioError):
    """The configured device is unplugged or not paired right now."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    input_channels: int
    output_channels: int
    default_samplerate: int
    is_default_input: bool
    is_default_output: bool


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depends on the host
        raise AudioError(
            "sounddevice could not be imported. On macOS, install PortAudio "
            "with `brew install portaudio` and reinstall the Python package."
        ) from exc
    return sd


def list_devices() -> list[DeviceInfo]:
    sd = _sounddevice()
    try:
        devices = sd.query_devices()
        default = sd.default.device
    except Exception as exc:
        raise AudioError(f"could not query audio devices: {exc}") from exc
    default_in = default[0] if isinstance(default, (list, tuple)) else default
    default_out = default[1] if isinstance(default, (list, tuple)) and len(default) > 1 else default
    found: list[DeviceInfo] = []
    for index, device in enumerate(devices):
        found.append(
            DeviceInfo(
                index=index,
                name=str(device["name"]),
                input_channels=int(device["max_input_channels"]),
                output_channels=int(device["max_output_channels"]),
                default_samplerate=int(device["default_samplerate"]),
                is_default_input=index == default_in,
                is_default_output=index == default_out,
            )
        )
    return found


def format_devices(devices: list[DeviceInfo]) -> str:
    lines = ["INDEX  IN  OUT  RATE   DEFAULT  NAME"]
    for device in devices:
        marks = []
        if device.is_default_input:
            marks.append("input")
        if device.is_default_output:
            marks.append("output")
        mark = ",".join(marks)
        lines.append(
            f"{device.index:<6} {device.input_channels:<3} {device.output_channels:<4} "
            f"{device.default_samplerate:<6} {mark:<8} {device.name}"
        )
    if len(lines) == 1:
        lines.append("(no audio devices found)")
    return "\n".join(lines)


def resolve_device(spec: str | None, kind: str) -> DeviceInfo:
    """Find an input or output device.

    ``spec`` is a case-insensitive substring of the device name, or a
    numeric index. ``None`` selects the current system default.
    """
    if kind not in {"input", "output"}:
        raise ValueError("kind must be input or output")
    devices = list_devices()
    if spec is None:
        chosen = next((device for device in devices if getattr(device, f"is_default_{kind}")), None)
        if chosen is None:
            chosen = next(
                (
                    device
                    for device in devices
                    if (device.input_channels if kind == "input" else device.output_channels) > 0
                ),
                None,
            )
        if chosen is None:
            raise DeviceNotFound(f"no {kind} device is available")
        return chosen

    text = spec.strip()
    if text.isdigit():
        index = int(text)
        matches = [device for device in devices if device.index == index]
        if not matches:
            raise DeviceNotFound(f"no audio device with index {index}")
        device = matches[0]
        channels = device.input_channels if kind == "input" else device.output_channels
        if channels <= 0:
            raise DeviceNotFound(f"device {device.name!r} has no {kind} channels")
        return device

    needle = text.lower()
    matches = []
    for device in devices:
        channels = device.input_channels if kind == "input" else device.output_channels
        if channels > 0 and needle in device.name.lower():
            matches.append(device)
    if not matches:
        raise DeviceNotFound(f"no {kind} device matching {spec!r}")
    if len(matches) > 1:
        names = ", ".join(device.name for device in matches)
        log.info("several %s devices match %r (%s); using %s", kind, spec, names, matches[0].name)
    return matches[0]
