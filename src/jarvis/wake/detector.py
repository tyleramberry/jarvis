"""On-device wake word. No audio is uploaded to run this."""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

# openWakeWord scores 16 kHz mono int16 in 80 ms frames.
FRAME_SAMPLES = 1280


class WakeWordDetector:
    def __init__(
        self,
        model: str = "hey_jarvis",
        model_path: str | None = None,
        threshold: float = 0.5,
        vad_threshold: float = 0.5,
    ):
        self.model = model
        self.model_path = model_path
        self.threshold = threshold
        self.vad_threshold = vad_threshold
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import openwakeword
            from openwakeword.model import Model
        except Exception as exc:
            raise RuntimeError(
                "openWakeWord is not installed. From the project directory run: "
                "pip install -e ."
            ) from exc
        models = [self.model_path] if self.model_path else [self.model]
        if not self.model_path:
            log.info("checking on-device wake model %s (first run downloads the model file)", self.model)
            try:
                openwakeword.utils.download_models()
            except Exception:
                log.warning("wake model download failed; loading will be tried anyway", exc_info=True)
        log.info("loading wake model %s with onnxruntime", models[0])
        self._model = Model(
            wakeword_models=models,
            inference_framework="onnx",
            vad_threshold=self.vad_threshold,
        )

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()

    def process(self, frame: np.ndarray) -> tuple[bool, float, str]:
        """Score one 16 kHz frame. Returns (detected, score, label)."""
        if self._model is None:
            raise RuntimeError("wake model is not loaded")
        samples = np.asarray(frame, dtype=np.int16).reshape(-1)
        if samples.size != FRAME_SAMPLES:
            raise ValueError(f"wake frame must be {FRAME_SAMPLES} samples, got {samples.size}")
        scores = self._model.predict(samples)
        if not scores:
            return False, 0.0, self.model
        label, score = max(scores.items(), key=lambda item: item[1])
        score = float(score)
        if score >= self.threshold:
            self._model.reset()
            return True, score, str(label)
        return False, score, str(label)
