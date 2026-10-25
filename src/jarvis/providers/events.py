"""Normalize realtime server events from OpenAI and xAI."""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass
class ProviderEvent:
    kind: str
    audio: bytes | None = None
    item_id: str | None = None
    text: str | None = None
    name: str | None = None
    call_id: str | None = None
    arguments: str | None = None
    raw_type: str = ""
    fatal: bool = False


def parse_server_event(event: dict) -> list[ProviderEvent]:
    """Turn one JSON event into zero or more normalized events.

    Both providers speak a similar event language. A few legacy names are
    accepted so a provider update does not drop audio on the floor.
    """
    event_type = str(event.get("type") or "")
    if event_type in {"response.output_audio.delta", "response.audio.delta"}:
        payload = event.get("delta") or ""
        audio = base64.b64decode(payload) if payload else b""
        return [
            ProviderEvent(
                kind="audio",
                audio=audio,
                item_id=event.get("item_id"),
                raw_type=event_type,
            )
        ]
    if event_type in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
        text = event.get("transcript") or event.get("text") or ""
        return [ProviderEvent(kind="assistant_transcript", text=str(text), raw_type=event_type)]
    if event_type == "conversation.item.input_audio_transcription.completed":
        return [
            ProviderEvent(
                kind="user_transcript",
                text=str(event.get("transcript") or ""),
                raw_type=event_type,
            )
        ]
    if event_type == "input_audio_buffer.speech_started":
        return [ProviderEvent(kind="speech_started", item_id=event.get("item_id"), raw_type=event_type)]
    if event_type == "input_audio_buffer.speech_stopped":
        return [ProviderEvent(kind="speech_stopped", raw_type=event_type)]
    if event_type == "response.created":
        return [ProviderEvent(kind="response_started", raw_type=event_type)]
    if event_type in {"response.done", "response.cancelled"}:
        parsed = [ProviderEvent(kind="response_done", raw_type=event_type)]
        response = event.get("response") or {}
        for item in response.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "function_call":
                parsed.append(_function_event(item, event_type))
        return parsed
    if event_type == "response.output_item.done":
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            return [_function_event(item, event_type)]
        return []
    if event_type == "response.function_call_arguments.done":
        return [
            ProviderEvent(
                kind="function_call",
                name=event.get("name"),
                call_id=event.get("call_id"),
                arguments=_arguments(event.get("arguments")),
                raw_type=event_type,
            )
        ]
    if event_type in {"session.created", "session.updated"}:
        return [ProviderEvent(kind="session_ready", raw_type=event_type)]
    if event_type in {"output_audio_buffer.cleared", "output_audio_buffer.stopped"}:
        return [ProviderEvent(kind="clear_playback", raw_type=event_type)]
    if event_type == "error" or event_type.endswith(".error"):
        detail = event.get("error") or event
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("code") or event_type)
            code = str(detail.get("code") or "")
        else:
            message = str(detail)
            code = ""
        fatal = is_fatal_error(f"{code} {message}")
        return [ProviderEvent(kind="error", text=message, raw_type=event_type, fatal=fatal)]
    return []


def is_fatal_error(message: str) -> bool:
    text = message.lower()
    if "cancel" in text and ("no active" in text or "not active" in text):
        return False
    if "response_cancel_not_active" in text:
        return False
    return True


def _function_event(item: dict, raw_type: str) -> ProviderEvent:
    return ProviderEvent(
        kind="function_call",
        name=item.get("name"),
        call_id=item.get("call_id"),
        arguments=_arguments(item.get("arguments")),
        raw_type=raw_type,
    )


def _arguments(value: object) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value or "{}"
    import json

    return json.dumps(value)
