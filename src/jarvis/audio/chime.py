"""Short tones synthesized in memory. No audio files to ship."""

from __future__ import annotations

import numpy as np


def render_chime(sample_rate: int) -> np.ndarray:
    """A two-note chime with a short leading silence.

    The silence gives a Bluetooth speaker a moment to wake before the tone.
    """
    lead = np.zeros(int(sample_rate * 0.12), dtype=np.int16)
    gap = np.zeros(int(sample_rate * 0.03), dtype=np.int16)
    return np.concatenate(
        [
            lead,
            _tone(sample_rate, 880, 0.09),
            gap,
            _tone(sample_rate, 1320, 0.12),
        ]
    )


def render_error_tone(sample_rate: int) -> np.ndarray:
    """A short descending tone played when a conversation cannot start."""
    gap = np.zeros(int(sample_rate * 0.04), dtype=np.int16)
    return np.concatenate(
        [
            _tone(sample_rate, 440, 0.09, amplitude=0.18),
            gap,
            _tone(sample_rate, 330, 0.12, amplitude=0.18),
        ]
    )


def _tone(sample_rate: int, frequency: float, duration: float, amplitude: float = 0.22) -> np.ndarray:
    count = max(1, int(sample_rate * duration))
    time_axis = np.arange(count, dtype=np.float32) / sample_rate
    envelope = np.ones(count, dtype=np.float32)
    fade = max(1, int(sample_rate * 0.012))
    envelope[:fade] = np.linspace(0, 1, fade, dtype=np.float32)
    envelope[-fade:] = np.linspace(1, 0, fade, dtype=np.float32)
    wave = np.sin(2 * np.pi * frequency * time_axis) * envelope * amplitude
    return np.clip(np.rint(wave * 32767), -32768, 32767).astype(np.int16)
