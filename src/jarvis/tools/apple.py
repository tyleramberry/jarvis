"""Calendar and Reminders through AppleScript.

These run only on macOS, and only after the user allows Automation for
the Python program. Arguments are passed as osascript argv so a reminder
title cannot change the script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from jarvis.tools.base import Tool, object_schema

CALENDAR_TODAY_SCRIPT = r'''
set output to ""
set todayStart to current date
set hours of todayStart to 0
set minutes of todayStart to 0
set seconds of todayStart to 0
set todayEnd to todayStart + (1 * days)
tell application "Calendar"
  repeat with cal in calendars
    try
      set evs to (every event of cal whose start date ≥ todayStart and start date < todayEnd)
      repeat with ev in evs
        set output to output & (name of cal) & " | " & (summary of ev) & " | " & ((start date of ev) as string) & " | " & ((end date of ev) as string) & linefeed
      end repeat
    end try
  end repeat
end tell
return output
'''

ADD_REMINDER_SCRIPT = r'''
on run argv
  set reminderName to item 1 of argv
  set listName to item 2 of argv
  set reminderNotes to item 3 of argv
  tell application "Reminders"
    if listName is "" then
      set targetList to default list
    else
      set targetList to list listName
    end if
    tell targetList
      if reminderNotes is "" then
        make new reminder with properties {name:reminderName}
      else
        make new reminder with properties {name:reminderName, body:reminderNotes}
      end if
    end tell
    return "Added reminder to " & (name of targetList)
  end tell
end run
'''


class CalendarTodayTool(Tool):
    name = "calendar_today"
    description = "Read today's events from Apple Calendar. Use this when the person asks what is on the calendar today."
    parameters = object_schema()

    def __init__(self, runner=None):
        self._runner = runner or run_osascript

    def run(self, arguments: dict) -> str:
        try:
            text = self._runner(CALENDAR_TODAY_SCRIPT, [], 30)
        except Exception as exc:
            return json.dumps({"error": str(exc), "hint": AUTOMATION_HINT})
        events = [line.strip() for line in text.splitlines() if line.strip()]
        if not events:
            return json.dumps({"events": [], "summary": "No events on the calendar today."})
        return json.dumps({"events": events, "summary": f"{len(events)} event(s) today."})


class AddReminderTool(Tool):
    name = "add_reminder"
    description = "Add a reminder to Apple Reminders."
    parameters = object_schema(
        {
            "title": {"type": "string", "description": "What to remember."},
            "list_name": {
                "type": "string",
                "description": "Reminders list name. Omit to use the default list.",
            },
            "notes": {"type": "string", "description": "Optional extra detail."},
        },
        required=["title"],
    )

    def __init__(self, runner=None):
        self._runner = runner or run_osascript

    def run(self, arguments: dict) -> str:
        title = str(arguments.get("title") or "").strip()
        if not title:
            return json.dumps({"error": "title is required"})
        list_name = str(arguments.get("list_name") or "").strip()
        notes = str(arguments.get("notes") or "").strip()
        try:
            message = self._runner(ADD_REMINDER_SCRIPT, [title, list_name, notes], 30)
        except Exception as exc:
            return json.dumps({"error": str(exc), "hint": AUTOMATION_HINT})
        return json.dumps({"ok": True, "message": message or "Reminder added.", "title": title})


AUTOMATION_HINT = (
    "On macOS, run `python -m jarvis tools` once from Terminal and allow "
    "Calendar and Reminders under System Settings → Privacy & Security → Automation."
)


def run_osascript(script: str, args: list[str] | None = None, timeout: int = 30) -> str:
    if sys.platform != "darwin":
        raise RuntimeError("Apple Calendar and Reminders tools run on macOS.")
    if shutil.which("osascript") is None:
        raise RuntimeError("osascript is not on PATH.")
    command = ["osascript", "-e", script]
    if args:
        command.append("--")
        command.extend(args)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("osascript timed out. Calendar or Reminders may be waiting for permission.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "osascript failed").strip()
        raise RuntimeError(detail)
    return completed.stdout.strip()
