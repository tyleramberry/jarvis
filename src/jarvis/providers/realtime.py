"""WebSocket speech-to-speech client for OpenAI and xAI.

The two services use the same overall shape: session.update, append PCM,
play audio deltas, and return function calls to the client. The profile
selects the URL, the key, and the session payload.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from urllib.parse import quote

from jarvis.config import ProviderConfig
from jarvis.providers.events import ProviderEvent, parse_server_event

log = logging.getLogger(__name__)


class MissingAPIKey(RuntimeError):
    pass


class RealtimeClient:
    def __init__(self, settings: ProviderConfig):
        self.settings = settings
        self._ws = None
        self._queue: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._reader: asyncio.Task | None = None
        self._updates = 0
        self._closed = False

    async def connect(self) -> None:
        key_env = self.settings.api_key_env
        api_key = os.environ.get(key_env, "").strip() if key_env else ""
        if not api_key:
            raise MissingAPIKey(
                f"{key_env or 'API key'} is not set. Add it to .env next to config.yaml. "
                "See the README."
            )
        import websockets

        url = _with_model(self.settings.base_url, self.settings.model)
        headers = {"Authorization": f"Bearer {api_key}"}
        log.info("connecting to %s model %s", self.settings.name, self.settings.model)
        try:
            self._ws = await _connect(websockets, url, headers)
        except Exception as exc:
            message = str(exc)
            if "401" in message or "403" in message:
                raise RuntimeError(
                    f"{self.settings.name} rejected the API key in {key_env}. "
                    "Check the value in .env."
                ) from exc
            raise RuntimeError(f"could not connect to {self.settings.name}: {exc}") from exc
        self._closed = False
        self._reader = asyncio.create_task(self._recv_loop(), name="realtime-recv")

    async def configure(
        self,
        *,
        instructions: str,
        voice: str,
        tools: list[dict],
        sample_rate: int,
        barge_in: bool,
        model: str | None = None,
    ) -> None:
        payload = build_session_update(
            self.settings,
            instructions=instructions,
            voice=voice,
            tools=tools,
            sample_rate=sample_rate,
            barge_in=barge_in,
            model=model or self.settings.model,
        )
        before = self._updates
        await self._send_json(payload)
        for _ in range(50):
            if self._updates > before:
                log.info("session configured (%s, barge_in=%s)", self.settings.name, barge_in)
                return
            await asyncio.sleep(0.1)
        log.warning("session.update was not acknowledged; continuing anyway")

    async def send_audio(self, pcm16: bytes) -> None:
        if not pcm16:
            return
        await self._send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16).decode("ascii"),
            }
        )

    async def cancel_response(self) -> None:
        await self._send_json({"type": "response.cancel"})

    async def truncate(self, item_id: str, audio_end_ms: int) -> None:
        await self._send_json(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": max(0, int(audio_end_ms)),
            }
        )

    async def send_tool_result(self, call_id: str, output: str) -> None:
        await self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def request_response(self) -> None:
        await self._send_json({"type": "response.create"})

    async def events(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                log.debug("websocket close failed", exc_info=True)
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, timeout=2)
            except Exception:
                self._reader.cancel()
        await self._queue.put(None)

    async def _send_json(self, payload: dict) -> None:
        if self._ws is None:
            raise RuntimeError("realtime socket is not connected")
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    await self._queue.put(ProviderEvent(kind="audio", audio=message, raw_type="binary"))
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("ignoring non-JSON realtime message")
                    continue
                for parsed in parse_server_event(event):
                    if parsed.kind == "session_ready":
                        self._updates += 1
                    if parsed.kind == "error":
                        log.warning("realtime error: %s", parsed.text)
                    await self._queue.put(parsed)
        except Exception as exc:
            if not self._closed:
                log.warning("realtime connection ended: %s", exc)
                await self._queue.put(
                    ProviderEvent(kind="error", text=str(exc), raw_type="connection", fatal=True)
                )
        finally:
            await self._queue.put(None)


def build_session_update(
    settings: ProviderConfig,
    *,
    instructions: str,
    voice: str,
    tools: list[dict],
    sample_rate: int,
    barge_in: bool,
    model: str,
) -> dict:
    if settings.name == "grok":
        return _grok_update(settings, instructions, voice, tools, sample_rate)
    return _openai_update(settings, instructions, voice, tools, sample_rate, barge_in, model)


def _openai_update(settings, instructions, voice, tools, sample_rate, barge_in, model) -> dict:
    turn_detection = {
        "type": "server_vad",
        "threshold": settings.vad_threshold,
        "prefix_padding_ms": settings.prefix_padding_ms,
        "silence_duration_ms": settings.silence_duration_ms,
        "create_response": True,
        "interrupt_response": barge_in,
    }
    audio_input: dict = {
        "format": {"type": "audio/pcm", "rate": sample_rate},
        "turn_detection": turn_detection,
    }
    reduction = (settings.noise_reduction or "off").lower()
    if reduction in {"far_field", "near_field"}:
        audio_input["noise_reduction"] = {"type": reduction}
    if settings.transcribe_input:
        audio_input["transcription"] = {"model": settings.transcription_model}
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": audio_input,
                "output": {
                    "format": {"type": "audio/pcm", "rate": sample_rate},
                    "voice": voice,
                },
            },
            "tools": tools,
            "tool_choice": "auto",
        },
    }


def _grok_update(settings, instructions, voice, tools, sample_rate) -> dict:
    # xAI bills server_vad sessions for the whole time the socket is open,
    # including silence. The assistant closes the socket on its own timeout.
    return {
        "type": "session.update",
        "session": {
            "voice": voice,
            "instructions": instructions,
            "turn_detection": {"type": "server_vad"},
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": sample_rate}},
                "output": {"format": {"type": "audio/pcm", "rate": sample_rate}},
            },
            "tools": tools,
        },
    }


def _with_model(base_url: str, model: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}model={quote(model)}"


async def _connect(websockets, url: str, headers: dict):
    kwargs = dict(
        max_size=16 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=20,
        open_timeout=15,
    )
    try:
        return await websockets.connect(url, additional_headers=headers, **kwargs)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, **kwargs)
