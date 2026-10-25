"""Microphone capture and speaker playback with device reopen."""

from __future__ import annotations

import asyncio
import logging
import threading

import numpy as np

from jarvis.audio.devices import AudioError, DeviceInfo, resolve_device
from jarvis.audio.signal import StreamResampler

log = logging.getLogger(__name__)


class Microphone:
    """A one-consumer queue of mono int16 frames from the input device."""

    def __init__(self, spec: str | None, preferred_rate: int, block_ms: int):
        self.spec = spec
        self.preferred_rate = preferred_rate
        self.block_ms = block_ms
        self.rate = preferred_rate
        self.device_name = ""
        self._queue: asyncio.Queue[np.ndarray] | None = None
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._failed = False
        self._status_errors = 0

    @property
    def failed(self) -> bool:
        return self._failed or not self.healthy

    @property
    def healthy(self) -> bool:
        if self._failed or self._stream is None:
            return False
        try:
            return bool(self._stream.active)
        except Exception:
            return False

    def open(self) -> DeviceInfo:
        self.close()
        device = resolve_device(self.spec, "input")
        self.device_name = device.name
        self._failed = False
        self._status_errors = 0
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=48)
        self._stream, self.rate = _open_input(
            device,
            self.preferred_rate,
            self.block_ms,
            self._callback,
        )
        log.info("input open: %s @ %s Hz", device.name, self.rate)
        return device

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("input close failed", exc_info=True)

    def drain(self) -> None:
        queue = self._queue
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def read(self) -> np.ndarray | None:
        queue = self._queue
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            return None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            text = str(status)
            if "overflow" not in text.lower():
                self._status_errors += 1
                if self._status_errors == 1 or self._status_errors % 25 == 0:
                    log.warning("input status: %s", text)
                if self._status_errors >= 40:
                    self._failed = True
            else:
                log.debug("input overflow")
        else:
            self._status_errors = 0
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or not loop.is_running():
            return
        chunk = np.array(indata[:, 0], dtype=np.int16, copy=True)
        loop.call_soon_threadsafe(self._offer, chunk)

    def _offer(self, chunk: np.ndarray) -> None:
        queue = self._queue
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(chunk)
        except asyncio.QueueFull:
            pass


class Player:
    """Callback playback. PCM handed to ``enqueue`` is at the API sample rate."""

    def __init__(self, spec: str | None):
        self.spec = spec
        self.device_name = ""
        self.device_rate = 24000
        self.on_active = None
        self.on_idle = None
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()
        self._playing = False
        self._failed = False
        self._played_samples = 0
        self._item_id: str | None = None
        self._resampler = StreamResampler(24000, 24000)
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def healthy(self) -> bool:
        if self._failed or self._stream is None:
            return False
        try:
            return bool(self._stream.active)
        except Exception:
            return False

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return not self._playing

    def open(self) -> DeviceInfo:
        self.close()
        device = resolve_device(self.spec, "output")
        self.device_name = device.name
        self.device_rate = device.default_samplerate or 48000
        self._failed = False
        self._resampler = StreamResampler(24000, self.device_rate)
        self._loop = asyncio.get_running_loop()
        self._idle = asyncio.Event()
        self._idle.set()
        sd = _sounddevice()
        block = max(1, int(self.device_rate * 0.03))
        try:
            stream = sd.OutputStream(
                device=device.index,
                channels=1,
                samplerate=self.device_rate,
                dtype="int16",
                blocksize=block,
                latency="low",
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            raise AudioError(f"could not open output {device.name!r}: {exc}") from exc
        self._stream = stream
        log.info("output open: %s @ %s Hz", device.name, self.device_rate)
        return device

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        with self._lock:
            self._buffer = np.zeros(0, dtype=np.int16)
            self._playing = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("output close failed", exc_info=True)

    def enqueue(self, pcm: np.ndarray, item_id: str | None, api_rate: int) -> None:
        samples = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if samples.size == 0:
            return
        if self._resampler.src_rate != api_rate or self._resampler.dst_rate != self.device_rate:
            self._resampler = StreamResampler(api_rate, self.device_rate)
        converted = self._resampler.process(samples)
        if converted.size == 0:
            return
        notify = False
        with self._lock:
            if item_id != self._item_id:
                self._item_id = item_id
                self._played_samples = 0
            self._buffer = np.concatenate([self._buffer, converted]) if self._buffer.size else converted
            if not self._playing:
                self._playing = True
                self._idle.clear()
                notify = True
        if notify and self.on_active:
            self.on_active()

    def clear(self) -> None:
        notify = False
        with self._lock:
            self._buffer = np.zeros(0, dtype=np.int16)
            self._resampler.reset()
            if self._playing:
                self._playing = False
                notify = True
            self._idle.set()
        if notify and self.on_idle:
            self.on_idle()

    def poll(self) -> None:
        return None

    def played_ms(self) -> int:
        with self._lock:
            if self.device_rate <= 0:
                return 0
            return int(self._played_samples / self.device_rate * 1000)

    def current_item_id(self) -> str | None:
        with self._lock:
            return self._item_id

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        if self.is_idle:
            return
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except asyncio.TimeoutError:
            log.warning("playback did not finish within %.1fs", timeout)

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            text = str(status)
            if "underflow" not in text.lower():
                log.warning("output status: %s", text)
        notify = False
        with self._lock:
            count = min(frames, self._buffer.size)
            if count:
                outdata[:count, 0] = self._buffer[:count]
                self._buffer = self._buffer[count:]
                self._played_samples += count
            if count < frames:
                outdata[count:, 0] = 0
            if self._playing and self._buffer.size == 0:
                self._playing = False
                notify = True
        if notify:
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._notify_idle)

    def _notify_idle(self) -> None:
        self._idle.set()
        if self.on_idle:
            self.on_idle()


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover
        raise AudioError(
            "sounddevice could not be imported. On macOS, `brew install portaudio` "
            "and reinstall this package."
        ) from exc
    return sd


def _open_input(device: DeviceInfo, preferred_rate: int, block_ms: int, callback):
    sd = _sounddevice()
    rates = [preferred_rate]
    if device.default_samplerate and device.default_samplerate not in rates:
        rates.append(device.default_samplerate)
    errors = []
    for rate in rates:
        block = max(1, int(rate * block_ms / 1000))
        try:
            stream = sd.InputStream(
                device=device.index,
                channels=1,
                samplerate=rate,
                dtype="int16",
                blocksize=block,
                latency="low",
                callback=callback,
            )
            stream.start()
            return stream, rate
        except Exception as exc:
            errors.append(f"{rate} Hz: {exc}")
    raise AudioError(f"could not open input {device.name!r}: {'; '.join(errors)}")
