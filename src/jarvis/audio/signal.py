"""Small PCM helpers used by the wake loop, the session, and tests."""

from __future__ import annotations

import numpy as np


def rms_int16(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    values = samples.astype(np.float32, copy=False)
    return float(np.sqrt(np.mean(values * values)))


class StreamResampler:
    """Linear resampler that keeps fractional position across chunks."""

    def __init__(self, src_rate: int, dst_rate: int):
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        incoming = np.asarray(chunk).reshape(-1)
        if self.src_rate == self.dst_rate:
            if incoming.dtype == np.int16:
                return incoming.astype("<i2", copy=False)
            return _to_int16(incoming.astype(np.float32))
        if incoming.size:
            data = incoming.astype(np.float32, copy=False)
            self._pending = np.concatenate([self._pending, data]) if self._pending.size else data
        if self._pending.size < 2:
            return np.zeros(0, dtype="<i2")

        step = self.src_rate / self.dst_rate
        max_pos = self._pending.size - 1
        span = max_pos - self._pos
        if span <= 0:
            return np.zeros(0, dtype="<i2")
        count = int(np.floor((span - 1e-9) / step)) + 1
        if count <= 0:
            return np.zeros(0, dtype="<i2")
        positions = self._pos + step * np.arange(count, dtype=np.float64)
        index = np.floor(positions).astype(np.int64)
        frac = (positions - index).astype(np.float32)
        samples = self._pending[index] * (1.0 - frac) + self._pending[index + 1] * frac
        self._pos = float(positions[-1] + step)
        drop = int(np.floor(self._pos))
        if drop > 0:
            drop = min(drop, self._pending.size)
            self._pending = self._pending[drop:]
            self._pos -= drop
        return _to_int16(samples)


class FrameAssembler:
    """Slice a stream into fixed-size frames, carrying the remainder."""

    def __init__(self, frame_samples: int = 1280):
        if frame_samples <= 0:
            raise ValueError("frame_samples must be positive")
        self.frame_samples = frame_samples
        self._buffer = np.zeros(0, dtype=np.int16)

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.int16)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        incoming = np.asarray(samples, dtype=np.int16).reshape(-1)
        if self._buffer.size:
            incoming = np.concatenate([self._buffer, incoming])
        frames: list[np.ndarray] = []
        size = self.frame_samples
        offset = 0
        while offset + size <= incoming.size:
            frames.append(incoming[offset : offset + size])
            offset += size
        self._buffer = incoming[offset:]
        return frames


def _to_int16(samples: np.ndarray) -> np.ndarray:
    rounded = np.rint(samples)
    return np.clip(rounded, -32768, 32767).astype("<i2")
