"""Decide half-duplex versus full-duplex from config and device names.

Half-duplex mutes the microphone while the assistant is speaking, plus a
short tail, so a mic with no echo cancellation does not hear the speaker.
Full-duplex leaves the mic open and allows barge-in. That only works when
the speakerphone itself cancels echo (the Anker PowerConf and similar).
"""

from __future__ import annotations

from dataclasses import dataclass


# Names that mean the hardware is a speakerphone with its own echo canceller.
# "anker" alone is intentionally absent: Anker also sells plain USB mics.
AEC_HINTS = (
    "powerconf",
    "jabra",
    "poly sync",
    "polycom",
    "speakerphone",
    "yvc-",
    "yvc ",
    "epos",
    "meetup",
    "rally bar",
    "echo cancel",
    "aec",
)


def normalize_device_name(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def looks_like_aec_device(name: str | None) -> bool:
    text = normalize_device_name(name)
    return any(hint in text for hint in AEC_HINTS)


@dataclass(frozen=True)
class DuplexDecision:
    """half_duplex True means mute the mic while the assistant speaks."""

    half_duplex: bool
    reason: str
    input_name: str
    output_name: str

    @property
    def barge_in(self) -> bool:
        return not self.half_duplex

    @property
    def label(self) -> str:
        if self.half_duplex:
            return "half-duplex"
        return "full-duplex"


def resolve_duplex(
    half_duplex: str,
    aec: str,
    input_name: str | None,
    output_name: str | None,
) -> DuplexDecision:
    """Resolve the mic policy.

    ``half_duplex`` and ``aec`` are ``auto``, ``on``, or ``off``.
    When both are ``auto``, half-duplex is used unless a device name
    matches a known echo-cancelling speakerphone. Unknown hardware stays
    half-duplex, which is the safe default for a USB mic plus a Bluetooth
    speaker.
    """
    mode = _choice(half_duplex, "half_duplex")
    aec_mode = _choice(aec, "aec")
    incoming = input_name or ""
    outgoing = output_name or ""

    if mode == "on":
        return DuplexDecision(True, "half_duplex is on in config", incoming, outgoing)
    if mode == "off":
        return DuplexDecision(
            False,
            "half_duplex is off in config; barge-in stays available",
            incoming,
            outgoing,
        )

    if aec_mode == "on":
        return DuplexDecision(
            False,
            "aec is on in config; treating this rig as a speakerphone with echo cancellation",
            incoming,
            outgoing,
        )
    if aec_mode == "off":
        return DuplexDecision(True, "aec is off in config", incoming, outgoing)

    input_aec = looks_like_aec_device(incoming)
    output_aec = looks_like_aec_device(outgoing)
    same_device = bool(incoming) and normalize_device_name(incoming) == normalize_device_name(outgoing)
    # Hardware echo cancellation only covers the speakerphone's own speaker.
    # A PowerConf microphone plus an Echo Dot speaker is still half-duplex.
    if same_device and (input_aec or output_aec):
        return DuplexDecision(
            False,
            f"input and output are the same echo-cancelling speakerphone ({incoming})",
            incoming,
            outgoing,
        )
    if input_aec and output_aec:
        return DuplexDecision(
            False,
            "input and output both look like echo-cancelling speakerphones",
            incoming,
            outgoing,
        )
    if input_aec and not output_aec:
        return DuplexDecision(
            True,
            "the microphone looks like a speakerphone, but playback is a different device, "
            "so its echo cancellation does not cover that speaker",
            incoming,
            outgoing,
        )

    if not incoming and not outgoing:
        return DuplexDecision(
            True,
            "no AEC device detected; half-duplex is the default",
            incoming,
            outgoing,
        )

    if normalize_device_name(incoming) != normalize_device_name(outgoing):
        return DuplexDecision(
            True,
            "input and output are different devices, so hardware echo cancellation cannot cover the speaker",
            incoming,
            outgoing,
        )

    return DuplexDecision(
        True,
        "no AEC device detected; half-duplex is the default",
        incoming,
        outgoing,
    )


def _choice(value: str | bool | None, label: str) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value or "auto").strip().lower()
    if text in {"auto", "on", "off", "true", "false", "yes", "no"}:
        if text in {"true", "yes", "on"}:
            return "on"
        if text in {"false", "no", "off"}:
            return "off"
        return "auto"
    raise ValueError(f"{label} must be auto, on, or off (got {value!r})")
