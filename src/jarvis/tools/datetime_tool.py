"""Local clock. This does not leave the machine."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.tools.base import Tool, object_schema


class DateTimeTool(Tool):
    name = "get_current_datetime"
    description = (
        "Get the current local date and time. Use this when the person asks "
        "what time it is, what day it is, or before talking about today's calendar."
    )
    parameters = object_schema(
        {
            "timezone": {
                "type": "string",
                "description": "Optional IANA timezone such as America/Los_Angeles. Omit for the home timezone.",
            }
        }
    )

    def __init__(self, home_timezone: str = ""):
        self.home_timezone = home_timezone

    def run(self, arguments: dict) -> str:
        requested = str(arguments.get("timezone") or self.home_timezone or "").strip()
        name, now = current_datetime(requested)
        try:
            spoken = now.strftime("%A, %B %-d, %Y, %-I:%M %p %Z")
        except ValueError:
            spoken = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
        return json.dumps({"timezone": name, "iso": now.isoformat(timespec="seconds"), "spoken": spoken})


def current_datetime(timezone_name: str = "") -> tuple[str, datetime]:
    name = timezone_name or system_timezone_name()
    if name in {"", "local"}:
        return "local", datetime.now().astimezone()
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {name}") from exc
    return name, datetime.now(zone)


def system_timezone_name() -> str:
    """Best-effort IANA name from /etc/localtime, which macOS and Linux both use."""
    try:
        resolved = Path("/etc/localtime").resolve()
    except OSError:
        return "local"
    parts = resolved.parts
    if "zoneinfo" in parts:
        index = parts.index("zoneinfo") + 1
        name = "/".join(parts[index:])
        if name:
            return name
    return "local"


def spoken_now(timezone_name: str = "") -> str:
    name, now = current_datetime(timezone_name)
    try:
        spoken = now.strftime("%A, %B %-d, %Y, %-I:%M %p %Z")
    except ValueError:
        spoken = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
    return f"{spoken} ({name})"
