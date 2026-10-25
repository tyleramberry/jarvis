"""Keep a Mac Mini from idle-sleeping while the assistant is running."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def caffeinate_command(pid: int) -> list[str]:
    """Assertions last until ``pid`` exits. ``-s`` applies only on AC power."""
    return ["caffeinate", "-ims", "-w", str(pid)]


def start_keep_awake(enabled: bool) -> None:
    if not enabled:
        return
    if sys.platform != "darwin":
        log.info("system.keep_awake applies on macOS; this machine will use its own power settings")
        return
    if shutil.which("caffeinate") is None:
        log.warning("caffeinate is not available, so the Mac may still idle-sleep")
        return
    subprocess.Popen(
        caffeinate_command(os.getpid()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("macOS wake assertion held until this process exits (the display may still sleep)")
